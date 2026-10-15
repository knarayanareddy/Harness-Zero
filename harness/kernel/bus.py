"""
Asynchronous Fan-out EventBus with Bounded Queues and Delta Coalescing.
Implements the core event distribution semantics defined in architecture §6.
"""
from __future__ import annotations

import asyncio
import logging
import uuid

from harness.sdk.events import Event

logger = logging.getLogger("harness.kernel.bus")

DELTA_EVENT_TYPES = frozenset({
    "model.text.delta",
    "model.thinking.delta",
    "tool.output.delta",
})


class Subscriber:
    def __init__(
        self,
        sub_id: str,
        types: frozenset[str] | None = None,
        interactive: bool = True,
        max_queue_size: int = 1000,
    ):
        self.id = sub_id
        self.types = types
        self.interactive = interactive
        self.max_queue_size = max_queue_size
        self.queue: asyncio.Queue[Event] = asyncio.Queue(maxsize=max_queue_size)
        self.lagging = False
        self._last_delta: Event | None = None

    def matches(self, event_type: str) -> bool:
        if self.types is None:
            return True
        return event_type in self.types

    async def get(self) -> Event:
        """Retrieves the next event from the queue."""
        event = await self.queue.get()
        if self._last_delta is event:
            self._last_delta = None
        return event

    def get_nowait(self) -> Event:
        """Retrieves the next event synchronously without blocking."""
        event = self.queue.get_nowait()
        if self._last_delta is event:
            self._last_delta = None
        return event

    def empty(self) -> bool:
        return self.queue.empty()

    def qsize(self) -> int:
        return self.queue.qsize()


class EventBus:
    def __init__(self, default_queue_size: int = 1000):
        self.default_queue_size = default_queue_size
        self._subscribers: dict[str, Subscriber] = {}

    def subscribe(
        self,
        types: list[str] | set[str] | frozenset[str] | None = None,
        interactive: bool = True,
        max_queue_size: int | None = None,
    ) -> Subscriber:
        """Creates and registers a new subscriber with a bounded event queue."""
        sub_id = f"sub_{uuid.uuid4().hex[:8]}"
        filter_types = frozenset(types) if types is not None else None
        qsize = max_queue_size or self.default_queue_size
        sub = Subscriber(
            sub_id=sub_id,
            types=filter_types,
            interactive=interactive,
            max_queue_size=qsize,
        )
        self._subscribers[sub_id] = sub
        return sub

    def unsubscribe(self, sub: Subscriber | str) -> None:
        """Removes a subscriber by instance or ID."""
        sub_id = sub.id if isinstance(sub, Subscriber) else sub
        self._subscribers.pop(sub_id, None)

    def has_interactive_subscribers(self) -> bool:
        """Checks if any active subscriber declared interactive=True."""
        return any(s.interactive for s in self._subscribers.values())

    def publish(self, event: Event) -> None:
        """
        Fans out event to all matching subscribers.
        Coalesces consecutive delta events when a subscriber queue approaches capacity.
        Never drops non-delta lifecycle events.
        """
        is_delta = event.type in DELTA_EVENT_TYPES
        original = event
        for sub in list(self._subscribers.values()):
            event = original
            if not sub.matches(event.type):
                continue

            # Delta coalescing logic:
            # If the incoming event is a delta and can coalesce with sub._last_delta,
            # merge the text payload in place to conserve queue slots.
            if is_delta and sub._last_delta is not None and sub._last_delta.type == event.type:
                # Verify delta stream correlation (e.g. same call_id or stream)
                same_call = sub._last_delta.data.get("call_id") == event.data.get("call_id")
                same_stream = sub._last_delta.data.get("stream") == event.data.get("stream")
                same_turn = sub._last_delta.turn_id == event.turn_id
                if same_call and same_stream and same_turn:
                    prev_text = sub._last_delta.data.get("text", "")
                    new_text = event.data.get("text", "")
                    # `_last_delta` is this subscriber's private copy (see below), so the
                    # merge never touches the Event instance shared with other subscribers.
                    sub._last_delta.data["text"] = prev_text + new_text
                    continue

            if is_delta:
                # Each subscriber queues its own copy of a delta. Coalescing mutates the
                # queued copy in place; mutating the shared instance would append every
                # later chunk once per subscriber (text duplicated N times in the UI).
                event = Event(
                    type=event.type,
                    data=dict(event.data),
                    id=event.id,
                    ts=event.ts,
                    session_id=event.session_id,
                    turn_id=event.turn_id,
                )

            # If queue is full:
            if sub.queue.full():
                sub.lagging = True
                if is_delta:
                    # Drop older or current delta to protect critical lifecycle events
                    continue
                else:
                    # Non-delta events MUST NEVER be dropped.
                    # Force room by popping an older delta if available, otherwise grow
                    try:
                        # Attempt to drain one delta from the queue
                        temp_drained: list[Event] = []
                        evicted = False
                        while not sub.queue.empty():
                            item = sub.queue.get_nowait()
                            if not evicted and item.type in DELTA_EVENT_TYPES:
                                evicted = True
                                continue
                            temp_drained.append(item)
                        for item in temp_drained:
                            sub.queue.put_nowait(item)
                    except Exception:
                        pass

            try:
                sub.queue.put_nowait(event)
                if is_delta:
                    sub._last_delta = event
            except asyncio.QueueFull:
                # If still full for a non-delta, increase capacity temporarily
                sub.queue._maxsize = sub.queue.qsize() + 100
                sub.queue.put_nowait(event)
                logger.warning(
                    f"[EventBus] Subscriber '{sub.id}' is lagging. Queue expanded to preserve non-delta event '{event.type}'"
                )
