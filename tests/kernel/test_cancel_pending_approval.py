"""T05: a cancelled turn must never execute a pending approved-later tool."""
from __future__ import annotations

import asyncio

import pytest
import pytest_asyncio

from harness.kernel import gate as gate_module
from harness.kernel.approvals import ApprovalBroker
from harness.kernel.bootstrap import bootstrap_kernel
from harness.kernel.bus import EventBus
from harness.kernel.transcript import TranscriptLog
from harness.sdk.testing.scripted import ScriptedProvider
from harness.sdk.types import Done, ToolCall, ToolCallEnd


@pytest_asyncio.fixture
async def pending_write(tmp_path):
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    runtime = await bootstrap_kernel(
        workspace=workspace, config_dir=tmp_path / "config", resume_session=False,
        overrides={"kernel.security.preset": "careful"},
    )
    provider = ScriptedProvider(responses=[
        [ToolCallEnd(id="write", name="write_to_file", arguments={"file_path": "marker.txt", "content": "must-not-run"}), Done(stop_reason="tool_use")],
        [Done(stop_reason="end_turn")],
    ])
    runtime.registry.register_provider(provider, plugin_name="test_t05")
    session = await runtime.session_manager.create_session()
    spec, handler, owner = runtime.registry.tools["write_to_file"]
    starts = []

    async def observed_handler(args, ctx):
        starts.append(ctx.call_id)
        return await handler(args, ctx)

    runtime.registry.tools["write_to_file"] = (spec, observed_handler, owner)
    subscriber = runtime.bus.subscribe(interactive=True)
    try:
        yield runtime, session, subscriber, starts
    finally:
        runtime.bus.unsubscribe(subscriber)
        await runtime.registry.unload_all()


async def event_of_type(subscriber, event_type, timeout=5):
    async with asyncio.timeout(timeout):
        while True:
            event = await subscriber.get()
            if event.type == event_type:
                return event


async def test_cancel_turn_durably_rejects_pending_approval_and_late_answer(pending_write):
    runtime, session, subscriber, starts = pending_write
    turn = asyncio.create_task(runtime.commands.submit_turn(
        session_id=session.session_id, prompt="Write marker.txt",
        options={"provider": "scripted", "model": "scripted-v1"},
    ))
    try:
        asked = await event_of_type(subscriber, "approval.required")
        approval_id = asked.data["approval_id"]
        assert not starts
        assert await runtime.commands.cancel_turn(session.session_id)

        resolved = await event_of_type(subscriber, "approval.resolved", timeout=1)
        assert resolved.data["approval_id"] == approval_id
        assert resolved.data["approved"] is False
        assert "cancel" in resolved.data["reason"].lower()
        assert resolved.record["metadata"]["status"] == "cancelled"
        assert not runtime.gate.approvals.pending()

        late = await runtime.commands.resolve_approval(approval_id=approval_id, approved=True)
        assert late["status"] == "not_found"
        result = await asyncio.wait_for(turn, timeout=5)
        assert result.stop_reason == "cancelled"
        assert starts == []
        assert not (runtime.registry.workspace / "marker.txt").exists()
        records = [r for r in session.log.records if r["kind"] == "approval.resolved"]
        assert len(records) == 1 and records[0]["metadata"]["approved"] is False
    finally:
        if not turn.done():
            turn.cancel()
        await asyncio.gather(turn, return_exceptions=True)


def broker_with_log():
    bus = EventBus()
    subscriber = bus.subscribe(interactive=True)
    log = TranscriptLog(None, session_id="test")
    broker = ApprovalBroker(bus=bus, timeout_s=2, log_for=lambda _sid: log)
    return broker, subscriber, log


async def test_cancelled_question_refuses_answer_before_waiter_resumes():
    broker, subscriber, log = broker_with_log()
    cancel = asyncio.Event()
    questions = [{"id": "choice", "question": "Choose?", "options": [{"id": "a", "label": "A"}, {"id": "b", "label": "B"}]}]
    task = asyncio.create_task(broker.interactions.ask(
        "question", {"questions": questions}, session_id="test", cancel_event=cancel,
    ))
    try:
        asked = await event_of_type(subscriber, "question.asked")
        cancel.set()  # No yield before a late answer tries to win the race.
        accepted = await broker.interactions.resolve_question(asked.data["interaction_id"], {"choice": "a"})
        assert not accepted
        assert await asyncio.wait_for(task, 1) is None
        records = [r for r in log.records if r["kind"] == "question.answered"]
        assert len(records) == 1 and records[0]["metadata"]["status"] == "cancelled"
        assert broker.interactions.pending() == []
    finally:
        if not task.done():
            task.cancel()
        await asyncio.gather(task, return_exceptions=True)
        broker.bus.unsubscribe(subscriber)


