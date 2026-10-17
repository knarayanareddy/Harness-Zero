"""Durable, session-scoped process jobs shared by tools, the runner, and the Studio panel."""
from __future__ import annotations

import asyncio
import json
import logging
import os
import re
import stat
import time
import uuid
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from harness.sdk.context import JobsView, PluginCommandContext, PluginContext
from harness.sdk.contracts import Executor, Process
from harness.sdk.events import JobExited, JobOutput, JobStarted, TaskCompleted, TaskStarted
from harness.sdk.types import ExecRequest, ExecResult

logger = logging.getLogger(__name__)

_SESSION_ID = re.compile(r"^session_[0-9a-f]{12}$")
_JOB_ID = re.compile(r"^job_[0-9a-f]{12}$")
_INDEX_NAME = "index.jsonl"
_FLUSH_INTERVAL_S = 0.25
_MAX_EVENT_BYTES = 64 * 1024
_INDEX_COMPACT_BYTES = 8 * 1024 * 1024


def _write_all(fd: int, data: bytes) -> None:
    """Write every byte even when the OS returns a short write."""
    view = memoryview(data)
    while view:
        written = os.write(fd, view)
        if written <= 0:
            raise OSError("short write while persisting job data")
        view = view[written:]


def _open_regular_file(path: Path, flags: int, mode: int = 0o600) -> int:
    """Open a job data file without following links or blocking on special files."""
    safe_flags = flags | getattr(os, "O_NOFOLLOW", 0) | getattr(os, "O_NONBLOCK", 0)
    fd = os.open(path, safe_flags, mode)
    try:
        info = os.fstat(fd)
        if not stat.S_ISREG(info.st_mode):
            raise ValueError(f"Refusing non-regular job data file: {path.name}")
        if getattr(info, "st_nlink", 1) > 1:
            raise ValueError(f"Refusing multiply-linked job data file: {path.name}")
    except BaseException:
        os.close(fd)
        raise
    return fd


def _read_regular_file(path: Path) -> bytes:
    fd = _open_regular_file(path, os.O_RDONLY)
    try:
        chunks: list[bytes] = []
        while chunk := os.read(fd, 64 * 1024):
            chunks.append(chunk)
        return b"".join(chunks)
    finally:
        os.close(fd)


@dataclass(slots=True)
class Job:
    session_id: str
    job_id: str
    label: str
    command: str
    cwd: str
    started_at: float
    state: str = "starting"
    pid: int | None = None
    exit_code: int | None = None
    reason: str | None = None
    ended_at: float | None = None
    duration_ms: float | None = None
    output_bytes: int = 0
    spill_base_offset: int = 0
    reported: bool = False
    started_recorded: bool = False
    exited_recorded: bool = False
    persistent: bool = True
    reactive_wakeup: bool = False
    sandbox: dict[str, Any] | None = None
    process: Process | None = None
    activation_task: asyncio.Task[None] | None = None
    monitor_task: asyncio.Task[None] | None = None
    kill_requested: bool = False
    ring: bytearray = field(default_factory=bytearray)


@dataclass(slots=True)
class JobWakeup:
    """Structural equivalent of the kernel's wake-up event; plugins stay kernel-independent."""

    task_id: str
    exit_code: int
    tail: str
    label: str = ""
    timestamp: float = field(default_factory=time.time)
    session_id: str = ""


