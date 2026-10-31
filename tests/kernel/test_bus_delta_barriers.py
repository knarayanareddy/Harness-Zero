"""Regression coverage for delivered-event barriers in delta coalescing."""
from __future__ import annotations

import pytest

from harness.kernel.bus import EventBus
from harness.sdk.events import (
    ApprovalResolved,
    Event,
    ModelCompleted,
    ModelTextDelta,
    ModelThinkingDelta,
    ToolCallResult,
    ToolOutputDelta,
    TurnCancelled,
    TurnCompleted,
    validate_event,
)
from harness.sdk.types import Usage


def _delta(text: str, *, session: str = "session-A", turn: str = "turn-A") -> Event:
    return Event(
        type=ModelTextDelta.EVENT_TYPE,
        data=ModelTextDelta(text=text),
        session_id=session,
        turn_id=turn,
    )


def _barrier(payload, *, session: str = "session-A", turn: str = "turn-A") -> Event:
    event = Event(
        type=payload.EVENT_TYPE,
        data=payload,
        session_id=session,
        turn_id=turn,
    )
    assert validate_event(event) == []
    return event


def _queued(subscriber) -> list[Event]:
    events = []
    while not subscriber.empty():
        events.append(subscriber.get_nowait())
    return events


@pytest.mark.parametrize(
    "payload",
    [
        ModelCompleted(stop_reason="end_turn", usage=Usage()),
        TurnCompleted(stop_reason="end_turn", usage=Usage(), duration_ms=1.0, tool_calls=0),
        TurnCancelled(reason="user requested cancellation"),
        ToolCallResult(
            call_id="call-A",
            name="read_file",
            is_error=False,
            duration_ms=1.0,
            preview="read complete",
        ),
        ApprovalResolved(approval_id="approval-A", approved=True, by="user"),
    ],
)
def test_matching_typed_lifecycle_event_separates_delta_runs(payload):
    bus = EventBus()
    subscriber = bus.subscribe(interactive=False)
    barrier = _barrier(payload)

    bus.publish(_delta("before"))
    bus.publish(barrier)
    bus.publish(_delta("after"))

    events = _queued(subscriber)
    assert [event.type for event in events] == [
        ModelTextDelta.EVENT_TYPE,
        barrier.type,
        ModelTextDelta.EVENT_TYPE,
    ]
    assert [event.data.get("text") for event in events] == ["before", None, "after"]


def test_each_delivered_barrier_starts_a_new_delta_run():
    bus = EventBus()
    subscriber = bus.subscribe(interactive=False)

    bus.publish(_delta("A"))
    bus.publish(_barrier(ModelCompleted(stop_reason="end_turn", usage=Usage())))
    bus.publish(_delta("B"))
    bus.publish(_barrier(TurnCompleted(stop_reason="end_turn", usage=Usage(), duration_ms=1.0, tool_calls=0)))
    bus.publish(_delta("C"))

    events = _queued(subscriber)
    assert [(event.type, event.data.get("text")) for event in events] == [
        (ModelTextDelta.EVENT_TYPE, "A"),
        (ModelCompleted.EVENT_TYPE, None),
        (ModelTextDelta.EVENT_TYPE, "B"),
        (TurnCompleted.EVENT_TYPE, None),
        (ModelTextDelta.EVENT_TYPE, "C"),
    ]


def test_barrier_fanout_keeps_each_subscribers_delta_copy_private():
    bus = EventBus()
    subscribers = [bus.subscribe(interactive=False) for _ in range(2)]
    originals = [_delta("A"), _barrier(ModelCompleted(stop_reason="end_turn", usage=Usage())), _delta("B")]

    for event in originals:
        bus.publish(event)

    delivered = [_queued(subscriber) for subscriber in subscribers]
    for events in delivered:
        assert [(event.type, event.data.get("text")) for event in events] == [
            (ModelTextDelta.EVENT_TYPE, "A"),
            (ModelCompleted.EVENT_TYPE, None),
            (ModelTextDelta.EVENT_TYPE, "B"),
        ]
    assert delivered[0][0] is not delivered[1][0]
    assert delivered[0][2] is not delivered[1][2]
    assert [event.data["text"] for event in (originals[0], originals[2])] == ["A", "B"]


