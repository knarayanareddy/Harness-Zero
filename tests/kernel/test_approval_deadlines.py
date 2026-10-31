"""Approval expiry is measured from broker creation and checked at resolution claim."""
from __future__ import annotations

import asyncio
import json
import time

import pytest
import pytest_asyncio

from harness.kernel.bootstrap import bootstrap_kernel
from harness.kernel.bus import EventBus
from harness.kernel.interactions import InteractionBroker
from harness.kernel.transcript import TranscriptLog
from harness.plugins.bridges.acp.server import ACPServer
from harness.sdk.testing.scripted import ScriptedProvider
from harness.sdk.types import Done, ToolCallEnd


@pytest_asyncio.fixture
async def pending_write(tmp_path):
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    runtime = await bootstrap_kernel(
        workspace=workspace,
        config_dir=tmp_path / "config",
        resume_session=False,
        overrides={"kernel.security.preset": "careful"},
    )
    marker = workspace / "approval-deadline-marker.txt"
    provider = ScriptedProvider(responses=[
        [
            ToolCallEnd(
                id="deadline-write",
                name="write_to_file",
                arguments={"file_path": marker.name, "content": "approved"},
            ),
            Done(stop_reason="tool_use"),
        ],
        [Done(stop_reason="end_turn")],
    ])
    runtime.registry.register_provider(provider, plugin_name="test_approval_deadline")
    session = await runtime.session_manager.create_session()
    spec, handler, owner = runtime.registry.tools["write_to_file"]
    handler_starts: list[str] = []

    async def observed_handler(args, ctx):
        handler_starts.append(ctx.call_id)
        return await handler(args, ctx)

    runtime.registry.tools["write_to_file"] = (spec, observed_handler, owner)
    subscriber = runtime.bus.subscribe(interactive=True, session_id=session.session_id)
    turn = asyncio.create_task(runtime.commands.submit_turn(
        session_id=session.session_id,
        prompt="Write the marker",
        options={"provider": "scripted", "model": "scripted-v1"},
    ))
    try:
        async with asyncio.timeout(5):
            while True:
                event = await subscriber.get()
                if event.type == "approval.required":
                    break
        request = runtime.gate.approvals.peek(event.data["approval_id"])
        assert request is not None
        yield runtime, session, subscriber, event, turn, request, marker, handler_starts
    finally:
        if not turn.done():
            turn.cancel()
        await asyncio.gather(turn, return_exceptions=True)
        runtime.bus.unsubscribe(subscriber)
        await runtime.registry.unload_all()


def _expire_monotonic_deadline(request) -> None:
    """Advance this request's monotonic eligibility point without wall-clock sleeps."""
    deadline = getattr(request, "deadline", None)
    if isinstance(deadline, (int, float)) and not isinstance(deadline, bool):
        request.deadline = asyncio.get_running_loop().time() - 1.0


async def _settle_timeout(runtime, request) -> None:
    await runtime.gate.approvals.interactions._timeout_approval(request)


@pytest.mark.asyncio
async def test_expired_response_before_timeout_callback_cannot_claim_or_start_handler(pending_write):
    runtime, session, _subscriber, _event, turn, request, marker, starts = pending_write
    _expire_monotonic_deadline(request)

    response = await runtime.commands.resolve_approval(
        approval_id=request.id, approved=True, by="deadline-test",
    )
    assert response["status"] == "not_found"
    assert not request.future.done()
    assert starts == []
    assert not marker.exists()

    await _settle_timeout(runtime, request)
    result = await asyncio.wait_for(turn, timeout=2)
    assert result.stop_reason == "end_turn"
    assert starts == []
    assert not marker.exists()
    records = [row for row in session.log.records if row["kind"] == "approval.resolved"]
    assert len(records) == 1
    assert records[0]["metadata"]["by"] == "timeout"
    assert records[0]["metadata"]["approved"] is False