async def test_external_task_cancellation_records_approval_cancellation():
    broker, subscriber, log = broker_with_log()
    task = asyncio.create_task(broker.ask("tool_execution", "Write?", session_id="test"))
    try:
        asked = await event_of_type(subscriber, "approval.required")
        task.cancel()
        with pytest.raises(asyncio.CancelledError):
            await task
        assert broker.pending() == []
        records = [r for r in log.records if r["kind"] == "approval.resolved"]
        assert len(records) == 1
        assert records[0]["metadata"]["status"] == "cancelled"
        assert not await broker.resolve_recorded(asked.data["approval_id"], True)
    finally:
        if not task.done():
            task.cancel()
        await asyncio.gather(task, return_exceptions=True)
        broker.bus.unsubscribe(subscriber)


async def test_cancellation_during_request_persistence_does_not_leak_pending(monkeypatch):
    broker, subscriber, log = broker_with_log()
    started = asyncio.Event()
    release = asyncio.Event()
    record = broker.interactions._record

    async def blocked_record(session_id, kind, content, metadata):
        if kind == "approval.requested":
            started.set()
            await release.wait()
        return await record(session_id, kind, content, metadata)

    monkeypatch.setattr(broker.interactions, "_record", blocked_record)
    task = asyncio.create_task(broker.ask("tool_execution", "Write?", session_id="test"))
    try:
        await asyncio.wait_for(started.wait(), 1)
        task.cancel()
        with pytest.raises(asyncio.CancelledError):
            await task
        assert broker.pending() == []
        assert any(r["kind"] == "approval.resolved" and r["metadata"]["status"] == "cancelled" for r in log.records)
    finally:
        release.set()
        if not task.done():
            task.cancel()
        await asyncio.gather(task, return_exceptions=True)
        broker.bus.unsubscribe(subscriber)


@pytest.mark.parametrize("mode", ["sync", "recorded"])
async def test_cancel_signal_beats_late_approval_before_waiter_runs(mode):
    broker, subscriber, log = broker_with_log()
    cancel = asyncio.Event()
    task = asyncio.create_task(broker.ask("tool_execution", "Write?", session_id="test", cancel_event=cancel))
    try:
        asked = await event_of_type(subscriber, "approval.required")
        cancel.set()
        approval_id = asked.data["approval_id"]
        accepted = broker.resolve(approval_id, True) if mode == "sync" else await broker.resolve_recorded(approval_id, True)
        assert not accepted
        approved, reason = await asyncio.wait_for(task, 1)
        assert not approved and "cancel" in reason.lower()
        records = [r for r in log.records if r["kind"] == "approval.resolved"]
        assert len(records) == 1 and records[0]["metadata"]["status"] == "cancelled"
        assert not await broker.resolve_recorded(approval_id, True)
        assert len(log.records) == 2
    finally:
        if not task.done():
            task.cancel()
        await asyncio.gather(task, return_exceptions=True)
        broker.bus.unsubscribe(subscriber)


async def test_queued_handler_rechecks_cancellation_after_auto_approval(pending_write, monkeypatch):
    runtime, session, _subscriber, starts = pending_write
    cancel = asyncio.Event()
    original = asyncio.create_task
    injected = []

    def schedule(coroutine, *args, **kwargs):
        if coroutine.cr_code.co_name in ("observed_handler", "run_handler") and not injected:
            injected.append(True)
            cancel.set()  # After gate setup, before the handler's first step.
        return original(coroutine, *args, **kwargs)

    monkeypatch.setattr(gate_module.asyncio, "create_task", schedule)
    result = await runtime.gate.execute(
        ToolCall(id="write", name="write_to_file", arguments={"file_path": "marker.txt", "content": "must-not-run"}),
        options={"auto_approve": True}, session_id=session.session_id, log=session.log, cancel_event=cancel,
    )
    assert injected, "setup: the handler scheduling boundary was not reached"
    assert result.is_error and result.metadata["cancelled"] is True
    assert starts == []
    assert not (runtime.registry.workspace / "marker.txt").exists()


