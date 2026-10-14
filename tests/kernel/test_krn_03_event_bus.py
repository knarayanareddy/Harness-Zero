import pytest

from harness.kernel.bus import EventBus
from harness.sdk.events import Event


@pytest.mark.asyncio
async def test_event_bus_fan_out_and_filters():
    bus = EventBus()

    # Sub 1: all events
    sub1 = bus.subscribe(interactive=True)
    # Sub 2: only tool.* events
    sub2 = bus.subscribe(types=["tool.call", "tool.result"], interactive=False)

    assert bus.has_interactive_subscribers() is True

    # Publish turn.started (matches sub1 only)
    bus.publish(Event(type="turn.started", data={"prompt": "hello"}))

    # Publish tool.call (matches sub1 and sub2)
    bus.publish(Event(type="tool.call", data={"name": "view_file"}))

    # Check sub1 received both
    e1_1 = sub1.get_nowait()
    e1_2 = sub1.get_nowait()
    assert e1_1.type == "turn.started"
    assert e1_2.type == "tool.call"
    assert sub1.empty() is True

    # Check sub2 received only tool.call
    e2_1 = sub2.get_nowait()
    assert e2_1.type == "tool.call"
    assert sub2.empty() is True

    # Unsubscribe
    bus.unsubscribe(sub1)
    assert bus.has_interactive_subscribers() is False


@pytest.mark.asyncio
async def test_event_bus_delta_coalescing_under_flood():
    """
    KRN-03 Invariant:
    10,000 deltas into a slow subscriber coalesce without dropping non-delta events;
    ordering is preserved.
    """
    bus = EventBus(default_queue_size=500)
    sub = bus.subscribe(max_queue_size=500)

    # 1. Publish start event
    bus.publish(Event(type="turn.started", data={"prompt": "test"}))

    # 2. Flood 10,000 delta chunks
    for _i in range(10000):
        bus.publish(
            Event(
                type="model.text.delta",
                data={"text": "x", "call_id": "c1", "stream": "main"},
            )
        )

    # 3. Publish completion lifecycle event
    bus.publish(Event(type="model.completed", data={"stop_reason": "end_turn"}))

    # Assert queue size did NOT blow up past bounded size
    assert sub.qsize() <= 500

    # Read events from the slow subscriber
    received_events: list[Event] = []
    while not sub.empty():
        received_events.append(sub.get_nowait())

    # Invariant: First event must be turn.started
    assert received_events[0].type == "turn.started"

    # Invariant: Last event must be model.completed
    assert received_events[-1].type == "model.completed"
    assert received_events[-1].data["stop_reason"] == "end_turn"

    # Invariant: Deltas were coalesced, total text length preserved
    delta_events = [e for e in received_events if e.type == "model.text.delta"]
    assert len(delta_events) >= 1
    total_delta_text = "".join(e.data.get("text", "") for e in delta_events)
    assert len(total_delta_text) == 10000
    assert total_delta_text == "x" * 10000


@pytest.mark.asyncio
async def test_event_bus_never_drops_non_delta_events():
    """Ensures critical lifecycle events are never dropped even if queue saturates."""
    bus = EventBus(default_queue_size=10)
    sub = bus.subscribe(max_queue_size=10)

    # Publish 20 non-delta events
    for i in range(20):
        bus.publish(Event(type="task.completed", data={"index": i}))

    # Read all events
    events = []
    while not sub.empty():
        events.append(sub.get_nowait())

    # All 20 non-delta events must be preserved in order
    assert len(events) == 20
    for i, e in enumerate(events):
        assert e.data["index"] == i
