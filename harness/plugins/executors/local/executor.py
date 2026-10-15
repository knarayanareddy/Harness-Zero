"""
Local process executor implementation conforming to Executor and Process contracts (§4, PLG-02).
"""
from __future__ import annotations

import asyncio
import logging
import os
import signal
import time
import uuid
from collections.abc import AsyncIterator
from pathlib import Path
from typing import Literal

from harness.sdk.types import ExecRequest, ExecResult

logger = logging.getLogger(__name__)

SAFE_ENV_KEYS = frozenset({
    "PATH",
    "HOME",
    "LANG",
    "LC_ALL",
    "TERM",
    "USER",
    "SHELL",
    "TMPDIR",
})


class LocalProcess:
    """
    Handle for an active local OS process with streaming output, stdin piping,
    graceful process group termination, and exit code capture.
    """

    def __init__(
        self,
        process_id: str,
        proc: asyncio.subprocess.Process,
        start_time: float,
        max_buffer_bytes: int = 10 * 1024 * 1024,
    ) -> None:
        self.id = process_id
        self._proc = proc
        self.pid = proc.pid
        self._start_time = start_time
        self._max_buffer_bytes = max_buffer_bytes

        self._stdout_chunks: list[str] = []
        self._stderr_chunks: list[str] = []
        self._total_bytes: int = 0
        self._truncated: bool = False
        self._output_queue: asyncio.Queue[tuple[Literal["stdout", "stderr"], str] | None] = asyncio.Queue()
        self._readers_done: asyncio.Event = asyncio.Event()
        self._killed = False

        # Start readers
        self._reader_task = asyncio.create_task(self._pump_output())

    async def _pump_output(self) -> None:
        async def _read_stream(
            stream: asyncio.StreamReader | None, stream_name: Literal["stdout", "stderr"]
        ):
            if stream is None:
                return
            while True:
                line = await stream.readline()
                if not line:
                    break
                text = line.decode("utf-8", errors="replace")
                self._total_bytes += len(line)
                if self._total_bytes <= self._max_buffer_bytes:
                    if stream_name == "stdout":
                        self._stdout_chunks.append(text)
                    else:
                        self._stderr_chunks.append(text)
                else:
                    self._truncated = True

                await self._output_queue.put((stream_name, text))

        await asyncio.gather(
            _read_stream(self._proc.stdout, "stdout"),
            _read_stream(self._proc.stderr, "stderr"),
        )
        await self._output_queue.put(None)
        self._readers_done.set()

    async def output(self) -> AsyncIterator[tuple[Literal["stdout", "stderr"], str]]:
        """Streams stdout and stderr chunks in real time."""
        while True:
            item = await self._output_queue.get()
            if item is None:
                break
            yield item

    async def send_input(self, text: str) -> None:
        """Sends stdin input to process."""
        if self._proc.stdin and not self._proc.stdin.is_closing():
            data = text.encode("utf-8")
            self._proc.stdin.write(data)
            await self._proc.stdin.drain()

    async def kill(self, grace_s: float = 2.0) -> None:
        """
        Idempotently terminates or kills process group.
        Sends SIGTERM first, waits grace_s, then escalates to SIGKILL if necessary.
        """
        if self._proc.returncode is not None:
            return

        self._killed = True
        pid = self.pid
        if pid is None:
            return

        try:
            os.killpg(pid, signal.SIGTERM)
        except (ProcessLookupError, OSError):
            return

        # Wait grace period
        start_wait = time.time()
        while time.time() - start_wait < grace_s:
            if self._proc.returncode is not None:
                return
            await asyncio.sleep(0.05)

        # Escalate to SIGKILL if still running
        if self._proc.returncode is None:
            try:
                os.killpg(pid, signal.SIGKILL)
            except (ProcessLookupError, OSError):
                pass

    async def wait(self) -> ExecResult:
        """Waits for process termination and reader completion."""
        exit_code = await self._proc.wait()
        await self._readers_done.wait()

        duration_ms = int((time.time() - self._start_time) * 1000)
        return ExecResult(
            exit_code=exit_code,
            stdout="".join(self._stdout_chunks),
            stderr="".join(self._stderr_chunks),
            truncated=self._truncated,
            duration_ms=duration_ms,
        )


class LocalExecutor:
    """
    Runs commands in local subprocesses with process group isolation,
    environment scrubbing, and real-time output streaming.
    """

    name: str = "local"
    isolation: Literal["none"] = "none"

    def __init__(self, default_cwd: Path | None = None) -> None:
        self.default_cwd = default_cwd or Path.cwd()

    def _build_env(self, env_overlay: dict[str, str]) -> dict[str, str]:
        env: dict[str, str] = {
            "GIT_PAGER": "cat",
            "GIT_TERMINAL_PROMPT": "0",
            "PAGER": "cat",
        }
        for k in SAFE_ENV_KEYS:
            if k in os.environ:
                env[k] = os.environ[k]
        env.update(env_overlay)
        return env

    async def start(self, req: ExecRequest) -> LocalProcess:
        """Launches process and returns active Process handle."""
        cmd = req.command
        cwd = Path(req.cwd).resolve() if req.cwd else self.default_cwd
        cwd.mkdir(parents=True, exist_ok=True)
        env = self._build_env(req.env_overlay)
        process_id = f"proc_{uuid.uuid4().hex[:12]}"

        start_time = time.time()
        stdin_mode = asyncio.subprocess.PIPE if req.interactive else asyncio.subprocess.DEVNULL

        if req.shell or isinstance(cmd, str):
            cmd_str = cmd if isinstance(cmd, str) else " ".join(cmd)
            proc = await asyncio.create_subprocess_shell(
                cmd_str,
                cwd=str(cwd),
                env=env,
                stdin=stdin_mode,
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE,
                preexec_fn=os.setsid,
            )
        else:
            proc = await asyncio.create_subprocess_exec(
                cmd[0],
                *cmd[1:],
                cwd=str(cwd),
                env=env,
                stdin=stdin_mode,
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE,
                preexec_fn=os.setsid,
            )

        return LocalProcess(
            process_id=process_id,
            proc=proc,
            start_time=start_time,
        )