@pytest.mark.asyncio
async def test_expired_request_is_not_forwarded_to_acp_peer(pending_write):
    runtime, session, _subscriber, event, turn, request, marker, starts = pending_write
    server = ACPServer(runtime.commands, bus=runtime.bus)
    server._owned_sessions.add(session.session_id)
    server._v1_tool_calls[(session.session_id, request.call_id)] = "turn_deadline"
    barrier = asyncio.get_running_loop().create_future()
    server._v1_turn_barriers[session.session_id] = (barrier, "turn_deadline")

    class ResponseIfForwarded:
        def __init__(self, acp_server: ACPServer) -> None:
            self.acp_server = acp_server
            self.messages: list[dict[str, object]] = []

        def is_closing(self) -> bool:
            return False

        def write(self, raw: bytes) -> None:
            message = json.loads(raw)
            self.messages.append(message)
            if message.get("method") == "session/request_permission":
                self.acp_server._receive_v1_permission_response({
                    "jsonrpc": "2.0",
                    "id": message["id"],
                    "result": {"outcome": {"outcome": "selected", "optionId": "allow_once"}},
                })

        async def drain(self) -> None:
            return None

    writer = ResponseIfForwarded(server)
    server._writer = writer
    _expire_monotonic_deadline(request)

    await server._forward_v1_approval(event, event.data)

    assert writer.messages == []
    assert server._v1_pending_permissions == {}
    assert not request.resolved
    assert starts == []
    assert not marker.exists()
    await _settle_timeout(runtime, request)
    result = await asyncio.wait_for(turn, timeout=2)
    assert result.stop_reason == "end_turn"
    assert starts == []
    assert not marker.exists()


@pytest.mark.asyncio
async def test_request_persistence_consumes_original_monotonic_budget():
    loop = asyncio.get_running_loop()
    actual_time = loop.time
    clock = [actual_time()]
    loop.time = lambda: clock[0]
    bus = EventBus()
    subscriber = bus.subscribe(interactive=True, session_id="session_deadline")
    log = TranscriptLog(None, session_id="session_deadline")
    broker = InteractionBroker(bus=bus, timeout_s=5, log_for=lambda _sid: log)
    request_registered = asyncio.Event()
    allow_persistence = asyncio.Event()
    original_record_request = broker._record_request

    async def held_request_record(request):
        request_registered.set()
        await allow_persistence.wait()
        return await original_record_request(request)

    broker._record_request = held_request_record
    answer = asyncio.create_task(broker.ask(
        "approval",
        {"kind": "tool_execution", "summary": "Write marker", "call_id": "call_deadline"},
        session_id="session_deadline",
    ))
    try:
        await request_registered.wait()
        request = next(iter(broker._pending.values()))
        deadline = getattr(request, "deadline", None)
        assert isinstance(deadline, (int, float)) and not isinstance(deadline, bool)
        assert deadline == clock[0] + 5
        assert abs(request.created_at - time.time()) < 2

        # Controlled event-loop time advances while request persistence is held;
        # wall-clock metadata remains independent of approval eligibility.
        clock[0] += 6
        allow_persistence.set()
        event = await subscriber.get()
        assert event.type == "approval.required"
        assert not await broker.resolve_approval_recorded(request.id, True, by="late-peer")
        assert await answer == (False, "timed out")
        resolved = [row for row in log.records if row["kind"] == "approval.resolved"]
        assert len(resolved) == 1
        assert resolved[0]["metadata"]["by"] == "timeout"
        assert resolved[0]["metadata"]["approved"] is False
    finally:
        allow_persistence.set()
        if not answer.done():
            answer.cancel()
            await asyncio.gather(answer, return_exceptions=True)
        loop.time = actual_time
        bus.unsubscribe(subscriber)