async def test_cancelling_one_session_does_not_cancel_another():
    broker, subscriber, log = broker_with_log()
    cancel_a, cancel_b = asyncio.Event(), asyncio.Event()
    a = asyncio.create_task(broker.ask("tool_execution", "A?", session_id="a", cancel_event=cancel_a))
    b = asyncio.create_task(broker.ask("tool_execution", "B?", session_id="b", cancel_event=cancel_b))
    try:
        requests = [await event_of_type(subscriber, "approval.required") for _ in range(2)]
        b_id = next(e.data["approval_id"] for e in requests if e.session_id == "b")
        cancel_a.set()
        assert not (await asyncio.wait_for(a, 1))[0]
        assert not b.done() and not cancel_b.is_set()
        assert len(broker.pending()) == 1
        assert await broker.resolve_recorded(b_id, True, reason="allowed B")
        assert await asyncio.wait_for(b, 1) == (True, "allowed B")
        records = [r for r in log.records if r["kind"] == "approval.resolved"]
        assert len(records) == 2
    finally:
        for task in (a, b):
            if not task.done():
                task.cancel()
        await asyncio.gather(a, b, return_exceptions=True)
        broker.bus.unsubscribe(subscriber)


@pytest.mark.parametrize("outcome", ["approve", "deny", "timeout"])
async def test_non_cancelled_outcomes_remain_distinct(outcome):
    broker, subscriber, log = broker_with_log()
    task = asyncio.create_task(broker.ask("tool_execution", "Write?", session_id="test", timeout_s=.02 if outcome == "timeout" else 2))
    try:
        asked = await event_of_type(subscriber, "approval.required")
        if outcome != "timeout":
            assert await broker.resolve_recorded(asked.data["approval_id"], outcome == "approve", reason=outcome)
        approved, reason = await asyncio.wait_for(task, 1)
        assert approved == (outcome == "approve")
        assert reason == ("timed out" if outcome == "timeout" else outcome)
        record = next(r for r in log.records if r["kind"] == "approval.resolved")
        assert record["metadata"].get("status") != "cancelled"
        assert record["metadata"]["by"] == ("timeout" if outcome == "timeout" else "user")
    finally:
        if not task.done():
            task.cancel()
        await asyncio.gather(task, return_exceptions=True)
        broker.bus.unsubscribe(subscriber)


async def test_cancelled_resolution_caller_cannot_strand_claimed_approval(monkeypatch):
    broker, subscriber, log = broker_with_log()
    started, release = asyncio.Event(), asyncio.Event()
    original = broker.interactions._record

    async def blocked_record(session_id, kind, content, metadata):
        if kind == "approval.resolved":
            started.set()
            await release.wait()
        return await original(session_id, kind, content, metadata)

    monkeypatch.setattr(broker.interactions, "_record", blocked_record)
    waiter = asyncio.create_task(broker.ask("tool_execution", "Write?", session_id="test"))
    resolution = None
    try:
        asked = await event_of_type(subscriber, "approval.required")
        resolution = asyncio.create_task(broker.resolve_recorded(asked.data["approval_id"], True, reason="go"))
        await asyncio.wait_for(started.wait(), 1)
        resolution.cancel()
        with pytest.raises(asyncio.CancelledError):
            await resolution
        release.set()
        assert await asyncio.wait_for(waiter, 1) == (True, "go")
        assert broker.pending() == []
        records = [r for r in log.records if r["kind"] == "approval.resolved"]
        assert len(records) == 1 and records[0]["metadata"]["approved"] is True
    finally:
        release.set()
        for task in (waiter, resolution):
            if task is not None and not task.done():
                task.cancel()
        await asyncio.gather(*(task for task in (waiter, resolution) if task is not None), return_exceptions=True)
        broker.bus.unsubscribe(subscriber)


@pytest.mark.parametrize("auto_approve", [False, True])
async def test_already_cancelled_approval_never_creates_request(auto_approve):
    broker, subscriber, log = broker_with_log()
    cancel = asyncio.Event()
    cancel.set()
    approved, reason = await broker.ask("tool_execution", "Write?", session_id="test", auto_approve=auto_approve, cancel_event=cancel)
    assert not approved and "cancel" in reason.lower()
    assert broker.pending() == [] and log.records == []
    assert subscriber.queue.empty()
    broker.bus.unsubscribe(subscriber)