class JobRegistry:
    """The registered ``jobs`` service and a BackgroundInbox-compatible wake-up source."""

    def __init__(self, ctx: PluginContext) -> None:
        self.ctx = ctx
        self.workspace = Path(ctx.workspace).resolve()
        self.ring_bytes = max(1, int(ctx.settings.get("ring_bytes", 65_536) or 65_536))
        self.max_jobs = max(1, int(ctx.settings.get("max_jobs", 20) or 20))
        self.spill_max_bytes = max(1, int(ctx.settings.get("spill_max_bytes", 52_428_800) or 52_428_800))
        self._jobs: dict[tuple[str, str], Job] = {}
        self._wakeup_queues: dict[str, asyncio.Queue[JobWakeup]] = {}
        self._global_wakeup_queue: asyncio.Queue[JobWakeup] = asyncio.Queue()
        self._load_snapshots()

    # ------------------------------------------------------------------ public service / views
    def for_session(self, session_id: str, turn_id: str | None = None) -> JobsView:
        self._validate_session_id(session_id)
        return _SessionJobsView(self, session_id, turn_id)

    async def start(
        self,
        session_id: str,
        turn_id: str | None,
        executor: Executor,
        req: ExecRequest,
        label: str = "",
    ) -> dict[str, Any]:
        """Write ``job.started`` before asking the executor to create the process."""
        self._ensure_capacity(session_id)
        job = self._new_job(
            session_id=session_id,
            command=str(req.command),
            cwd=str(req.cwd),
            label=label or str(req.command),
            turn_id=turn_id,
        )
        self._jobs[(session_id, job.job_id)] = job
        self._append_index(job)
        try:
            await self._emit_started(job, turn_id=turn_id)
        except Exception:
            self._jobs.pop((session_id, job.job_id), None)
            self._append_tombstone(job)
            raise

        try:
            proc = await executor.start(req)
        except Exception as err:
            await self._finish(
                job,
                exit_code=1,
                reason="start_error",
                duration_ms=(time.time() - job.started_at) * 1000,
                turn_id=turn_id,
                publish_wakeup=False,
            )
            raise RuntimeError(f"Could not start job: {err}") from err

        job.process = proc
        job.pid = getattr(proc, "pid", None)
        job.sandbox = self._sandbox_of(proc)
        job.state = "running"
        self._append_index(job)
        job.monitor_task = asyncio.create_task(self._monitor(job, turn_id=turn_id))
        return self._summary(job)

    def register_process(
        self,
        session_id: str,
        turn_id: str | None,
        process: Process,
        label: str = "",
        *,
        initial_output: str = "",
    ) -> str:
        """Adopt a process already started by the legacy ``run_command`` tool."""
        self._validate_session_id(session_id)
        self._ensure_capacity(session_id)
        command = str(getattr(process, "command", "") or label or getattr(process, "id", "background job"))
        job = self._new_job(session_id=session_id, command=command, cwd=str(self.workspace), label=label or command, turn_id=turn_id)
        job.reactive_wakeup = True
        job.process = process
        job.pid = getattr(process, "pid", None)
        job.sandbox = self._sandbox_of(process)
        job.state = "running"
        self._jobs[(session_id, job.job_id)] = job
        self._append_index(job)
        job.activation_task = asyncio.create_task(
            self._activate_legacy(job, turn_id=turn_id, initial_output=initial_output)
        )
        return job.job_id

    def get(self, session_id: str, job_id: str) -> dict[str, Any] | None:
        job = self._find(session_id, job_id)
        return self._summary(job) if job is not None else None

    def list(self, session_id: str) -> list[dict[str, Any]]:
        self._validate_session_id(session_id)
        jobs = [job for (sid, _), job in self._jobs.items() if sid == session_id]
        jobs.sort(key=lambda job: (job.started_at, job.job_id), reverse=True)
        return [self._summary(job) for job in jobs]

    async def output(
        self,
        session_id: str,
        job_id: str,
        *,
        tail_lines: int = 50,
        since_offset: int | None = None,
    ) -> dict[str, Any] | None:
        job = self._find(session_id, job_id)
        if job is None:
            return None
        tail_lines = max(1, min(int(tail_lines), 10_000))
        if job.persistent:
            spill = self._spill_path(job)
            self._check_safe_file(spill, self._jobs_dir(session_id))
            try:
                raw = _read_regular_file(spill)
            except FileNotFoundError:
                raw = b""
            total_bytes = max(job.output_bytes, job.spill_base_offset + len(raw))
            base_offset = max(job.spill_base_offset, total_bytes - len(raw))
        else:
            raw = bytes(job.ring)
            total_bytes = job.output_bytes
            base_offset = max(0, total_bytes - len(raw))

        if since_offset is not None:
            requested = max(0, int(since_offset))
            start_offset = max(requested, base_offset)
            truncated = requested < base_offset
            local_start = max(0, start_offset - base_offset)
            content = raw[local_start:].decode("utf-8", errors="replace")
            next_offset = base_offset + len(raw)
        else:
            lines = raw.decode("utf-8", errors="replace").splitlines(keepends=True)
            content = "".join(lines[-tail_lines:])
            encoded_content = content.encode("utf-8", errors="replace")
            start_offset = max(base_offset, total_bytes - len(encoded_content))
            next_offset = total_bytes
            truncated = base_offset > 0

        return {
            "job_id": job.job_id,
            "label": job.label,
            "state": job.state,
            "running": job.state in {"starting", "running"},
            "exit_code": job.exit_code,
            "reason": job.reason,
            "output": content,
            "offset": start_offset,
            "next_offset": next_offset,
            "total_bytes": total_bytes,
            "truncated": truncated,
            "spill_path": self._relative_spill_path(job) if job.persistent else None,
        }

    async def kill(self, session_id: str, job_id: str) -> bool:
        job = self._find(session_id, job_id)
        if job is None or job.state != "running" or job.process is None:
            return False
        job.kill_requested = True
        kill = getattr(job.process, "kill", None)
        if kill is None:
            return False
        outcome = kill()
        if asyncio.iscoroutine(outcome):
            await outcome
        await self.wait_task(session_id, job_id, timeout_s=10.0)
        return job.state == "exited"

    async def send_input(self, session_id: str, job_id: str, text: str) -> bool:
        job = self._find(session_id, job_id)
        if job is None or job.state != "running" or job.process is None:
            return False
        send_input = getattr(job.process, "send_input", None)
        if send_input is None:
            return False
        outcome = send_input(text)
        if asyncio.iscoroutine(outcome):
            await outcome
        return True

    async def wait_task(self, session_id: str, job_id: str, timeout_s: float | None = None) -> int | None:
        job = self._find(session_id, job_id)
        if job is None:
            return None
        tasks = [task for task in (job.activation_task, job.monitor_task) if task is not None]
        if job.state == "exited":
            job.reported = True
            self._append_index(job)
            return job.exit_code
        if tasks:
            waiter = asyncio.gather(*tasks)
            if timeout_s is not None and timeout_s > 0:
                await asyncio.wait_for(asyncio.shield(waiter), timeout=timeout_s)
            else:
                await waiter
        if job.state == "exited":
            job.reported = True
            self._append_index(job)
        return job.exit_code

    def get_status(self, job_id: str, session_id: str | None = None) -> dict[str, Any] | None:
        job = self._find(session_id, job_id) if session_id else self._find_any(job_id)
        if job is None:
            return None
        return self._legacy_summary(job)

    def list_tasks(self, session_id: str | None = None) -> list[dict[str, Any]]:
        jobs = list(self._jobs.values())
        if session_id:
            jobs = [job for job in jobs if job.session_id == session_id]
        jobs.sort(key=lambda job: (job.started_at, job.job_id))
        return [self._legacy_summary(job) for job in jobs]

    def list_pending_tasks(self, session_id: str | None = None) -> list[dict[str, Any]]:
        """Expose active legacy `run_command` tasks to the turn's reactive wait loop; `job_start` is detached."""
        jobs = [
            job
            for job in self._jobs.values()
            if job.reactive_wakeup
            and job.state in {"starting", "running"}
            and (session_id is None or job.session_id == session_id)
        ]
        jobs.sort(key=lambda job: (job.started_at, job.job_id))
        return [self._legacy_summary(job) for job in jobs]

    def has_pending(self, session_id: str | None = None) -> bool:
        return bool(self.list_pending_tasks(session_id))

    async def wait_for_wakeup(
        self,
        timeout_s: float = 600.0,
        session_id: str | None = None,
    ) -> JobWakeup | None:
        queue = self._global_wakeup_queue if session_id is None else self._queue_for(session_id)
        try:
            async with asyncio.timeout(timeout_s):
                wakeup = await queue.get()
        except TimeoutError:
            return None
        if wakeup.session_id:
            job = self._find(wakeup.session_id, wakeup.task_id)
            if job is not None and not job.reported:
                job.reported = True
                self._append_index(job)
        return wakeup

    async def pending_turn_notice(self, session_id: str) -> dict[str, Any] | None:
        """Returns a one-turn notice for exited jobs not already consumed by a wake-up or prior turn."""
        unreported = [
            job
            for (sid, _), job in self._jobs.items()
            if sid == session_id and job.state == "exited" and not job.reported
        ]
        if not unreported:
            return None
        unreported.sort(key=lambda job: (job.ended_at or job.started_at, job.job_id))
        summaries: list[str] = []
        ids: list[str] = []
        for job in unreported[:20]:
            ids.append(job.job_id)
            duration = max(0.0, (job.duration_ms or 0.0) / 1000.0)
            tail = await self.output(session_id, job.job_id, tail_lines=20)
            tail_text = (tail or {}).get("output", "").strip().replace("\n", " | ")[-1200:]
            if job.reason == "lost":
                outcome = "was lost when the daemon restarted"
            elif job.exit_code is None:
                outcome = f"exited ({job.reason or 'unknown'})"
            else:
                outcome = f"exited {job.exit_code}"
            part = f'#{job.job_id} "{job.label}" {outcome} after {duration:.0f}s'
            if tail_text:
                part += f" (tail: {tail_text})"
            summaries.append(part)
        return {
            "message": "Jobs finished since your last turn: " + "; ".join(summaries) + ". Use job_output for more.",
            "job_ids": ids,
        }

    def mark_reported(self, session_id: str, job_ids: list[str]) -> None:
        for job_id in job_ids:
            job = self._find(session_id, job_id)
            if job is not None:
                job.reported = True
                self._append_index(job)

    async def recover(self) -> None:
        """Close every persisted running job: process handles cannot survive daemon reconstruction."""
        recovered = [job for job in self._jobs.values() if job.state in {"starting", "running"}]
        for job in recovered:
            if not job.started_recorded:
                try:
                    await self._emit_started(job, turn_id=None)
                except Exception as err:
                    logger.warning("Could not repair missing job.started record for %s: %s", job.job_id, err)
            tail = await self.output(job.session_id, job.job_id, tail_lines=20)
            await self._finish(
                job,
                exit_code=None,
                reason="lost",
                duration_ms=max(0.0, (time.time() - job.started_at) * 1000),
                turn_id=None,
                tail=str((tail or {}).get("output", "")),
                publish_wakeup=False,
            )

    async def close(self) -> None:
        """Stop child processes on a graceful daemon shutdown; abrupt restarts are recovered as lost."""
        active = [job for job in self._jobs.values() if job.state == "running" and job.process is not None]
        for job in active:
            try:
                await self.kill(job.session_id, job.job_id)
            except Exception as err:  # cleanup must not mask daemon shutdown
                logger.warning("Could not stop background job %s during shutdown: %s", job.job_id, err)
        pending = [
            task
            for job in self._jobs.values()
            for task in (job.activation_task, job.monitor_task)
            if task is not None and not task.done()
        ]
        if pending:
            await asyncio.gather(*pending, return_exceptions=True)

    # --------------------------------------------------------------- inbox compatibility
    def register_task(
        self,
        task_id: str,
        proc: Process,
        label: str = "",
        max_tail_lines: int = 50,
        session_id: str | None = None,
    ) -> Job:
        """BackgroundInbox-compatible entry point retained for older kernel embedders."""
        sid = session_id or ""
        job_id = task_id if _JOB_ID.fullmatch(task_id) else f"job_{uuid.uuid4().hex[:12]}"
        return self._register_legacy_with_id(sid, job_id, proc, label, persistent=bool(sid))

    def _register_legacy_with_id(
        self,
        session_id: str,
        job_id: str,
        process: Process,
        label: str,
        *,
        persistent: bool,
    ) -> Job:
        if not session_id:
            # Legacy callers without session identity stay in memory; they must not create a fake session.
            session_id = "session_000000000000"
        self._validate_session_id(session_id)
        if persistent:
            self._ensure_capacity(session_id)
        command = str(getattr(process, "command", "") or label or getattr(process, "id", "background job"))
        job = Job(
            session_id=session_id,
            job_id=job_id,
            label=label or command,
            command=command,
            cwd=str(self.workspace),
            started_at=time.time(),
            state="running",
            pid=getattr(process, "pid", None),
            sandbox=self._sandbox_of(process),
            process=process,
            persistent=persistent,
            reactive_wakeup=True,
        )
        self._jobs[(session_id, job_id)] = job
        self._append_index(job)
        job.activation_task = asyncio.create_task(self._activate_legacy(job, turn_id=None, initial_output=""))
        return job

    async def wait_task_compat(self, task_id: str, timeout_s: float | None = None) -> Any:
        job = self._find_any(task_id)
        if job is None:
            return None
        return await self.wait_task(job.session_id, task_id, timeout_s)

    async def kill_compat(self, task_id: str) -> bool:
        job = self._find_any(task_id)
        return await self.kill(job.session_id, task_id) if job is not None else False

    async def send_input_compat(self, task_id: str, text: str) -> bool:
        job = self._find_any(task_id)
        return await self.send_input(job.session_id, task_id, text) if job is not None else False

    # ------------------------------------------------------------------ commands
    async def command_list(self, session_id: str, _context: PluginCommandContext) -> list[dict[str, Any]]:
        return self.list(session_id)

    async def command_output(
        self,
        session_id: str,
        job_id: str,
        tail_lines: int = 50,
        since_offset: int | None = None,
        _context: PluginCommandContext | None = None,
    ) -> dict[str, Any] | None:
        return await self.output(session_id, job_id, tail_lines=tail_lines, since_offset=since_offset)

    async def command_kill(
        self,
        session_id: str,
        job_id: str,
        _context: PluginCommandContext,
    ) -> dict[str, Any]:
        job = self._find(session_id, job_id)
        if job is None:
            return {"ok": False, "job_id": job_id, "reason": "not_found"}
        async with _context.session_lock(session_id):
            allowed, reason = await _context.request_approval(
                session_id=session_id,
                kind="job.kill",
                summary=f"Stop background job {job.job_id} ({job.label})",
                details={"job_id": job.job_id, "label": job.label, "command": job.command},
                destructive=True,
            )
            if not allowed:
                return {"ok": False, "job_id": job_id, "reason": reason or "approval_denied"}
            killed = await self.kill(session_id, job_id)
        return {"ok": killed, "job_id": job_id, "state": (self._find(session_id, job_id).state if self._find(session_id, job_id) else "missing")}

    # ------------------------------------------------------------------ lifecycle internals
    async def _activate_legacy(self, job: Job, *, turn_id: str | None, initial_output: str) -> None:
        try:
            await self._emit_started(job, turn_id=turn_id)
            job.started_recorded = job.persistent
        except Exception as err:
            logger.warning("Could not persist legacy job.started for %s: %s", job.job_id, err)
        self._append_index(job)
        self._emit_legacy_started(job, turn_id=turn_id)
        if initial_output:
            await self._append_text(job, "combined", initial_output)
            await self._flush_output(job, [("combined", initial_output)], turn_id=turn_id)
        job.monitor_task = asyncio.create_task(self._monitor(job, turn_id=turn_id))

    async def _monitor(self, job: Job, *, turn_id: str | None) -> None:
        process = job.process
        if process is None:
            return
        pending: list[tuple[str, str]] = []
        pending_bytes = 0
        pending_start = job.output_bytes
        last_flush = asyncio.get_running_loop().time()
        iterator = process.output().__aiter__()
        next_chunk = asyncio.create_task(iterator.__anext__())
        exit_code: int | None = None
        duration_ms: float | None = None
        reason = "exit"
        try:
            while True:
                elapsed = asyncio.get_running_loop().time() - last_flush
                remaining = max(0.0, _FLUSH_INTERVAL_S - elapsed)
                done, _ = await asyncio.wait({next_chunk}, timeout=remaining)
                if not done:
                    if pending:
                        await self._flush_output(job, pending, start_offset=pending_start, turn_id=turn_id)
                        pending = []
                        pending_bytes = 0
                        pending_start = job.output_bytes
                    last_flush = asyncio.get_running_loop().time()
                    continue
                try:
                    stream, text = next_chunk.result()
                except StopAsyncIteration:
                    break
                next_chunk = asyncio.create_task(iterator.__anext__())
                if not isinstance(text, str) or not text:
                    continue
                start_offset = job.output_bytes
                await self._append_text(job, stream, text)
                if not pending:
                    pending_start = start_offset
                pending.append((stream, text))
                pending_bytes += len(text.encode("utf-8", errors="replace"))
                elapsed = asyncio.get_running_loop().time() - last_flush
                if pending_bytes >= _MAX_EVENT_BYTES or elapsed >= _FLUSH_INTERVAL_S:
                    await self._flush_output(job, pending, start_offset=pending_start, turn_id=turn_id)
                    pending = []
                    pending_bytes = 0
                    pending_start = job.output_bytes
                    last_flush = asyncio.get_running_loop().time()

            if pending:
                await self._flush_output(job, pending, start_offset=pending_start, turn_id=turn_id)
            wait_result = await process.wait()
            if isinstance(wait_result, ExecResult):
                exit_code = wait_result.exit_code
                duration_ms = wait_result.duration_ms
            elif isinstance(wait_result, int):
                exit_code = wait_result
            if exit_code is None:
                exit_code = getattr(process, "exit_code", None)
            if exit_code is None:
                exit_code = 0
            if job.kill_requested:
                reason = "killed"
        except asyncio.CancelledError:
            reason = "monitor_cancelled"
            exit_code = -15
            raise
        except Exception as err:
            logger.exception("Error monitoring background job %s: %s", job.job_id, err)
            reason = "error"
            exit_code = 1
        finally:
            if not next_chunk.done():
                next_chunk.cancel()
                await asyncio.gather(next_chunk, return_exceptions=True)
            if job.state != "exited":
                await self._finish(
                    job,
                    exit_code=exit_code,
                    reason=reason,
                    duration_ms=duration_ms or (time.time() - job.started_at) * 1000,
                    turn_id=turn_id,
                    publish_wakeup=True,
                )

    async def _append_text(self, job: Job, stream: str, text: str) -> None:
        data = text.encode("utf-8", errors="replace")
        if job.persistent:
            spill = self._spill_path(job)
            jobs_dir = self._jobs_dir(job.session_id)
            self._check_safe_file(spill, jobs_dir)
            flags = os.O_APPEND | os.O_CREAT | os.O_WRONLY
            fd = _open_regular_file(spill, flags, 0o600)
            try:
                _write_all(fd, data)
            finally:
                os.close(fd)
        job.output_bytes += len(data)
        if len(data) >= self.ring_bytes:
            job.ring[:] = data[-self.ring_bytes :]
        else:
            job.ring.extend(data)
            if len(job.ring) > self.ring_bytes:
                del job.ring[: len(job.ring) - self.ring_bytes]
        if job.persistent:
            self._bound_spill(job)

    def _bound_spill(self, job: Job) -> None:
        spill = self._spill_path(job)
        jobs_dir = self._jobs_dir(job.session_id)
        self._check_safe_file(spill, jobs_dir)
        fd = _open_regular_file(spill, os.O_RDONLY)
        try:
            size = os.fstat(fd).st_size
            if size <= self.spill_max_bytes:
                job.spill_base_offset = max(0, job.output_bytes - size)
                return
            with os.fdopen(fd, "rb", closefd=False) as stream:
                stream.seek(size - self.spill_max_bytes)
                tail = stream.read(self.spill_max_bytes)
        finally:
            os.close(fd)
        fd = _open_regular_file(spill, os.O_WRONLY)
        try:
            os.ftruncate(fd, 0)
            _write_all(fd, tail)
            os.fsync(fd)
        finally:
            os.close(fd)
        job.spill_base_offset = max(0, job.output_bytes - len(tail))

    async def _flush_output(
        self,
        job: Job,
        chunks: list[tuple[str, str]],
        *,
        start_offset: int | None = None,
        turn_id: str | None,
    ) -> None:
        if not chunks or not job.persistent:
            return
        text = "".join(chunk for _, chunk in chunks)
        streams = {stream for stream, _ in chunks}
        stream = next(iter(streams)) if len(streams) == 1 else "mixed"
        offset_start = job.output_bytes - len(text.encode("utf-8", errors="replace")) if start_offset is None else start_offset
        offset_end = job.output_bytes
        spill = self._spill_path(job)
        self._check_safe_file(spill, self._jobs_dir(job.session_id))
        try:
            fd = _open_regular_file(spill, os.O_WRONLY | os.O_APPEND)
        except FileNotFoundError:
            fd = None
        if fd is not None:
            try:
                os.fsync(fd)
            finally:
                os.close(fd)
        payload = JobOutput(
            job_id=job.job_id,
            offset_start=offset_start,
            offset_end=offset_end,
            stream=stream,
            text=text,
        )
        metadata = {
            "job_id": job.job_id,
            "offset_start": offset_start,
            "offset_end": offset_end,
            "bytes": max(0, offset_end - offset_start),
            "stream": stream,
            "spill_path": self._relative_spill_path(job),
        }
        try:
            await self.ctx.emit_record(
                payload,
                content=f"Persisted {metadata['bytes']} output bytes for {job.job_id} at offsets {offset_start}:{offset_end}.",
                metadata=metadata,
                session_id=job.session_id,
                turn_id=turn_id,
            )
        except Exception as err:
            # Output is already durable in the bounded spill file; keep monitoring and report the record failure.
            logger.warning("Could not persist job.output record for %s: %s", job.job_id, err)
        self._append_progress(job)

    async def _emit_started(self, job: Job, *, turn_id: str | None) -> None:
        if not job.persistent:
            return
        payload = JobStarted(
            job_id=job.job_id,
            label=job.label,
            command=job.command,
            cwd=job.cwd,
            pid=job.pid,
            started_at=job.started_at,
            sandbox=job.sandbox,
        )
        metadata = {
            "job_id": job.job_id,
            "label": job.label,
            "command": job.command,
            "cwd": job.cwd,
            "pid": job.pid,
            "started_at": job.started_at,
            "sandbox": job.sandbox,
        }
        record = await self.ctx.emit_record(
            payload,
            content=f"Job {job.job_id} started: {job.label}",
            metadata=metadata,
            session_id=job.session_id,
            turn_id=turn_id,
        )
        if record is None:
            raise RuntimeError(f"job.started could not be attached to session {job.session_id}")
        job.started_recorded = True
        self._append_index(job)

    async def _finish(
        self,
        job: Job,
        *,
        exit_code: int | None,
        reason: str,
        duration_ms: float,
        turn_id: str | None,
        tail: str | None = None,
        publish_wakeup: bool,
    ) -> None:
        if job.state == "exited" and job.exited_recorded:
            return
        job.state = "exited"
        job.exit_code = exit_code
        job.reason = reason
        job.ended_at = time.time()
        job.duration_ms = max(0.0, float(duration_ms))
        if tail is None:
            tail = self._tail_text(job, 20)
        payload = JobExited(
            job_id=job.job_id,
            label=job.label,
            exit_code=job.exit_code,
            reason=reason,
            duration_ms=job.duration_ms,
            tail=tail,
        )
        metadata = {
            "job_id": job.job_id,
            "label": job.label,
            "exit_code": job.exit_code,
            "reason": reason,
            "duration_ms": job.duration_ms,
            "ended_at": job.ended_at,
            "output_bytes": job.output_bytes,
            "spill_base_offset": job.spill_base_offset,
            "tail": tail,
        }
        if job.persistent:
            try:
                await self.ctx.emit_record(
                    payload,
                    content=f"Job {job.job_id} {reason}: exit code {job.exit_code} after {job.duration_ms / 1000:.1f}s.\n{tail}",
                    metadata=metadata,
                    session_id=job.session_id,
                    turn_id=turn_id,
                )
                job.exited_recorded = True
            except Exception as err:
                logger.warning("Could not persist job.exited record for %s: %s", job.job_id, err)
        self._append_index(job)
        if job.reactive_wakeup:
            self._emit_legacy_completed(job, tail=tail, turn_id=turn_id)
        if publish_wakeup and job.session_id and job.reactive_wakeup:
            wakeup = JobWakeup(
                task_id=job.job_id,
                exit_code=job.exit_code if job.exit_code is not None else 1,
                tail=tail,
                label=job.label,
                session_id=job.session_id,
            )
            self._queue_for(job.session_id).put_nowait(wakeup)
            self._global_wakeup_queue.put_nowait(wakeup)

    def _emit_legacy_started(self, job: Job, *, turn_id: str | None) -> None:
        try:
            self.ctx.emit_event(
                TaskStarted(task_id=job.job_id, label=job.label),
                session_id=job.session_id if job.persistent else None,
                turn_id=turn_id,
            )
        except Exception as err:
            logger.debug("Could not publish legacy task.started event: %s", err)

    def _emit_legacy_completed(self, job: Job, *, tail: str, turn_id: str | None) -> None:
        try:
            self.ctx.emit_event(
                TaskCompleted(
                    task_id=job.job_id,
                    label=job.label,
                    exit_code=job.exit_code,
                    tail=tail,
                ),
                session_id=job.session_id if job.persistent else None,
                turn_id=turn_id,
            )
        except Exception as err:
            logger.debug("Could not publish legacy task.completed event: %s", err)

    def _new_job(self, *, session_id: str, command: str, cwd: str, label: str, turn_id: str | None) -> Job:
        self._validate_session_id(session_id)
        job_id = f"job_{uuid.uuid4().hex[:12]}"
        return Job(
            session_id=session_id,
            job_id=job_id,
            label=label[:200],
            command=command[:16_384],
            cwd=cwd[:4096],
            started_at=time.time(),
        )

    def _ensure_capacity(self, session_id: str) -> None:
        self._validate_session_id(session_id)
        jobs = [job for (sid, _), job in self._jobs.items() if sid == session_id]
        while len(jobs) >= self.max_jobs:
            candidates = sorted(
                (job for job in jobs if job.state == "exited"),
                key=lambda job: (job.ended_at or job.started_at, job.job_id),
            )
            if not candidates:
                raise RuntimeError(f"This session already has {self.max_jobs} active jobs.")
            self._prune(candidates[0])
            jobs.remove(candidates[0])

    def _prune(self, job: Job) -> None:
        self._append_tombstone(job)
        if job.persistent:
            jobs_dir = self._jobs_dir(job.session_id)
            spill = self._spill_path(job)
            try:
                self._check_safe_file(spill, jobs_dir)
                spill.unlink(missing_ok=True)
            except (OSError, ValueError) as err:
                logger.warning("Could not remove old job spill %s: %s", spill, err)
        self._jobs.pop((job.session_id, job.job_id), None)

    def _append_tombstone(self, job: Job) -> None:
        if not job.persistent:
            return
        row = self._snapshot(job)
        row["state"] = "pruned"
        self._append_index_row(job.session_id, row)

    def _persist_snapshot(self, job: Job) -> None:
        self._append_index_row(job.session_id, self._snapshot(job))

    def _append_index(self, job: Job) -> None:
        if job.persistent:
            self._persist_snapshot(job)

    def _append_progress(self, job: Job) -> None:
        """Journal only changing output counters at the stream rate; full metadata is written at lifecycle edges."""
        if job.persistent:
            self._append_index_row(
                job.session_id,
                {
                    "job_id": job.job_id,
                    "state": job.state,
                    "output_bytes": job.output_bytes,
                    "spill_base_offset": job.spill_base_offset,
                },
            )

    def _append_index_row(self, session_id: str, row: dict[str, Any]) -> None:
        jobs_dir = self._jobs_dir(session_id)
        index = jobs_dir / _INDEX_NAME
        self._check_safe_file(index, jobs_dir)
        encoded = (json.dumps(row, separators=(",", ":"), ensure_ascii=False) + "\n").encode("utf-8")
        fd = _open_regular_file(index, os.O_APPEND | os.O_CREAT | os.O_WRONLY, 0o600)
        try:
            _write_all(fd, encoded)
            os.fsync(fd)
            index_size = os.fstat(fd).st_size
        finally:
            os.close(fd)
        if index_size > _INDEX_COMPACT_BYTES:
            self._compact_index(session_id, index, jobs_dir)

    def _compact_index(self, session_id: str, index: Path, jobs_dir: Path) -> None:
        """Keep only each retained job's latest snapshot after the append-only index grows large."""
        self._check_safe_file(index, jobs_dir)
        latest: dict[str, dict[str, Any]] = {}
        try:
            content = _read_regular_file(index).decode("utf-8", errors="replace")
            for line in content.splitlines():
                try:
                    row = json.loads(line)
                except json.JSONDecodeError:
                    continue
                if not isinstance(row, dict):
                    continue
                job_id = str(row.get("job_id", ""))
                if _JOB_ID.fullmatch(job_id):
                    merged = dict(latest.get(job_id, {}))
                    merged.update(row)
                    latest[job_id] = merged
        except (OSError, ValueError) as err:
            logger.warning("Could not compact job index for %s: %s", session_id, err)
            return
        latest = {job_id: row for job_id, row in latest.items() if row.get("state") != "pruned"}
        temp = jobs_dir / f".{_INDEX_NAME}.{uuid.uuid4().hex}.tmp"
        fd = _open_regular_file(temp, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
        try:
            for job_id in sorted(latest):
                encoded = (json.dumps(latest[job_id], separators=(",", ":"), ensure_ascii=False) + "\n").encode("utf-8")
                _write_all(fd, encoded)
            os.fsync(fd)
        finally:
            os.close(fd)
        try:
            self._check_safe_file(index, jobs_dir)
            os.replace(temp, index)
        except (OSError, ValueError) as err:
            logger.warning("Could not replace compacted job index for %s: %s", session_id, err)
        finally:
            temp.unlink(missing_ok=True)

    def _load_snapshots(self) -> None:
        harness_dir = self.workspace / ".harness"
        sessions_dir = harness_dir / "sessions"
        if (
            harness_dir.is_symlink()
            or not harness_dir.is_dir()
            or sessions_dir.is_symlink()
            or not sessions_dir.is_dir()
        ):
            return
        try:
            sessions_root = sessions_dir.resolve()
            if not sessions_root.is_relative_to(self.workspace):
                logger.warning("Skipping jobs outside the workspace sessions root")
                return
            candidates = list(sessions_root.glob("session_*/jobs/" + _INDEX_NAME))
        except OSError as err:
            logger.warning("Could not scan job state: %s", err)
            return
        for index in candidates:
            session_path = index.parent.parent
            jobs_path = index.parent
            session_id = session_path.name
            if (
                not _SESSION_ID.fullmatch(session_id)
                or harness_dir.is_symlink()
                or sessions_dir.is_symlink()
                or session_path.is_symlink()
                or jobs_path.is_symlink()
                or index.is_symlink()
            ):
                continue
            try:
                jobs_dir = jobs_path.resolve()
                session_dir = session_path.resolve()
                if (
                    not session_dir.is_relative_to(sessions_root)
                    or not jobs_dir.is_relative_to(session_dir)
                    or not index.resolve().is_relative_to(jobs_dir)
                ):
                    continue
                self._check_safe_file(index, jobs_path)
                index_stat = index.lstat()
                content = _read_regular_file(index).decode("utf-8", errors="replace")
                latest: dict[str, dict[str, Any]] = {}
                for line in content.splitlines():
                    try:
                        row = json.loads(line)
                    except json.JSONDecodeError:
                        continue
                    if not isinstance(row, dict):
                        continue
                    job_id = str(row.get("job_id", ""))
                    if _JOB_ID.fullmatch(job_id):
                        merged = dict(latest.get(job_id, {}))
                        merged.update(row)
                        latest[job_id] = merged
                for job_id, row in latest.items():
                    if row.get("state") == "pruned":
                        continue
                    state = str(row.get("state") or "running")
                    if state not in {"starting", "running", "exited"}:
                        continue
                    job = Job(
                        session_id=session_id,
                        job_id=job_id,
                        label=str(row.get("label") or job_id)[:200],
                        command=str(row.get("command") or "")[:16_384],
                        cwd=str(row.get("cwd") or self.workspace)[:4096],
                        started_at=float(row.get("started_at") or index_stat.st_mtime),
                        state=state,
                        pid=row.get("pid"),
                        exit_code=row.get("exit_code"),
                        reason=row.get("reason"),
                        ended_at=row.get("ended_at"),
                        duration_ms=row.get("duration_ms"),
                        output_bytes=max(0, int(row.get("output_bytes") or 0)),
                        spill_base_offset=max(0, int(row.get("spill_base_offset") or 0)),
                        reported=bool(row.get("reported", False)),
                        started_recorded=bool(row.get("started_recorded", True)),
                        exited_recorded=bool(row.get("exited_recorded", True)),
                        sandbox=row.get("sandbox") if isinstance(row.get("sandbox"), dict) else None,
                    )
                    spill = self._spill_path(job)
                    try:
                        self._check_safe_file(spill, jobs_path)
                        raw = _read_regular_file(spill)
                    except FileNotFoundError:
                        raw = None
                    except (OSError, ValueError) as err:
                        logger.warning("Could not restore output for job %s: %s", job_id, err)
                        continue
                    if raw is not None:
                        job.output_bytes = max(job.output_bytes, job.spill_base_offset + len(raw))
                        job.spill_base_offset = max(0, job.output_bytes - len(raw))
                        job.ring[:] = raw[-self.ring_bytes :]
                    self._jobs[(session_id, job_id)] = job
            except (OSError, ValueError, TypeError) as err:
                logger.warning("Could not restore job state from %s: %s", index, err)

    def _snapshot(self, job: Job) -> dict[str, Any]:
        return {
            "job_id": job.job_id,
            "session_id": job.session_id,
            "state": job.state,
            "label": job.label,
            "command": job.command,
            "cwd": job.cwd,
            "pid": job.pid,
            "started_at": job.started_at,
            "ended_at": job.ended_at,
            "exit_code": job.exit_code,
            "reason": job.reason,
            "duration_ms": job.duration_ms,
            "output_bytes": job.output_bytes,
            "spill_base_offset": job.spill_base_offset,
            "reported": job.reported,
            "started_recorded": job.started_recorded,
            "exited_recorded": job.exited_recorded,
            "sandbox": job.sandbox,
        }

    def _summary(self, job: Job) -> dict[str, Any]:
        return {
            **self._snapshot(job),
            "running": job.state in {"starting", "running"},
            "tail": self._tail_text(job, 20),
            "spill_path": self._relative_spill_path(job) if job.persistent else None,
        }

    def _legacy_summary(self, job: Job) -> dict[str, Any]:
        return {
            "task_id": job.job_id,
            "job_id": job.job_id,
            "label": job.label,
            "command": job.command,
            "completed": job.state == "exited",
            "running": job.state in {"starting", "running"},
            "exit_code": job.exit_code,
            "reason": job.reason,
            "sandbox": job.sandbox,
            "tail": self._tail_text(job, 10),
            "state": job.state,
        }

    def _tail_text(self, job: Job, lines: int) -> str:
        return "".join(bytes(job.ring).decode("utf-8", errors="replace").splitlines(keepends=True)[-lines:])

    def _spill_path(self, job: Job) -> Path:
        return self._jobs_dir(job.session_id) / f"{job.job_id}.log"

    def _relative_spill_path(self, job: Job) -> str:
        return f".harness/sessions/{job.session_id}/jobs/{job.job_id}.log"

    def _jobs_dir(self, session_id: str) -> Path:
        self._validate_session_id(session_id)
        workspace = self.workspace.resolve()
        harness_dir = workspace / ".harness"
        sessions_dir = harness_dir / "sessions"
        session_dir = sessions_dir / session_id
        jobs_dir = session_dir / "jobs"
        chain = (
            (harness_dir, workspace, "Harness data directory resolves outside the workspace"),
            (sessions_dir, harness_dir, "Session directory resolves outside .harness"),
            (session_dir, sessions_dir, "Session path resolves outside the sessions directory"),
            (jobs_dir, session_dir, "Jobs directory resolves outside the session directory"),
        )
        for directory, parent, message in chain:
            if directory.is_symlink():
                raise ValueError(f"Refusing symlinked job directory: {directory.name}")
            directory.mkdir(exist_ok=True)
            resolved = directory.resolve()
            expected_parent = parent.resolve()
            if not resolved.is_relative_to(expected_parent):
                raise ValueError(message)
        return jobs_dir

    @staticmethod
    def _check_safe_file(path: Path, parent: Path) -> None:
        if parent.is_symlink():
            raise ValueError(f"Refusing symlinked job data directory: {parent.name}")
        try:
            info = path.lstat()
        except FileNotFoundError:
            info = None
        if info is not None:
            if stat.S_ISLNK(info.st_mode):
                raise ValueError(f"Refusing symlinked job data file: {path.name}")
            if not stat.S_ISREG(info.st_mode):
                raise ValueError(f"Refusing non-regular job data file: {path.name}")
            if getattr(info, "st_nlink", 1) > 1:
                raise ValueError(f"Refusing multiply-linked job data file: {path.name}")
        parent_real = parent.resolve()
        if not path.resolve(strict=False).is_relative_to(parent_real):
            raise ValueError(f"Job data file escapes its session directory: {path.name}")

    @staticmethod
    def _validate_session_id(session_id: str) -> None:
        if not isinstance(session_id, str) or not _SESSION_ID.fullmatch(session_id):
            raise ValueError(f"Invalid session id for jobs: {session_id!r}")

    def _find(self, session_id: str, job_id: str) -> Job | None:
        try:
            self._validate_session_id(session_id)
        except ValueError:
            return None
        if not _JOB_ID.fullmatch(job_id):
            return None
        return self._jobs.get((session_id, job_id))

    def _find_any(self, job_id: str) -> Job | None:
        if not _JOB_ID.fullmatch(job_id):
            return None
        matches = [job for (_, known_id), job in self._jobs.items() if known_id == job_id]
        return matches[0] if len(matches) == 1 else None

    def _queue_for(self, session_id: str) -> asyncio.Queue[JobWakeup]:
        self._validate_session_id(session_id)
        return self._wakeup_queues.setdefault(session_id, asyncio.Queue())

    def _sandbox_of(self, process: Process) -> dict[str, Any] | None:
        value = getattr(process, "sandbox", None)
        if value is None:
            return None
        if isinstance(value, dict):
            return dict(value)
        if hasattr(value, "to_dict"):
            try:
                return value.to_dict()
            except Exception:
                return {"description": str(value)}
        return {"description": str(value)}


class _SessionJobsView:
    """Bound ToolContext view: callers cannot accidentally read or control another session's jobs."""

    def __init__(self, registry: JobRegistry, session_id: str, turn_id: str | None) -> None:
        self.registry = registry
        self.session_id = session_id
        self.turn_id = turn_id

    async def start(self, executor: Executor, req: ExecRequest, label: str = "") -> dict[str, Any]:
        return await self.registry.start(self.session_id, self.turn_id, executor, req, label)

    def register_process(
        self,
        process: Process,
        label: str = "",
        *,
        initial_output: str = "",
    ) -> str:
        return self.registry.register_process(
            self.session_id,
            self.turn_id,
            process,
            label,
            initial_output=initial_output,
        )

    def get(self, job_id: str) -> dict[str, Any] | None:
        return self.registry.get(self.session_id, job_id)

    def list(self) -> list[dict[str, Any]]:
        return self.registry.list(self.session_id)

    async def output(
        self,
        job_id: str,
        *,
        tail_lines: int = 50,
        since_offset: int | None = None,
    ) -> dict[str, Any] | None:
        return await self.registry.output(
            self.session_id,
            job_id,
            tail_lines=tail_lines,
            since_offset=since_offset,
        )

    async def kill(self, job_id: str) -> bool:
        return await self.registry.kill(self.session_id, job_id)

    async def send_input(self, job_id: str, text: str) -> bool:
        return await self.registry.send_input(self.session_id, job_id, text)

    async def wait(self, job_id: str, timeout_s: float | None = None) -> int | None:
        return await self.registry.wait_task(self.session_id, job_id, timeout_s)

    # The old TaskContext names remain session-scoped for ``manage_task`` during its compatibility window.
    def get_task(self, job_id: str) -> dict[str, Any] | None:
        return self.registry.get(self.session_id, job_id)

    def list_tasks(self) -> list[dict[str, Any]]:
        return self.registry.list_tasks(self.session_id)

    async def kill_task(self, job_id: str) -> bool:
        return await self.kill(job_id)

    async def send_task_input(self, job_id: str, text: str) -> bool:
        return await self.send_input(job_id, text)

    async def wait_task(self, job_id: str, timeout_s: float | None = None) -> int | None:
        return await self.wait(job_id, timeout_s)