@pytest.mark.asyncio
async def test_resolver_queued_on_resolution_lock_rechecks_expiry_before_claim(
    pending_write, monkeypatch: pytest.MonkeyPatch,
):
    runtime, session, _subscriber, _event, turn, request, marker, starts = pending_write
    deadline = getattr(request, "deadline", None)
    assert isinstance(deadline, (int, float)) and not isinstance(deadline, bool)
    assert deadline > asyncio.get_running_loop().time()
    lock = runtime.gate.approvals.interactions._resolution_lock
    queued = asyncio.Event()
    original_acquire = lock.acquire

    async def observe_queue():
        if lock.locked():
            queued.set()
        return await original_acquire()

    monkeypatch.setattr(lock, "acquire", observe_queue)
    await lock.acquire()
    resolver = asyncio.create_task(runtime.commands.resolve_approval(
        approval_id=request.id, approved=True, by="queued-deadline-test",
    ))
    try:
        # Observe the actual broker resolver wait on its held lock before expiry.
        await asyncio.wait_for(queued.wait(), timeout=1)
        _expire_monotonic_deadline(request)
    finally:
        lock.release()

    response = await resolver
    assert response["status"] == "not_found"
    assert starts == []
    assert not marker.exists()
    await _settle_timeout(runtime, request)
    result = await asyncio.wait_for(turn, timeout=2)
    assert result.stop_reason == "end_turn"
    records = [row for row in session.log.records if row["kind"] == "approval.resolved"]
    assert len(records) == 1
    assert records[0]["metadata"]["by"] == "timeout"
    assert records[0]["metadata"]["approved"] is False


@pytest.mark.asyncio
async def test_sync_compatibility_resolution_rejects_expired_approval(pending_write):
    runtime, session, _subscriber, _event, turn, request, marker, starts = pending_write
    _expire_monotonic_deadline(request)

    accepted = runtime.gate.approvals.resolve(
        request.id, True, by="legacy-keypress-test",
    )
    assert accepted is False
    assert starts == []
    assert not marker.exists()
    await _settle_timeout(runtime, request)
    result = await asyncio.wait_for(turn, timeout=2)
    assert result.stop_reason == "end_turn"
    records = [row for row in session.log.records if row["kind"] == "approval.resolved"]
    assert len(records) == 1
    assert records[0]["metadata"]["by"] == "timeout"


@pytest.mark.asyncio
async def test_timely_approval_remains_a_positive_gate_control(pending_write):
    runtime, session, _subscriber, _event, turn, request, marker, starts = pending_write
    deadline = getattr(request, "deadline", None)
    assert isinstance(deadline, (int, float)) and not isinstance(deadline, bool)
    assert deadline > asyncio.get_running_loop().time()
    assert abs(request.created_at - time.time()) < 2
    # Wall-clock metadata changes do not grant or revoke monotonic eligibility.
    request.created_at = time.time() + 86_400
    response = await runtime.commands.resolve_approval(
        approval_id=request.id, approved=True, by="timely-deadline-test",
    )
    assert response["status"] == "resolved"
    result = await asyncio.wait_for(turn, timeout=3)
    assert result.stop_reason == "end_turn"
    assert starts == [request.call_id]
    assert marker.read_text(encoding="utf-8") == "approved"
    records = [row for row in session.log.records if row["kind"] == "approval.resolved"]
    assert len(records) == 1
    assert records[0]["metadata"]["by"] == "timely-deadline-test"
    assert records[0]["metadata"]["approved"] is True


@pytest.mark.asyncio
async def test_timely_claim_finishes_durable_record_even_if_write_crosses_deadline(
    pending_write, monkeypatch: pytest.MonkeyPatch,
):
    runtime, session, _subscriber, _event, turn, request, marker, starts = pending_write
    original_append = session.log.append
    write_started = asyncio.Event()
    release_write = asyncio.Event()

    async def held_append(record):
        if getattr(record, "kind", None) == "approval.resolved":
            write_started.set()
            await release_write.wait()
        return await original_append(record)

    monkeypatch.setattr(session.log, "append", held_append)
    resolver = asyncio.create_task(runtime.commands.resolve_approval(
        approval_id=request.id, approved=True, by="claimed-before-deadline-test",
    ))
    try:
        await asyncio.wait_for(write_started.wait(), timeout=2)
        _expire_monotonic_deadline(request)
    finally:
        release_write.set()

    response = await resolver
    assert response["status"] == "resolved"
    result = await asyncio.wait_for(turn, timeout=3)
    assert result.stop_reason == "end_turn"
    assert starts == [request.call_id]
    assert marker.read_text(encoding="utf-8") == "approved"
    resolved = [row for row in session.log.records if row["kind"] == "approval.resolved"]
    assert len(resolved) == 1
    assert resolved[0]["metadata"]["by"] == "claimed-before-deadline-test"
    assert resolved[0]["metadata"]["approved"] is True