async def test_turn_cancel_during_claimed_approval_persistence_prevents_handler(pending_write, monkeypatch):
    runtime, session, subscriber, starts = pending_write
    started, release = asyncio.Event(), asyncio.Event()
    broker = runtime.gate.approvals.interactions
    original = broker._record

    async def blocked_record(session_id, kind, content, metadata):
        if kind == "approval.resolved" and metadata["approved"]:
            started.set()
            await release.wait()
        return await original(session_id, kind, content, metadata)

    monkeypatch.setattr(broker, "_record", blocked_record)
    turn = asyncio.create_task(runtime.commands.submit_turn(
        session_id=session.session_id, prompt="Write marker.txt",
        options={"provider": "scripted", "model": "scripted-v1"},
    ))
    resolution = None
    try:
        asked = await event_of_type(subscriber, "approval.required")
        resolution = asyncio.create_task(runtime.commands.resolve_approval(approval_id=asked.data["approval_id"], approved=True))
        await asyncio.wait_for(started.wait(), 1)
        assert await runtime.commands.cancel_turn(session.session_id)
        release.set()
        await asyncio.wait_for(resolution, 1)
        result = await asyncio.wait_for(turn, 5)
        assert result.stop_reason == "cancelled"
        assert starts == [] and not (runtime.registry.workspace / "marker.txt").exists()
        records = [r for r in session.log.records if r["kind"] == "approval.resolved"]
        # The user decision was claimed first; do not rewrite history as a denial.
        assert len(records) == 1 and records[0]["metadata"]["approved"] is True
        assert broker.pending() == []
    finally:
        release.set()
        for task in (turn, resolution):
            if task is not None and not task.done():
                task.cancel()
        await asyncio.gather(*(task for task in (turn, resolution) if task is not None), return_exceptions=True)


@pytest.mark.parametrize("kind", ["approval", "question"])
async def test_repeated_cancellation_retains_cleanup_until_terminal_fact(kind, monkeypatch):
    broker, subscriber, log = broker_with_log()
    interactions = broker.interactions
    entered = asyncio.Event()
    method = "_cancel_approval" if kind == "approval" else "_resolve_question"
    original = getattr(interactions, method)

    async def observed(*args, **kwargs):
        entered.set()
        return await original(*args, **kwargs)

    monkeypatch.setattr(interactions, method, observed)
    if kind == "approval":
        task = asyncio.create_task(broker.ask("tool_execution", "Write?", session_id="test"))
    else:
        questions = [{"id": "choice", "question": "Choose?", "options": [{"id": "a", "label": "A"}, {"id": "b", "label": "B"}]}]
        task = asyncio.create_task(interactions.ask("question", {"questions": questions}, session_id="test"))
    held_by_test = False
    try:
        asked = await event_of_type(subscriber, "approval.required" if kind == "approval" else "question.asked")
        identity = asked.data["approval_id" if kind == "approval" else "interaction_id"]
        request = interactions.peek(identity)
        await interactions._resolution_lock.acquire()
        held_by_test = True  # Model an unrelated decision's in-flight persistence.
        task.cancel()
        await asyncio.wait_for(entered.wait(), 1)
        task.cancel()
        with pytest.raises(asyncio.CancelledError):
            await task
        if kind == "approval":
            request.future.cancel()  # Legacy observers may cancel this exposed future too.
        interactions._resolution_lock.release()
        held_by_test = False

        terminal = await event_of_type(subscriber, "approval.resolved" if kind == "approval" else "question.answered", timeout=1)
        assert terminal.record["metadata"]["status"] == "cancelled"
        if kind == "approval":
            assert terminal.data["approved"] is False
        else:
            assert terminal.data["status"] == "cancelled"
        cleanup = tuple(getattr(interactions, "_cleanup_tasks", ()))
        if cleanup:
            await asyncio.wait_for(asyncio.gather(*cleanup), 1)
        assert interactions.peek(identity) is None
        assert interactions.pending() == []
        records = [r for r in log.records if r["kind"] == ("approval.resolved" if kind == "approval" else "question.answered")]
        assert len(records) == 1
    finally:
        if held_by_test:
            interactions._resolution_lock.release()
        if not task.done():
            task.cancel()
        await asyncio.gather(task, return_exceptions=True)
        broker.bus.unsubscribe(subscriber)
