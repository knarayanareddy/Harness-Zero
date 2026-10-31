"""R22: queued deltas must not move across a delivered lifecycle boundary."""
from harness.kernel.bus import EventBus
from harness.sdk.events import Event, ModelCompleted, ModelTextDelta
from harness.sdk.types import Usage


def delta(text):
    return Event(
        type=ModelTextDelta.EVENT_TYPE,
        data=ModelTextDelta(text=text),
        session_id="session-barrier-probe",
        turn_id="turn-barrier-probe",
    )


def drain(subscriber):
    events = []
    while not subscriber.empty():
        event = subscriber.get_nowait()
        events.append((event.type, event.data.get("text")))
    return events


def test_R22_delta_cannot_move_across_model_completed():
    bus = EventBus()
    subscriber = bus.subscribe(interactive=False, max_queue_size=8)
    for event in [
        delta("A"),
        Event(
            type=ModelCompleted.EVENT_TYPE,
            data=ModelCompleted(stop_reason="end_turn", usage=Usage()),
            session_id="session-barrier-probe",
            turn_id="turn-barrier-probe",
        ),
        delta("B"),
    ]:
        bus.publish(event)
    assert drain(subscriber) == [
        ("model.text.delta", "A"),
        ("model.completed", None),
        ("model.text.delta", "B"),
    ]


def test_R22_adjacent_delta_coalescing_control():
    bus = EventBus()
    subscriber = bus.subscribe(interactive=False, max_queue_size=8)
    first, second = delta("A"), delta("B")
    bus.publish(first)
    bus.publish(second)
    assert drain(subscriber) == [("model.text.delta", "AB")]
    assert first.data["text"] == "A" and second.data["text"] == "B"