@pytest.mark.parametrize("receive", ["get", "get_nowait"])
@pytest.mark.asyncio
async def test_consuming_delta_does_not_reuse_it_across_a_later_barrier(receive):
    bus = EventBus()
    subscriber = bus.subscribe(interactive=False)
    bus.publish(_delta("consumed"))

    if receive == "get":
        consumed = await subscriber.get()
    else:
        consumed = subscriber.get_nowait()
    assert consumed.data["text"] == "consumed"

    bus.publish(_barrier(ModelCompleted(stop_reason="end_turn", usage=Usage())))
    bus.publish(_delta("remaining"))

    events = _queued(subscriber)
    assert [(event.type, event.data.get("text")) for event in events] == [
        (ModelCompleted.EVENT_TYPE, None),
        (ModelTextDelta.EVENT_TYPE, "remaining"),
    ]


def test_filtered_out_barrier_does_not_break_observable_delta_coalescing():
    bus = EventBus()
    subscriber = bus.subscribe(types={ModelTextDelta.EVENT_TYPE}, interactive=False)

    bus.publish(_delta("A"))
    bus.publish(_barrier(ModelCompleted(stop_reason="end_turn", usage=Usage())))
    bus.publish(_delta("B"))

    events = _queued(subscriber)
    assert [(event.type, event.data["text"]) for event in events] == [(ModelTextDelta.EVENT_TYPE, "AB")]


def test_adjacent_deltas_still_coalesce_at_queue_capacity():
    bus = EventBus()
    subscriber = bus.subscribe(interactive=False, max_queue_size=1)
    first, second = _delta("A"), _delta("B")

    bus.publish(first)
    bus.publish(second)

    assert subscriber.qsize() == 1
    assert subscriber.get_nowait().data["text"] == "AB"
    assert (first.data["text"], second.data["text"]) == ("A", "B")


def test_full_queue_barrier_preserves_critical_event_and_clears_evicted_delta_cache():
    bus = EventBus()
    subscriber = bus.subscribe(interactive=False, max_queue_size=2)
    critical = _barrier(ModelCompleted(stop_reason="end_turn", usage=Usage()))
    barrier = _barrier(TurnCompleted(stop_reason="end_turn", usage=Usage(), duration_ms=1.0, tool_calls=0))

    bus.publish(critical)
    bus.publish(_delta("evict-me"))
    bus.publish(barrier)
    assert subscriber.qsize() == 2
    assert subscriber.get_nowait() is critical

    bus.publish(_delta("after-barrier"))

    events = _queued(subscriber)
    assert [(event.type, event.data.get("text")) for event in events] == [
        (TurnCompleted.EVENT_TYPE, None),
        (ModelTextDelta.EVENT_TYPE, "after-barrier"),
    ]


@pytest.mark.parametrize(
    ("first", "second"),
    [
        (_delta("A", session="session-A"), _delta("B", session="session-B")),
        (_delta("A", turn="turn-A"), _delta("B", turn="turn-B")),
        (
            Event(
                type=ToolOutputDelta.EVENT_TYPE,
                data=ToolOutputDelta(call_id="call-A", stream="stdout", text="A"),
                session_id="session-A",
                turn_id="turn-A",
            ),
            Event(
                type=ToolOutputDelta.EVENT_TYPE,
                data=ToolOutputDelta(call_id="call-A", stream="stderr", text="B"),
                session_id="session-A",
                turn_id="turn-A",
            ),
        ),
        (
            Event(
                type=ToolOutputDelta.EVENT_TYPE,
                data=ToolOutputDelta(call_id="call-A", stream="stdout", text="A"),
                session_id="session-A",
                turn_id="turn-A",
            ),
            Event(
                type=ToolOutputDelta.EVENT_TYPE,
                data=ToolOutputDelta(call_id="call-B", stream="stdout", text="B"),
                session_id="session-A",
                turn_id="turn-A",
            ),
        ),
        (
            _delta("A"),
            Event(
                type=ModelThinkingDelta.EVENT_TYPE,
                data=ModelThinkingDelta(text="B"),
                session_id="session-A",
                turn_id="turn-A",
            ),
        ),
    ],
)
def test_delta_session_turn_type_and_channel_mismatches_stay_separate(first, second):
    bus = EventBus()
    subscriber = bus.subscribe(interactive=False)

    bus.publish(first)
    bus.publish(second)

    events = _queued(subscriber)
    assert [
        (
            event.type,
            event.session_id,
            event.turn_id,
            event.data.get("call_id"),
            event.data.get("stream"),
            event.data["text"],
        )
        for event in events
    ] == [
        (
            original.type,
            original.session_id,
            original.turn_id,
            original.data.get("call_id"),
            original.data.get("stream"),
            original.data["text"],
        )
        for original in (first, second)
    ]
    assert events[0] is not events[1]
