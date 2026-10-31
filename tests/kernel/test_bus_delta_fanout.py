"""
Regression: EventBus delta coalescing mutated the Event instance shared by all
subscribers, so with N subscribers every chunk after the first was appended N
times ("…is Amsterdam.is Amsterdam.is Amsterdam." in the web UI, which always
has at least two subscribers: the SSE replay buffer and the browser).

Found by tests/scenarios/plain_answer_web (flow-of-information
test, FLOW_TEST_FINDINGS.md F-6).
"""
from __future__ import annotations

import asyncio

import pytest

from harness.kernel.bus import EventBus
from harness.sdk.events import Event, ModelTextDelta, ModelThinkingDelta, ToolOutputDelta


async def _drain(sub, n: int) -> list[Event]:
    out = []
    for _ in range(n):
        out.append(await asyncio.wait_for(sub.get(), timeout=1))
    return out


@pytest.mark.asyncio
async def test_each_subscriber_sees_every_chunk_exactly_once():
    bus = EventBus()
    subs = [bus.subscribe(interactive=False) for _ in range(3)]

    chunks = ["The capital of the Netherlands ", "is Amsterdam.", " Really."]
    originals = [Event(type="model.text.delta", data={"text": c}, turn_id="t1") for c in chunks]
    for ev in originals:
        bus.publish(ev)
    bus.publish(Event(type="turn.completed", data={}, turn_id="t1"))

    for sub in subs:
        texts = ""
        while True:
            ev = await asyncio.wait_for(sub.get(), timeout=1)
            if ev.type == "turn.completed":
                break
            texts += ev.data["text"]
        assert texts == "".join(chunks), texts

    # The published instances are never mutated.
    assert [e.data["text"] for e in originals] == chunks


@pytest.mark.asyncio
async def test_coalescing_still_merges_queued_deltas_per_subscriber():
    bus = EventBus()
    sub = bus.subscribe(interactive=False)
    for c in ("a", "b", "c"):
        bus.publish(Event(type="model.text.delta", data={"text": c}, turn_id="t1"))
    # Nothing consumed yet → the three chunks were merged into one queued delta.
    assert sub.qsize() == 1
    ev = await sub.get()
    assert ev.data["text"] == "abc"


@pytest.mark.asyncio
async def test_deltas_from_different_turns_are_not_merged():
    bus = EventBus()
    sub = bus.subscribe(interactive=False)
    bus.publish(Event(type="model.text.delta", data={"text": "turn one"}, turn_id="t1"))
    bus.publish(Event(type="model.text.delta", data={"text": "turn two"}, turn_id="t2"))
    got = await _drain(sub, 2)
    assert [e.data["text"] for e in got] == ["turn one", "turn two"]


@pytest.mark.parametrize("payload_type", [ModelTextDelta, ModelThinkingDelta, ToolOutputDelta])
@pytest.mark.parametrize("turn_id", [None, "shared-turn"])
@pytest.mark.parametrize("other_session", ["B", None])
@pytest.mark.parametrize("foreign_first", [False, True])
def test_delta_coalescing_never_crosses_session_identity(payload_type, turn_id, other_session, foreign_first):
    bus = EventBus()
    subscribers = [bus.subscribe(interactive=False) for _ in range(2)]

    def event(session, text):
        args = {"text": text}
        if payload_type is ToolOutputDelta:
            args.update(call_id="repeated-call-id", stream="stdout")
        payload = payload_type(**args)
        return Event(type=payload.EVENT_TYPE, data=payload, session_id=session, turn_id=turn_id)

    owned = event("A", "OWNED_TEXT")
    foreign = event(other_session, "PRIVATE_FOREIGN_CANARY")
    originals = [foreign, owned] if foreign_first else [owned, foreign]
    for item in originals:
        bus.publish(item)
    for subscriber in subscribers:
        received = []
        while not subscriber.empty():
            received.append(subscriber.get_nowait())
        visible_to_a = "".join(item.data["text"] for item in received if item.session_id == "A")
        assert "PRIVATE_FOREIGN_CANARY" not in visible_to_a
        assert [(item.session_id, item.data["text"]) for item in received] == [
            (item.session_id, item.data["text"]) for item in originals
        ], "Both distinct session envelopes and their ordering must survive fanout"
    assert owned.data["text"] == "OWNED_TEXT"
    assert foreign.data["text"] == "PRIVATE_FOREIGN_CANARY"


@pytest.mark.parametrize("payload_type", [ModelTextDelta, ModelThinkingDelta, ToolOutputDelta])
def test_same_session_typed_deltas_still_coalesce(payload_type):
    bus = EventBus()
    subscriber = bus.subscribe(interactive=False)
    for text in ("one", "two"):
        args = {"text": text}
        if payload_type is ToolOutputDelta:
            args.update(call_id="same-call", stream="stdout")
        payload = payload_type(**args)
        bus.publish(Event(type=payload.EVENT_TYPE, data=payload, session_id="A", turn_id="same-turn"))
    assert subscriber.qsize() == 1
    result = subscriber.get_nowait()
    assert result.session_id == "A"
    assert result.data["text"] == "onetwo"
