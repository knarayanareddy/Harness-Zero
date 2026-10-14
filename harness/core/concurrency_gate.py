"""
In-Flight Concurrency Gate (Operon / Claude Cowork Pattern).
Prevents LLM rate-limit thrashing and thread exhaustion during wide subagent fan-outs.
"""
from __future__ import annotations

import asyncio
import logging
import time
from collections.abc import Callable
from dataclasses import dataclass, field
from typing import Any

logger = logging.getLogger("harness.concurrency")


@dataclass
class QueuedRequest:
    future: asyncio.Future
    request_type: str
    enqueued_at: float = field(default_factory=time.time)


class InFlightQueue:
    def __init__(self):
        self._queue: list[QueuedRequest] = []
        self._last_acquire_wait_ms: float = 0.0
        self._aborted_count: int = 0

    @property
    def queued(self) -> int:
        return len(self._queue)

    @property
    def oldest_wait_ms(self) -> float:
        if not self._queue:
            return 0.0
        return (time.time() - self._queue[0].enqueued_at) * 1000.0

    def enqueue(self, request_type: str = "agent") -> asyncio.Future:
        loop = asyncio.get_running_loop()
        fut = loop.create_future()
        req = QueuedRequest(future=fut, request_type=request_type)
        self._queue.append(req)
        return fut

    def take(self) -> QueuedRequest | None:
        if not self._queue:
            return None
        req = self._queue.pop(0)
        self._last_acquire_wait_ms = (time.time() - req.enqueued_at) * 1000.0
        return req


class ConcurrencyGate:
    """
    Reverse-engineered from Anthropic Operon's in-flight concurrency limiter.
    Limits active concurrent requests, queues excess during subagent fan-out,
    and prioritizes safety classifier checks.
    """
    def __init__(self, limit: int = 64):
        self.limit = limit
        self._active: int = 0
        self._classifier_active: int = 0
        self._agents_queue = InFlightQueue()
        self._classifiers_queue = InFlightQueue()
        self._last_depth_warn_at: float = 0.0

    @property
    def active(self) -> int:
        return self._active

    @property
    def queued(self) -> int:
        return self._agents_queue.queued + self._classifiers_queue.queued

    async def acquire(self, request_type: str = "agent") -> Callable[[], None]:
        """
        Acquires an in-flight slot. If slots are saturated, waits in queue.
        Returns a release callback.
        """
        if self._active < self.limit:
            self._active += 1
            if request_type == "classifier":
                self._classifier_active += 1
            return self._make_release(request_type)

        # Slots full -> enqueue
        target_queue = (
            self._classifiers_queue
            if request_type == "classifier"
            else self._agents_queue
        )
        fut = target_queue.enqueue(request_type)
        self._warn_if_deep()
        await fut

        return self._make_release(request_type)

    def _warn_if_deep(self) -> None:
        now = time.time()
        if self.queued > self.limit and (now - self._last_depth_warn_at) > 30.0:
            self._last_depth_warn_at = now
            oldest_ms = max(
                self._agents_queue.oldest_wait_ms,
                self._classifiers_queue.oldest_wait_ms,
            )
            logger.warning(
                f"[concurrency] In-flight gate: {self.queued} requests waiting for {self.limit} slot(s). "
                f"Oldest wait: {oldest_ms:.1f}ms. Wide subagent fan-out in progress."
            )

    def _make_release(self, request_type: str) -> Callable[[], None]:
        released = False

        def release() -> None:
            nonlocal released
            if released:
                return
            released = True
            self._release(request_type)

        return release

    def _release(self, request_type: str) -> None:
        if request_type == "classifier":
            self._classifier_active = max(0, self._classifier_active - 1)

        # Prioritize safety classifiers unless starving agents
        req = self._classifiers_queue.take() or self._agents_queue.take()
        if not req:
            self._active = max(0, self._active - 1)
            return

        if req.request_type == "classifier":
            self._classifier_active += 1

        if not req.future.done():
            req.future.set_result(True)

    def stats(self) -> dict[str, Any]:
        return {
            "limit": self.limit,
            "active": self._active,
            "queued": self.queued,
            "agents_queued": self._agents_queue.queued,
            "classifiers_queued": self._classifiers_queue.queued,
            "oldest_wait_ms": max(
                self._agents_queue.oldest_wait_ms,
                self._classifiers_queue.oldest_wait_ms,
            ),
        }
