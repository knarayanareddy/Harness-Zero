"""
BackgroundInbox: reactive background task tracking and asynchronous wake-ups.
Fulfills KRN-08 (§5.6) and absorbs LOOP-04.
"""
from __future__ import annotations

import asyncio
import logging
import time
from dataclasses import dataclass, field
from typing import Any

from harness.kernel.bus import EventBus
from harness.sdk.contracts import Process
from harness.sdk.events import Event, TaskCompleted, TaskStarted
from harness.sdk.types import ExecResult

logger = logging.getLogger(__name__)


@dataclass
class WakeupEvent:
    task_id: str
    exit_code: int
    tail: str
    label: str = ""
    timestamp: float = field(default_factory=time.time)
    session_id: str = ""


@dataclass
class BackgroundTask:
    id: str
    process: Process
    label: str
    created_at: float
    output_lines: list[str] = field(default_factory=list)
    exit_code: int | None = None
    completed: bool = False
    monitor_task: asyncio.Task[None] | None = None
    session_id: str = ""


class BackgroundInbox:
    """
    Monitors running background processes, collects output tails,
    and enqueues reactive WakeupEvents upon process exit.
    """

    def __init__(self, bus: EventBus | None = None, session_id: str = ""):
        self.bus = bus
        self.session_id = session_id
        self._tasks: dict[str, BackgroundTask] = {}
        self._wakeup_queue: asyncio.Queue[WakeupEvent] = asyncio.Queue()
        self._session_wakeup_queues: dict[str, asyncio.Queue[WakeupEvent]] = {}

    def register_task(
        self,
        task_id: str,
        proc: Process,
        label: str = "",
        max_tail_lines: int = 50,
        session_id: str | None = None,
    ) -> BackgroundTask:
        """Registers a background Process to be monitored for exit."""
        bt = BackgroundTask(
            id=task_id,
            process=proc,
            label=label or task_id,
            created_at=time.time(),
            session_id=session_id or self.session_id,
        )
        self._tasks[task_id] = bt
        if self.bus:
            self.bus.publish(
                Event(
                    type="task.started",
                    session_id=bt.session_id or None,
                    data=TaskStarted(task_id=task_id, label=bt.label),
                )
            )

        # Launch background monitor
        bt.monitor_task = asyncio.create_task(
            self._monitor_process(bt, max_tail_lines)
        )
        return bt

    async def _monitor_process(self, bt: BackgroundTask, max_tail_lines: int) -> None:
        """Monitors process output stream until termination, then queues wakeup."""
        try:
            async for _, chunk in bt.process.output():
                for line in chunk.splitlines():
                    bt.output_lines.append(line)
                    if len(bt.output_lines) > max_tail_lines:
                        bt.output_lines.pop(0)

            # The Process contract returns an ExecResult (the status is not the result object itself). Keep
            # accepting integer-returning legacy adapters, but never turn a non-zero subprocess exit into 0.
            exit_code = getattr(bt.process, "exit_code", None)
            if hasattr(bt.process, "wait"):
                res = await bt.process.wait()
                if isinstance(res, ExecResult):
                    exit_code = res.exit_code
                elif isinstance(res, int):
                    exit_code = res
            bt.exit_code = exit_code if exit_code is not None else 0
        except asyncio.CancelledError:
            bt.exit_code = -15
            logger.info(f"Background task '{bt.id}' monitoring cancelled.")
        except Exception as err:
            logger.exception(f"Error monitoring background process '{bt.id}': {err}")
            bt.exit_code = 1
        finally:
            bt.completed = True
            tail_str = "\n".join(bt.output_lines)
            wakeup = WakeupEvent(
                task_id=bt.id,
                exit_code=bt.exit_code,
                tail=tail_str,
                label=bt.label,
                session_id=bt.session_id,
            )
            await self._wakeup_queue.put(wakeup)
            if bt.session_id:
                await self._session_queue(bt.session_id).put(wakeup)

            if self.bus:
                self.bus.publish(
                    Event(
                        type="task.completed",
                        session_id=bt.session_id or None,
                        data=TaskCompleted(
                            task_id=bt.id,
                            label=bt.label,
                            exit_code=bt.exit_code,
                            tail=tail_str,
                        ),
                    )
                )

    async def wait_for_wakeup(
        self,
        timeout_s: float = 600.0,
        session_id: str | None = None,
    ) -> WakeupEvent | None:
        """Awaits the next wake-up, optionally isolated to one session."""
        queue = self._session_queue(session_id) if session_id else self._wakeup_queue
        try:
            async with asyncio.timeout(timeout_s):
                return await queue.get()
        except TimeoutError:
            return None

    def _session_queue(self, session_id: str) -> asyncio.Queue[WakeupEvent]:
        return self._session_wakeup_queues.setdefault(session_id, asyncio.Queue())

    def has_pending(self, session_id: str | None = None) -> bool:
        """Checks if any registered task is actively running, optionally for one session."""
        return any(
            not task.completed and (session_id is None or task.session_id == session_id)
            for task in self._tasks.values()
        )

    def get_status(self, task_id: str, session_id: str | None = None) -> dict[str, Any] | None:
        """Returns status dictionary for a registered task."""
        bt = self._tasks.get(task_id)
        if not bt or (session_id and bt.session_id != session_id):
            return None
        return {
            "task_id": bt.id,
            "label": bt.label,
            "completed": bt.completed,
            "exit_code": bt.exit_code,
            "running": not bt.completed,
            "tail": "\n".join(bt.output_lines[-10:]),
            "sandbox": getattr(bt.process, "sandbox", None),
        }

    def list_tasks(self, session_id: str | None = None) -> list[dict[str, Any]]:
        """Returns summary list of registered tasks, optionally for one session."""
        return [
            {
                "task_id": bt.id,
                "label": bt.label,
                "completed": bt.completed,
                "exit_code": bt.exit_code,
                "running": not bt.completed,
                "sandbox": getattr(bt.process, "sandbox", None),
            }
            for bt in self._tasks.values()
            if session_id is None or bt.session_id == session_id
        ]

    async def send_input(self, task_id: str, text: str, session_id: str | None = None) -> bool:
        """Sends input text to running background task."""
        bt = self._tasks.get(task_id)
        if not bt or bt.completed or (session_id and bt.session_id != session_id):
            return False
        if hasattr(bt.process, "send_input"):
            res = bt.process.send_input(text)
            if asyncio.iscoroutine(res):
                await res
            return True
        return False

    async def wait_task(
        self,
        task_id: str,
        timeout_s: float | None = None,
        session_id: str | None = None,
    ) -> Any:
        """Waits for background task to complete and returns exit code."""
        bt = self._tasks.get(task_id)
        if not bt or (session_id and bt.session_id != session_id):
            return None
        if bt.completed:
            return bt.exit_code
        if bt.monitor_task:
            if timeout_s is not None and timeout_s > 0:
                async with asyncio.timeout(timeout_s):
                    await bt.monitor_task
            else:
                await bt.monitor_task
        return bt.exit_code

    async def kill(self, task_id: str, session_id: str | None = None) -> bool:
        """Terminates a running background task."""
        bt = self._tasks.get(task_id)
        if not bt or bt.completed or (session_id and bt.session_id != session_id):
            return False
        if hasattr(bt.process, "kill"):
            res = bt.process.kill()
            if asyncio.iscoroutine(res):
                await res
        if bt.monitor_task and not bt.monitor_task.done():
            bt.monitor_task.cancel()
        return True
