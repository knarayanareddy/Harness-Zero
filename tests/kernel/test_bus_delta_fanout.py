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
from harness.sdk.events import Event


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
