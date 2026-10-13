"""
Safe Shell Command Supervisor & Background Task Manager.
Enforces PAGER=cat, handles execution timeouts, transitions to background tasks
via WaitMsBeforeAsync, and manages task lifecycle (kill, status, send_input).
"""
from __future__ import annotations

import asyncio
from dataclasses import dataclass, field
from datetime import datetime, timezone
import os
from pathlib import Path
import signal
from typing import Any, Callable, Dict, List, Optional
import uuid


@dataclass
class BackgroundTask:
    task_id: str
    command: str
    cwd: Path
    process: asyncio.subprocess.Process
    start_time: str = field(
        default_factory=lambda: datetime.now(timezone.utc).isoformat()
    )
    status: str = "running"
    exit_code: Optional[int] = None
    stdout_chunks: List[str] = field(default_factory=list)
    stderr_chunks: List[str] = field(default_factory=list)

    @property
    def stdout(self) -> str:
        return "".join(self.stdout_chunks)

    @property
    def stderr(self) -> str:
        return "".join(self.stderr_chunks)


class CommandExecutor:
    def __init__(self, on_task_completed: Optional[Callable[[BackgroundTask], Any]] = None):
        self.tasks: Dict[str, BackgroundTask] = {}
        self.on_task_completed = on_task_completed

    async def run(
        self,
        command: str,
        cwd: Path,
        wait_ms_before_async: int = 5000,
        is_daemon: bool = False,
    ) -> Dict[str, Any]:
        """
        Executes a command. If it finishes within wait_ms_before_async, returns
        synchronously with stdout and exit_code. If it continues running, transitions
        into a background task and returns task_id.
        """
        env = dict(os.environ)
        env["PAGER"] = "cat"
        env["GIT_PAGER"] = "cat"
        env["CI"] = "1"

        cwd = Path(cwd)
        cwd.mkdir(parents=True, exist_ok=True)

        proc = await asyncio.create_subprocess_shell(
            command,
            cwd=str(cwd),
            env=env,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
            stdin=asyncio.subprocess.PIPE,
            start_new_session=True,
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
            bg_task.status = "completed" if exit_code == 0 else "failed"
            await asyncio.gather(stdout_task, stderr_task, return_exceptions=True)

            return {
                "status": bg_task.status,
                "exit_code": exit_code,
                "stdout": bg_task.stdout,
                "stderr": bg_task.stderr,
                "is_background": False,
            }
        except asyncio.TimeoutError:
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
        self, stream: Optional[asyncio.StreamReader], chunks: List[str], max_chunks: int = 10000
    ) -> None:
        if not stream:
            return
        total_bytes = 0
        max_bytes = 10 * 1024 * 1024  # 10 MB buffer limit
        while True:
            line = await stream.readline()
            if not line:
                break
            if len(chunks) < max_chunks and total_bytes < max_bytes:
                chunks.append(line.decode("utf-8", errors="replace"))
                total_bytes += len(line)
            elif len(chunks) == max_chunks:
                chunks.append("\n[OUTPUT TRUNCATED: Stream buffer limit reached]\n")

    async def _monitor_background_task(
        self,
        task: BackgroundTask,
        stdout_task: asyncio.Task,
        stderr_task: asyncio.Task,
    ) -> None:
        exit_code = await task.process.wait()
        task.exit_code = exit_code
        task.status = "completed" if exit_code == 0 else "failed"
        await asyncio.gather(stdout_task, stderr_task, return_exceptions=True)

        if self.on_task_completed:
            try:
                res = self.on_task_completed(task)
                if asyncio.iscoroutine(res):
                    await res
            except Exception:
                pass

    def get_task(self, task_id: str) -> Optional[BackgroundTask]:
        return self.tasks.get(task_id)

    def list_tasks(self) -> List[Dict[str, Any]]:
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
            task.status = "killed"
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
