"""
Safe Shell Command Supervisor & Background Task Manager.
Enforces PAGER=cat, env scrubbing, DEVNULL stdin, chunked stream reading,
transitions to background tasks via WaitMsBeforeAsync, and manages task lifecycle (kill, status, send_input).
"""
from __future__ import annotations

import asyncio
import os
import re
import signal
import uuid
from collections.abc import Callable
from dataclasses import dataclass, field
from datetime import UTC, datetime
from pathlib import Path
from typing import Any


@dataclass
class BackgroundTask:
    task_id: str
    command: str
    cwd: Path
    process: asyncio.subprocess.Process
    start_time: str = field(
        default_factory=lambda: datetime.now(UTC).isoformat()
    )
    status: str = "running"
    exit_code: int | None = None
    killed: bool = False
    stdout_chunks: list[str] = field(default_factory=list)
    stderr_chunks: list[str] = field(default_factory=list)

    @property
    def stdout(self) -> str:
        return "".join(self.stdout_chunks)

    @property
    def stderr(self) -> str:
        return "".join(self.stderr_chunks)


class CommandExecutor:
    def __init__(self, on_task_completed: Callable[[BackgroundTask], Any] | None = None):
        self.tasks: dict[str, BackgroundTask] = {}
        self.on_task_completed = on_task_completed

    @staticmethod
    def _scrubbed_env() -> dict[str, str]:
        """
        Creates a sanitized subprocess environment stripping sensitive keys:
        *_KEY, *_TOKEN, *_SECRET, *_PASSWORD, *_CREDENTIAL, *_PRIVATE_KEY,
        GITHUB_TOKEN, GH_TOKEN, AWS_*, etc.
        """
        sensitive_pattern = re.compile(
            r"(API_KEY|_TOKEN|SECRET|PASSWORD|CREDENTIAL|PRIVATE_KEY)",
            re.IGNORECASE,
        )
        env = {}
        for k, v in os.environ.items():
            if sensitive_pattern.search(k):
                continue
            if k in ("GITHUB_TOKEN", "GH_TOKEN") or k.startswith("AWS_"):
                continue
            env[k] = v

        env["PAGER"] = "cat"
        env["GIT_PAGER"] = "cat"
        env["CI"] = "1"
        env["HARNESS_AGENT"] = "1"
        env["TERM"] = "dumb"
        return env

    def _prune_tasks(self) -> None:
        """Prunes finished tasks keeping only the last 200 entries."""
        finished_ids = [
            tid for tid, t in self.tasks.items()
            if t.status in ("completed", "failed", "killed")
        ]
        if len(finished_ids) > 200:
            to_remove = finished_ids[: len(finished_ids) - 200]
            for tid in to_remove:
                self.tasks.pop(tid, None)

    async def run(
        self,
        command: str,
        cwd: Path,
        wait_ms_before_async: int = 5000,
        is_daemon: bool = False,
        interactive: bool = False,
    ) -> dict[str, Any]:
        """
        Executes a command. If it finishes within wait_ms_before_async, returns
        synchronously with stdout and exit_code. If it continues running, transitions
        into a background task and returns task_id.
        """
        env = self._scrubbed_env()

        cwd = Path(cwd).resolve()
        if not cwd.is_dir():
            raise FileNotFoundError(f"Working directory does not exist: {cwd}")

        stdin_mode = asyncio.subprocess.PIPE if interactive else asyncio.subprocess.DEVNULL

        proc = await asyncio.create_subprocess_shell(
            command,
            cwd=str(cwd),
            env=env,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
            stdin=stdin_mode,
            start_new_session=True,
            limit=4 * 1024 * 1024,
        )

        task_id = f"task_{uuid.uuid4().hex[:8]}"
        bg_task = BackgroundTask(
            task_id=task_id,
            command=command,
            cwd=cwd,
            process=proc,
        )
        self.tasks[task_id] = bg_task

        # Launch reader tasks
        stdout_task = asyncio.create_task(self._read_stream(proc.stdout, bg_task.stdout_chunks))
        stderr_task = asyncio.create_task(self._read_stream(proc.stderr, bg_task.stderr_chunks))

        # Wait up to wait_ms_before_async
        wait_seconds = wait_ms_before_async / 1000.0
        try:
            exit_code = await asyncio.wait_for(proc.wait(), timeout=wait_seconds)
            bg_task.exit_code = exit_code
            if bg_task.killed or bg_task.status == "killed":
                bg_task.status = "killed"
            else:
                bg_task.status = "completed" if exit_code == 0 else "failed"
            await asyncio.gather(stdout_task, stderr_task, return_exceptions=True)
            self._prune_tasks()

            return {
                "status": bg_task.status,
                "exit_code": exit_code,
                "stdout": bg_task.stdout,
                "stderr": bg_task.stderr,
                "is_background": False,
            }
        except TimeoutError:
            # Command still running -> transition to background task
            asyncio.create_task(
                self._monitor_background_task(bg_task, stdout_task, stderr_task)
            )
            return {
                "status": "running_in_background",
                "task_id": task_id,
                "is_background": True,
                "message": f"Command running in background with Task ID: {task_id}",
            }

    async def _read_stream(
        self, stream: asyncio.StreamReader | None, chunks: list[str], max_bytes: int = 10 * 1024 * 1024, max_lines: int = 10000
    ) -> None:
        if not stream:
            return
        total_bytes = 0
        total_lines = 0
        chunk_size = 65536  # 64 KiB chunks
        truncated = False

        while True:
            chunk = await stream.read(chunk_size)
            if not chunk:
                break
            text = chunk.decode("utf-8", errors="replace")
            lines_in_chunk = text.count("\n")
            if total_bytes + len(chunk) <= max_bytes and total_lines + lines_in_chunk <= max_lines:
                chunks.append(text)
                total_bytes += len(chunk)
                total_lines += lines_in_chunk
            else:
                if not truncated:
                    chunks.append("\n[OUTPUT TRUNCATED: Stream buffer limit reached]\n")
                    truncated = True

    async def _monitor_background_task(
        self,
        task: BackgroundTask,
        stdout_task: asyncio.Task,
        stderr_task: asyncio.Task,
    ) -> None:
        exit_code = await task.process.wait()
        task.exit_code = exit_code
        if task.killed or task.status == "killed":
            task.status = "killed"
        else:
            task.status = "completed" if exit_code == 0 else "failed"
        await asyncio.gather(stdout_task, stderr_task, return_exceptions=True)
        self._prune_tasks()

        if self.on_task_completed:
            try:
                res = self.on_task_completed(task)
                if asyncio.iscoroutine(res):
                    await res
            except Exception:
                pass

    def get_task(self, task_id: str) -> BackgroundTask | None:
        return self.tasks.get(task_id)

    def list_tasks(self) -> list[dict[str, Any]]:
        return [
            {
                "task_id": t.task_id,
                "command": t.command,
                "status": t.status,
                "start_time": t.start_time,
                "exit_code": t.exit_code,
            }
            for t in self.tasks.values()
        ]

    async def kill_task(self, task_id: str) -> bool:
        task = self.tasks.get(task_id)
        if not task or task.status != "running":
            return False
        task.killed = True
        task.status = "killed"
        try:
            current_pgrp = os.getpgrp() if hasattr(os, "getpgrp") else -1
            if hasattr(os, "killpg") and task.process.pid:
                try:
                    pgid = os.getpgid(task.process.pid)
                    if pgid != current_pgrp and pgid > 1:
                        os.killpg(pgid, signal.SIGTERM)
                    else:
                        task.process.terminate()
                except Exception:
                    task.process.terminate()
            else:
                task.process.terminate()

            await asyncio.sleep(0.2)
            if task.process.returncode is None:
                if hasattr(os, "killpg") and task.process.pid:
                    try:
                        pgid = os.getpgid(task.process.pid)
                        if pgid != current_pgrp and pgid > 1:
                            os.killpg(pgid, signal.SIGKILL)
                        else:
                            task.process.kill()
                    except Exception:
                        task.process.kill()
                else:
                    task.process.kill()
            return True
        except Exception:
            return False

    async def send_input(self, task_id: str, input_text: str) -> bool:
        task = self.tasks.get(task_id)
        if not task or task.status != "running" or not task.process.stdin:
            return False
        try:
            task.process.stdin.write(input_text.encode("utf-8") + b"\n")
            await task.process.stdin.drain()
            return True
        except Exception:
            return False
