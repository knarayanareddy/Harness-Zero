import asyncio
import pytest
from harness.core.concurrency_gate import ConcurrencyGate


@pytest.mark.asyncio
async def test_concurrency_gate_slots_and_queue():
    gate = ConcurrencyGate(limit=2)

    # Acquire 2 slots
    rel1 = await gate.acquire("agent")
    rel2 = await gate.acquire("agent")

    stats = gate.stats()
    assert stats["active"] == 2
    assert stats["queued"] == 0

    # 3rd request should be queued
    enqueued = False

    async def third_requester():
        nonlocal enqueued
        enqueued = True
        rel = await gate.acquire("agent")
        rel()

    task = asyncio.create_task(third_requester())
    await asyncio.sleep(0.05)
    assert enqueued is True
    assert gate.queued == 1

    # Release slot 1 -> unblocks 3rd
    rel1()
    await asyncio.sleep(0.05)
    assert gate.queued == 0

    rel2()
    await task
