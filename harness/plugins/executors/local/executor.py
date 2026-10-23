"""
Local process executor implementation conforming to Executor and Process contracts (§4, PLG-02).
"""
from __future__ import annotations

import asyncio
import contextlib
import fcntl
import logging
import os
import time
import uuid
from collections.abc import AsyncIterator
from pathlib import Path
from typing import Literal

from harness.sdk.process_supervisor import supervisor_command, supervisor_environment
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
        *,
        status_reader: asyncio.StreamReader | None = None,
        control_fd: int | None = None,
        status_transport: asyncio.ReadTransport | None = None,
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
        self._output_error: str | None = None
        self._output_queue: asyncio.Queue[tuple[Literal["stdout", "stderr"], str] | None] = asyncio.Queue()
        self._readers_done: asyncio.Event = asyncio.Event()
        self._killed = False
        self._kill_task: asyncio.Task[None] | None = None
        self._control_fd = control_fd
        self._status_reader = status_reader
        self._status_transport = status_transport
        self._stop_sent = False
        self._payload_exit: int | None = None
        self._supervisor_error: str | None = None
        self._ready = asyncio.get_running_loop().create_future()
        self._ready.add_done_callback(lambda future: future.exception() if not future.cancelled() else None)
        self._command_done = asyncio.Event()

        # Start readers
        self._reader_task = asyncio.create_task(self._pump_output())
        self._supervisor_task = asyncio.create_task(self._watch_supervisor()) if status_reader is not None else None
        if status_reader is None:
            self._ready.set_result(None)

    def _request_control(self, command: str) -> None:
        if self._stop_sent:
            return
        if self._control_fd is None:
            raise RuntimeError("No live supervisor channel establishes process-group ownership")
        os.write(self._control_fd, (command + "\n").encode("ascii"))
        if command == "KILL":
            self._stop_sent = True

    def _close_control(self) -> None:
        if self._control_fd is not None:
            fd, self._control_fd = self._control_fd, None
            os.close(fd)  # EOF makes the live supervisor kill its OWN group.

    async def _watch_supervisor(self) -> None:
        try:
            while True:
                line = await self._status_reader.readline()
                if line == b"HZSUP1 READY\n":
                    if not self._ready.done():
                        self._ready.set_result(None)
                elif line.startswith(b"HZSUP1 EXIT ") and line.endswith(b"\n"):
                    code = int(line[12:].strip())
                    if not -127 <= code <= 255 or not self._ready.done():
                        raise ValueError("Invalid supervisor status sequence")
                    self._payload_exit = code
                    self._command_done.set()
                    # Foreground descendants are not independent detached jobs.
                    # Normal payload completion closes the entire owned scope.
                    await self._ensure_stop(0)
                    break
                elif not line:
                    if not self._killed:
                        raise RuntimeError("Supervisor exited without payload status")
                    break
                else:
                    raise ValueError("Invalid supervisor status")
        except Exception as err:
            self._supervisor_error = type(err).__name__
            self._killed = True
            with contextlib.suppress(OSError, RuntimeError):
                self._request_control("KILL")
        finally:
            self._command_done.set()
            if not self._ready.done():
                self._ready.set_exception(RuntimeError("Supervisor did not become ready"))
            if self._status_transport is not None:
                self._status_transport.close()
            self._close_control()

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

        readers = [asyncio.create_task(_read_stream(self._proc.stdout, "stdout")),
                   asyncio.create_task(_read_stream(self._proc.stderr, "stderr"))]
        try:
            await asyncio.gather(*readers)
        except Exception as err:
            self._output_error = type(err).__name__
            self._truncated = True
            logger.warning("Process output failed: %s", self._output_error)
            self._killed = True
            # Ask the identity anchor, never a possibly reused numeric PGID.
            try:
                self._request_control("KILL")
            except (OSError, RuntimeError) as stop_error:
                self._supervisor_error = type(stop_error).__name__
        finally:
            for reader in readers:
                reader.cancel()
            try:
                await asyncio.gather(*readers, return_exceptions=True)
            finally:
                self._output_queue.put_nowait(None)
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
        self._killed = True
        await self._ensure_stop(grace_s)

    async def _ensure_stop(self, grace_s: float) -> None:
        if self._kill_task is None:
            if self._control_fd is None:
                if self._stop_sent:
                    return
                raise RuntimeError("No supervisor ownership channel; numeric-group signalling refused")
            self._kill_task = asyncio.create_task(self._kill_group(grace_s))
        # Multiple cancellation paths share one owned group cleanup. Cancelling
        # a caller cannot cancel the cleanup or signal a later reused PID twice.
        await asyncio.shield(self._kill_task)

    async def _kill_group(self, grace_s: float) -> None:
        if grace_s > 0 and not self._stop_sent:
            self._request_control("TERM")
            with contextlib.suppress(TimeoutError):
                await asyncio.wait_for(self._command_done.wait(), timeout=grace_s)
        self._request_control("KILL")
        try:
            await asyncio.wait_for(asyncio.gather(self._proc.wait(), self._readers_done.wait()), timeout=1)
        except TimeoutError:
            # asyncio's subprocess wait can include pipe transport teardown.
            # A reaped anchor has an actual exit status even while output is
            # still being drained; wait() retains that output completion below.
            # Never treat a still-live/unreaped anchor as a confirmed stop.
            if self._proc.returncode is None:
                raise

    async def wait(self) -> ExecResult:
        """Waits for process termination and reader completion."""
        if self._supervisor_task is not None:
            await asyncio.shield(self._supervisor_task)
            if self._supervisor_error:
                raise RuntimeError(f"Process outcome unconfirmed: supervisor {self._supervisor_error}")
        anchor_exit = await self._proc.wait()
        await self._readers_done.wait()
        exit_code = self._payload_exit if self._payload_exit is not None else anchor_exit

        duration_ms = int((time.time() - self._start_time) * 1000)
        return ExecResult(
            exit_code=exit_code,
            stdout="".join(self._stdout_chunks),
            stderr="".join(self._stderr_chunks),
            truncated=self._truncated,
            duration_ms=duration_ms,
            metadata={"output_error": self._output_error} if self._output_error else {},
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
        self._startup_tasks: set[asyncio.Task] = set()

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
        cancelled = asyncio.Event()
        task = asyncio.create_task(self._start_supervised(req, cancelled))
        self._startup_tasks.add(task)

        def startup_finished(owned: asyncio.Task) -> None:
            self._startup_tasks.discard(owned)
            if not owned.cancelled():
                owned.exception()  # Caller cancellation must not orphan an error future.

        task.add_done_callback(startup_finished)
        try:
            return await asyncio.shield(task)
        except asyncio.CancelledError:
            cancelled.set()

            async def reclaim_handoff() -> None:
                try:
                    process = await task
                except asyncio.CancelledError:
                    return
                await process.kill(grace_s=0)
                await process.wait()

            cleanup = asyncio.create_task(reclaim_handoff())
            self._startup_tasks.add(cleanup)
            cleanup.add_done_callback(startup_finished)
            with contextlib.suppress(asyncio.CancelledError, Exception):
                await asyncio.shield(cleanup)
            raise

    async def _start_supervised(self, req: ExecRequest, cancelled: asyncio.Event) -> LocalProcess:
        cmd = req.command
        cwd = Path(req.cwd).resolve() if req.cwd else self.default_cwd
        cwd.mkdir(parents=True, exist_ok=True)
        env = self._build_env(req.env_overlay)
        process_id = f"proc_{uuid.uuid4().hex[:12]}"

        start_time = time.time()
        stdin_mode = asyncio.subprocess.PIPE if req.interactive else asyncio.subprocess.DEVNULL

        if req.shell or isinstance(cmd, str):
            cmd_str = cmd if isinstance(cmd, str) else " ".join(cmd)
            argv = ["/bin/sh", "-c", cmd_str]
        else:
            argv = list(cmd)
        if not argv:
            raise ValueError("Cannot execute an empty command")
        owned: set[int] = set()
        local = None
        proc = None
        transport = None
        cancel_wait = None
        try:
            status_read, status_write = os.pipe()
            owned.update((status_read, status_write))
            control_read, control_write = os.pipe()
            owned.update((control_read, control_write))
            inherited = []
            for fd in (status_write, control_read):
                duplicate = fcntl.fcntl(fd, fcntl.F_DUPFD_CLOEXEC, 10)
                owned.add(duplicate)
                inherited.append(duplicate)
            os.close(status_write)
            owned.remove(status_write)
            os.close(control_read)
            owned.remove(control_read)
            proc = await asyncio.create_subprocess_exec(
                *supervisor_command(argv, *inherited), cwd=str(cwd), env=supervisor_environment(env),
                stdin=stdin_mode, stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.PIPE,
                pass_fds=tuple(inherited), start_new_session=True,
            )
            for fd in inherited:
                os.close(fd)
                owned.remove(fd)
            reader = asyncio.StreamReader(limit=256)
            status_file = os.fdopen(status_read, "rb", buffering=0)
            owned.remove(status_read)
            try:
                transport, _ = await asyncio.get_running_loop().connect_read_pipe(
                    lambda: asyncio.StreamReaderProtocol(reader), status_file,
                )
            except BaseException:
                status_file.close()
                raise
            os.set_blocking(control_write, False)
            local = LocalProcess(process_id, proc, start_time, status_reader=reader,
                                 control_fd=control_write, status_transport=transport)
            owned.remove(control_write)
            cancel_wait = asyncio.create_task(cancelled.wait())
            done, _ = await asyncio.wait({local._ready, cancel_wait}, timeout=5, return_when=asyncio.FIRST_COMPLETED)
            if cancelled.is_set():
                raise asyncio.CancelledError
            if local._ready not in done:
                raise TimeoutError("Process supervisor startup timed out")
            await local._ready
            return local
        except BaseException:
            if local is not None:
                local._killed = True
                local._close_control()
                with contextlib.suppress(Exception):
                    await local.wait()
            elif proc is not None:
                # The live anchor responds to EOF even if Python has not yet
                # constructed the LocalProcess/status transport.
                if control_write in owned:
                    os.close(control_write)
                    owned.remove(control_write)
                with contextlib.suppress(Exception):
                    await asyncio.wait_for(proc.communicate(), 1)
            raise
        finally:
            if cancel_wait is not None:
                cancel_wait.cancel()
                await asyncio.gather(cancel_wait, return_exceptions=True)
            for fd in owned:
                with contextlib.suppress(OSError):
                    os.close(fd)
            if local is None and transport is not None:
                transport.close()
