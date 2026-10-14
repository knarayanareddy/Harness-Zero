"""
Worktree process executor conforming to Executor and Process contracts (§4, PLG-18).
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

from .worktree import WorktreeManager

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


class WorktreeProcess:
    """Handle for an active worktree process with streaming output and lifecycle management."""

    def __init__(
        self,
        process_id: str,
        proc: asyncio.subprocess.Process,
        start_time: float,
        max_buffer_bytes: int = 10 * 1024 * 1024,
    ) -> None:
        self.id = process_id
        self._proc = proc
        self.pid = proc.pid or 0
        self._start_time = start_time
        self._max_buffer_bytes = max_buffer_bytes

        self._stdout_chunks: list[str] = []
        self._stderr_chunks: list[str] = []
        self._total_bytes: int = 0
        self._truncated: bool = False
        self._output_queue: asyncio.Queue[tuple[Literal["stdout", "stderr"], str] | None] = asyncio.Queue()
        self._readers_done: asyncio.Event = asyncio.Event()
        self._killed = False

        self._reader_task = asyncio.create_task(self._pump_output())

    async def _pump_output(self) -> None:
        async def _read_stream(stream: asyncio.StreamReader | None, stream_name: Literal["stdout", "stderr"]):
            if stream is None:
                return
            while True:
                line = await stream.readline()
                if not line:
                    break
                text = line.decode("utf-8", errors="replace")
                if self._total_bytes < self._max_buffer_bytes:
                    self._total_bytes += len(line)
                    if stream_name == "stdout":
                        self._stdout_chunks.append(text)
                    else:
                        self._stderr_chunks.append(text)
                else:
                    self._truncated = True

                await self._output_queue.put((stream_name, text))

        try:
            await asyncio.gather(
                _read_stream(self._proc.stdout, "stdout"),
                _read_stream(self._proc.stderr, "stderr"),
            )
        finally:
            await self._output_queue.put(None)
            self._readers_done.set()

    async def lines(self) -> AsyncIterator[tuple[Literal["stdout", "stderr"], str]]:
        while True:
            item = await self._output_queue.get()
            if item is None:
                break
            yield item

    async def send_input(self, text: str) -> None:
        if self._proc.stdin and not self._proc.stdin.is_closing():
            try:
                self._proc.stdin.write(text.encode("utf-8"))
                await self._proc.stdin.drain()
            except (BrokenPipeError, ConnectionResetError):
                pass

    async def kill(self, grace_s: float = 2.0) -> None:
        if self._killed or self._proc.returncode is not None:
            return
        self._killed = True
        pid = self.pid
        if pid <= 0:
            return

        try:
            os.killpg(pid, signal.SIGTERM)
        except (ProcessLookupError, OSError):
            pass

        try:
            await asyncio.wait_for(self._proc.wait(), timeout=grace_s)
        except TimeoutError:
            pass

        if self._proc.returncode is None:
            try:
                os.killpg(pid, signal.SIGKILL)
            except (ProcessLookupError, OSError):
                pass

    async def wait(self) -> ExecResult:
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


class WorktreeExecutor:
    """
    Executes commands within git worktree sandboxes or workspaces.
    Conforms to the Executor protocol with isolation='worktree'.
    """

    name: str = "worktree"
    isolation: Literal["worktree"] = "worktree"

    def __init__(self, repo_root: Path, default_cwd: Path | None = None) -> None:
        self.repo_root = Path(repo_root)
        self.default_cwd = default_cwd or self.repo_root
        self.worktree_mgr = WorktreeManager(repo_root=self.repo_root)

    def _build_env(self, env_overlay: dict[str, str]) -> dict[str, str]:
        env: dict[str, str] = {}
        for k in SAFE_ENV_KEYS:
            if k in os.environ:
                env[k] = os.environ[k]
        env.update(env_overlay)
        return env

    async def start(self, req: ExecRequest) -> WorktreeProcess:
        cmd = req.command
        cwd = Path(req.cwd).resolve() if req.cwd else self.default_cwd
        cwd.mkdir(parents=True, exist_ok=True)
        env = self._build_env(req.env_overlay)
        process_id = f"wt_proc_{uuid.uuid4().hex[:12]}"

        # Close stdin if non-interactive
        stdin_mode = asyncio.subprocess.PIPE if req.interactive else asyncio.subprocess.DEVNULL

        try:
            proc = await asyncio.create_subprocess_exec(
                *cmd,
                cwd=cwd,
                env=env,
                stdin=stdin_mode,
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE,
                start_new_session=True,
            )
        except Exception as err:
            logger.error(f"Failed to start worktree process {cmd}: {err}")
            raise

        return WorktreeProcess(
            process_id=process_id,
            proc=proc,
            start_time=time.time(),
        )
