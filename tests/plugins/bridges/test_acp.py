"""Internal ACP binding conformance against real kernel Commands and providers."""
from __future__ import annotations

import asyncio
import json
from pathlib import Path
from typing import Any

import pytest

from harness.kernel.bootstrap import bootstrap_kernel
from harness.kernel.bus import EventBus
from harness.plugins.bridges.acp import ACPServer
from harness.sdk.events import ApprovalRequired, Event, ModelTextDelta
from harness.sdk.testing.scripted import ScriptedProvider
from harness.sdk.types import Done, ModelInfo, TextDelta, ToolCallEnd

MODEL = ModelInfo(id="scripted-v1", display_name="Scripted")


class MockWriter:
    """In-memory transport only; Commands, gate, runner and provider remain real."""

    def __init__(self) -> None:
        self.lines: list[dict[str, Any]] = []
        self.closed = False
        self.wrote = asyncio.Event()

    def write(self, data: bytes) -> None:
        for line in data.decode("utf-8").splitlines():
            if line.strip():
                self.lines.append(json.loads(line))
                self.wrote.set()

    async def drain(self) -> None:
        return None

    def is_closing(self) -> bool:
        return self.closed


class FailingNotificationWriter(MockWriter):
    """Transport seam that fails only while draining a selected notification."""

    def __init__(self, method: str) -> None:
        super().__init__()
        self.method = method
        self.failed = asyncio.Event()

    async def drain(self) -> None:
        if self.lines and self.lines[-1].get("method") == self.method:
            self.failed.set()
            raise OSError(f"injected drain failure for {self.method}")


async def _runtime(tmp_path: Path, *responses: list[Any]):
    runtime = await bootstrap_kernel(
        workspace=tmp_path / "workspace",
        config_dir=tmp_path / "config",
        resume_session=False,
    )
    runtime.registry.register_provider(
        ScriptedProvider(responses=list(responses), models=[MODEL], strict_exhaustion=True),
        plugin_name="acp_test_scripted",
    )
    return runtime


def _send(reader: asyncio.StreamReader, request: dict[str, Any]) -> None:
    reader.feed_data((json.dumps(request) + "\n").encode())


async def _create_session(server: ACPServer, title: str = "ACP test") -> str:
    response = await server.handle_request({
        "jsonrpc": "2.0", "id": 1, "method": "session/new", "params": {"title": title}
    })
    assert "error" not in response
    return response["result"]["session_id"]


async def _wait_for_response(writer: MockWriter, request_id: int) -> dict[str, Any]:
    async def wait():
        while True:
            response = next((row for row in writer.lines if row.get("id") == request_id), None)
            if response is not None:
                return response
            writer.wrote.clear()
            await writer.wrote.wait()

    return await asyncio.wait_for(wait(), timeout=2.0)


@pytest.mark.asyncio
async def test_acp_session_and_prompt_use_actual_commands_and_runner(tmp_path: Path):
    runtime = await _runtime(
        tmp_path,
        [TextDelta(text="real runner output"), Done(stop_reason="end_turn")],
    )
    server = ACPServer(commands=runtime.commands, bus=runtime.bus)
    try:
        initialized = await server.handle_request({"jsonrpc": "2.0", "id": 1, "method": "initialize"})
        assert initialized["result"]["protocolVersion"] == "0.1"
        assert initialized["result"]["serverInfo"]["name"] == "harness-zero-acp"

        session_id = await _create_session(server)
        listed = await server.handle_request({"jsonrpc": "2.0", "id": 2, "method": "session/list"})
        assert [row["session_id"] for row in listed["result"]] == [session_id]

        turn = await server.handle_request({
            "jsonrpc": "2.0", "id": 3, "method": "prompt",
            "params": {
                "session_id": session_id,
                "prompt": "exercise the actual runner",
                "options": {"provider": "scripted", "model": MODEL.id},
            },
        })
        assert turn["result"] == {
            "status": "completed", "output": "real runner output", "session_id": session_id,
            "stop_reason": "end_turn",
        }
        history = await runtime.commands.sessions_get(session_id=session_id)
        assert any(row["content"] == "exercise the actual runner" for row in history["records"])
        assert await runtime.commands.sessions_list()
    finally:
        await runtime.registry.unload_all()


@pytest.mark.asyncio
async def test_cancel_during_real_pending_approval_is_truthfully_cancelled(tmp_path: Path):
    runtime = await _runtime(
        tmp_path,
        [ToolCallEnd(
            id="call-cancel",
            name="run_command",
            arguments={"command": "touch should-not-run", "cwd": ".", "wait_ms_before_async": 0},
        ), Done(stop_reason="tool_use")],
    )
    server = ACPServer(commands=runtime.commands, bus=runtime.bus)
    subscriber = runtime.bus.subscribe(types={"approval.required"}, interactive=False, max_queue_size=8)
    reader = asyncio.StreamReader()
    writer = MockWriter()
    try:
        session_id = await _create_session(server)
        server_task = asyncio.create_task(server.run_stdio(reader, writer))
        await asyncio.wait_for(server._ready.wait(), timeout=1.0)
        _send(reader, {
            "jsonrpc": "2.0", "id": 2, "method": "prompt",
            "params": {"session_id": session_id, "prompt": "run gated command", "options": {"provider": "scripted", "model": MODEL.id}},
        })
        request_event = await asyncio.wait_for(subscriber.get(), timeout=2.0)
        approval_id = request_event.data["approval_id"]

        _send(reader, {
            "jsonrpc": "2.0", "id": 3, "method": "cancel", "params": {"session_id": session_id}
        })
        response = await _wait_for_response(writer, 3)
        assert response["result"]["status"] == "cancellation_requested"
        turn = await _wait_for_response(writer, 2)
        assert turn["result"]["status"] == "cancelled"
        assert runtime.commands.gate.approvals.peek(approval_id) is None
        assert not (runtime.commands.workspace / "should-not-run").exists()
        reader.feed_eof()
        await asyncio.wait_for(server_task, timeout=2.0)
        assert not server._question_subscribers
        assert not runtime.bus.has_interactive_subscribers(session_id)
    finally:
        runtime.bus.unsubscribe(subscriber)
        await runtime.registry.unload_all()


@pytest.mark.asyncio
async def test_approval_resolution_requires_owned_pending_id_and_boolean(tmp_path: Path):
    runtime = await _runtime(
        tmp_path,
        [ToolCallEnd(
            id="call-approval",
            name="run_command",
            arguments={"command": "printf ACP_APPROVED", "cwd": ".", "wait_ms_before_async": 1000},
        ), Done(stop_reason="tool_use")],
        [TextDelta(text="approved tool completed"), Done(stop_reason="end_turn")],
    )
    server = ACPServer(commands=runtime.commands, bus=runtime.bus)
    foreign_server = ACPServer(commands=runtime.commands, bus=runtime.bus)
    events = runtime.bus.subscribe(types={"approval.required"}, interactive=False, max_queue_size=8)
    try:
        session_id = await _create_session(server)
        foreign_id = await _create_session(foreign_server, "foreign")
        reader = asyncio.StreamReader()
        writer = MockWriter()
        server_task = asyncio.create_task(server.run_stdio(reader, writer))
        await asyncio.wait_for(server._ready.wait(), timeout=1.0)
        _send(reader, {
            "jsonrpc": "2.0", "id": 2, "method": "prompt",
            "params": {"session_id": session_id, "prompt": "call gated tool", "options": {"provider": "scripted", "model": MODEL.id}},
        })
        approval_event = await asyncio.wait_for(events.get(), timeout=2.0)
        approval_id = approval_event.data["approval_id"]

        _send(reader, {
            "jsonrpc": "2.0", "id": 3, "method": "approval/resolve",
            "params": {"request_id": approval_id, "approved": "false"},
        })
        malformed = await _wait_for_response(writer, 3)
        assert malformed["error"]["code"] == -32602
        assert runtime.commands.gate.approvals.peek(approval_id) is not None

        foreign = await foreign_server.handle_request({
            "jsonrpc": "2.0", "id": 4, "method": "permission/resolve",
            "params": {"request_id": approval_id, "approved": True},
        })
        assert foreign["error"]["code"] == -32001
        assert foreign_id != session_id
        assert runtime.commands.gate.approvals.peek(approval_id) is not None

        _send(reader, {
            "jsonrpc": "2.0", "id": 5, "method": "approval/resolve",
            "params": {"request_id": approval_id, "approved": True, "reason": "approved in test"},
        })
        resolved = await _wait_for_response(writer, 5)
        assert resolved["result"]["status"] == "resolved"
        turn = await _wait_for_response(writer, 2)
        assert turn["result"]["status"] == "completed"
        assert turn["result"]["output"] == "approved tool completed"
        history = await runtime.commands.sessions_get(session_id=session_id)
        assert any(record.get("kind") == "tool.result" for record in history["records"])
        reader.feed_eof()
        await asyncio.wait_for(server_task, timeout=2.0)
    finally:
        runtime.bus.unsubscribe(events)
        if "server_task" in locals() and not server_task.done():
            reader.feed_eof()
            await asyncio.wait_for(server_task, timeout=2.0)
        await runtime.registry.unload_all()


@pytest.mark.asyncio
async def test_connection_event_forwarder_filters_sessions_and_uses_typed_payload(tmp_path: Path):
    runtime = await _runtime(tmp_path)
    configured_secret = "configured-acp-event-secret"
    runtime.commands.secrets.set("ACP_TEST_SECRET", configured_secret)
    server = ACPServer(commands=runtime.commands, bus=runtime.bus)
    writer = MockWriter()
    server._writer = writer
    try:
        session_id = await _create_session(server)
        server._bus_task = asyncio.create_task(server._forward_bus_events())
        await asyncio.wait_for(server._bus_ready.wait(), timeout=2.0)
        runtime.bus.publish(Event(
            type="model.text.delta", data=ModelTextDelta(text="AKIA1234567890123456"), session_id="foreign"
        ))
        runtime.bus.publish(Event(
            type="model.text.delta", data=ModelTextDelta(text="owned delta"), session_id=session_id
        ))
        await asyncio.wait_for(writer.wrote.wait(), timeout=2.0)
        assert len(writer.lines) == 1
        assert writer.lines[0]["method"] == "turn/delta"
        assert writer.lines[0]["params"] == {"session_id": session_id, "delta": "owned delta"}

        runtime.bus.publish(Event(
            type="approval.required",
            data=ApprovalRequired(
                approval_id="approval-typed",
                call_id="call-typed",
                kind="tool_execution",
                summary="Run the approved tool",
                details={"token": "AKIA1234567890123456", "configured": configured_secret, "large": "z" * 5000},
                timeout_s=30.0,
                options=["approve", "deny"],
            ),
            session_id=session_id,
        ))
        async def second_notification():
            while len(writer.lines) < 2:
                writer.wrote.clear()
                await writer.wrote.wait()
        await asyncio.wait_for(second_notification(), timeout=2.0)
        assert writer.lines[1]["method"] == "permission/request"
        assert writer.lines[1]["params"]["request_id"] == "approval-typed"
        assert writer.lines[1]["params"]["kind"] == "tool_execution"
        assert writer.lines[1]["params"]["details"]["token"] == "[REDACTED AWS KEY]"
        assert writer.lines[1]["params"]["details"]["configured"] == "[REDACTED SECRET]"
        assert writer.lines[1]["params"]["truncated"] is True
        assert writer.lines[1]["params"]["details"]["large"].endswith("…[truncated]")
    finally:
        if server._bus_task:
            server._bus_task.cancel()
            await asyncio.wait_for(asyncio.gather(server._bus_task, return_exceptions=True), timeout=2.0)
        await runtime.registry.unload_all()


@pytest.mark.asyncio
async def test_acp_rejects_malformed_and_unowned_requests_as_protocol_errors(tmp_path: Path):
    runtime = await _runtime(tmp_path)
    server = ACPServer(commands=runtime.commands, bus=runtime.bus)
    try:
        malformed = await server.handle_request({
            "jsonrpc": "2.0", "id": 1, "method": "prompt", "params": {"session_id": [], "prompt": 7}
        })
        assert malformed["error"]["code"] == -32001
        invalid_options = await server.handle_request({
            "jsonrpc": "2.0", "id": 2, "method": "session/new", "params": {"title": 42}
        })
        assert invalid_options["error"]["code"] == -32602
        invalid_session = await server.handle_request({
            "jsonrpc": "2.0", "id": 4, "method": "session/new",
            "params": {"session_id": "foreign-session"},
        })
        assert invalid_session["error"]["code"] == -32602
        invalid_envelope = await server.handle_request({"id": 3, "method": "session/list"})
        assert invalid_envelope["error"]["code"] == -32600
    finally:
        await runtime.registry.unload_all()


@pytest.mark.asyncio
async def test_stdio_eof_cancels_owned_turn_and_awaits_unsubscribe(tmp_path: Path):
    runtime = await _runtime(
        tmp_path,
        [ToolCallEnd(
            id="call-eof",
            name="run_command",
            arguments={"command": "touch eof-should-not-run", "cwd": ".", "wait_ms_before_async": 0},
        ), Done(stop_reason="tool_use")],
    )
    server = ACPServer(commands=runtime.commands, bus=runtime.bus)
    subscriber = runtime.bus.subscribe(types={"approval.required"}, interactive=False, max_queue_size=8)
    reader = asyncio.StreamReader()
    writer = MockWriter()
    try:
        session_id = await _create_session(server)
        server_task = asyncio.create_task(server.run_stdio(reader, writer))
        await asyncio.wait_for(server._ready.wait(), timeout=1.0)
        _send(reader, {
            "jsonrpc": "2.0", "id": 2, "method": "prompt",
            "params": {"session_id": session_id, "prompt": "await approval", "options": {"provider": "scripted", "model": MODEL.id}},
        })
        request_event = await asyncio.wait_for(subscriber.get(), timeout=2.0)
        assert request_event.data["approval_id"]
        reader.feed_eof()
        await asyncio.wait_for(server_task, timeout=2.0)
        assert server._bus_task is None
        assert server._subscriber is None
        assert runtime.commands.gate.approvals.peek(request_event.data["approval_id"]) is None
        assert not (runtime.commands.workspace / "eof-should-not-run").exists()
        assert not server._question_subscribers
        assert not runtime.bus.has_interactive_subscribers(session_id)
    finally:
        runtime.bus.unsubscribe(subscriber)
        await runtime.registry.unload_all()


@pytest.mark.asyncio
async def test_question_answers_are_connection_owned_and_interactive_only_for_owned_sessions(tmp_path: Path):
    runtime = await _runtime(
        tmp_path,
        [ToolCallEnd(
            id="call-question",
            name="ask_user",
            arguments={"questions": [{
                "id": "choice",
                "question": "Choose one",
                "options": [{"id": "a", "label": "Option A"}, {"id": "b", "label": "Option B"}],
            }]},
        ), Done(stop_reason="tool_use")],
        [TextDelta(text="question answered"), Done(stop_reason="end_turn")],
    )
    first = ACPServer(commands=runtime.commands, bus=runtime.bus)
    second = ACPServer(commands=runtime.commands, bus=runtime.bus)
    first_reader, second_reader = asyncio.StreamReader(), asyncio.StreamReader()
    first_writer, second_writer = MockWriter(), MockWriter()
    first_task = asyncio.create_task(first.run_stdio(first_reader, first_writer))
    second_task = asyncio.create_task(second.run_stdio(second_reader, second_writer))
    try:
        await asyncio.wait_for(asyncio.gather(first._ready.wait(), second._ready.wait()), timeout=1.0)
        _send(first_reader, {"jsonrpc": "2.0", "id": 1, "method": "initialize"})
        initialized = await _wait_for_response(first_writer, 1)
        assert initialized["result"]["capabilities"]["questions"] is True
        _send(first_reader, {"jsonrpc": "2.0", "id": 2, "method": "session/new"})
        first_session = (await _wait_for_response(first_writer, 2))["result"]["session_id"]
        _send(second_reader, {"jsonrpc": "2.0", "id": 1, "method": "session/new"})
        second_session = (await _wait_for_response(second_writer, 1))["result"]["session_id"]
        assert runtime.bus.has_interactive_subscribers(first_session)
        assert runtime.bus.has_interactive_subscribers(second_session)
        assert not runtime.bus.has_interactive_subscribers("not-owned-by-either")

        _send(first_reader, {
            "jsonrpc": "2.0", "id": 3, "method": "prompt",
            "params": {"session_id": first_session, "prompt": "ask the question", "options": {"provider": "scripted", "model": MODEL.id}},
        })

        async def question_notification():
            while not any(message.get("method") == "question/request" for message in first_writer.lines):
                first_writer.wrote.clear()
                await first_writer.wrote.wait()
            return next(message for message in first_writer.lines if message.get("method") == "question/request")

        notification = await asyncio.wait_for(question_notification(), timeout=2.0)
        interaction_id = notification["params"]["request_id"]
        assert notification["params"]["session_id"] == first_session
        assert notification["params"]["questions"][0]["id"] == "choice"

        _send(second_reader, {
            "jsonrpc": "2.0", "id": 4, "method": "question/answer",
            "params": {"session_id": first_session, "request_id": interaction_id, "answers": {"choice": "a"}},
        })
        foreign = await _wait_for_response(second_writer, 4)
        assert foreign["error"]["code"] == -32001
        assert await runtime.commands.interactions_list(session_id=first_session)

        _send(first_reader, {
            "jsonrpc": "2.0", "id": 5, "method": "question/answer",
            "params": {"session_id": first_session, "request_id": interaction_id, "answers": {"choice": "a"}},
        })
        answered = await _wait_for_response(first_writer, 5)
        assert answered["result"]["status"] == "answered"
        completed = await _wait_for_response(first_writer, 3)
        assert completed["result"]["status"] == "completed"
        assert completed["result"]["output"] == "question answered"
    finally:
        first_reader.feed_eof()
        second_reader.feed_eof()
        await asyncio.wait_for(asyncio.gather(first_task, second_task), timeout=2.0)
        await runtime.registry.unload_all()


@pytest.mark.asyncio
async def test_notification_write_failure_aborts_stdio_connection(tmp_path: Path):
    runtime = await _runtime(tmp_path)
    server = ACPServer(commands=runtime.commands, bus=runtime.bus)
    session_id = await _create_session(server)
    reader = asyncio.StreamReader()
    writer = FailingNotificationWriter("turn/delta")
    server_task = asyncio.create_task(server.run_stdio(reader, writer))
    try:
        await asyncio.wait_for(server._ready.wait(), timeout=1.0)
        await asyncio.wait_for(server._bus_ready.wait(), timeout=1.0)
        runtime.bus.publish(Event(
            type="model.text.delta", data=ModelTextDelta(text="will fail to drain"), session_id=session_id,
        ))
        await asyncio.wait_for(writer.failed.wait(), timeout=1.0)
        with pytest.raises(OSError, match="injected drain failure"):
            await asyncio.wait_for(asyncio.shield(server_task), timeout=1.0)
        assert server._subscriber is None
        assert not server._question_subscribers
    finally:
        if not server_task.done():
            reader.feed_eof()
            await asyncio.wait_for(server_task, timeout=2.0)
        await runtime.registry.unload_all()


@pytest.mark.parametrize("text", ["x" * 4096, "🧪" * 4096])
def test_safe_payload_has_aggregate_encoded_byte_budget(text):
    server = ACPServer(commands=object())
    payload = server._safe_payload({
        "session_id": "session-owned", "request_id": "approval-owned",
        "details": {f"field-{index}": {"nested": [text] * 32} for index in range(32)},
    })
    assert payload["session_id"] == "session-owned"
    assert payload["request_id"] == "approval-owned"
    assert payload["truncated"] is True
    assert len(json.dumps(payload, ensure_ascii=True, separators=(",", ":")).encode("ascii")) <= 16 * 1024


def test_safe_payload_never_materializes_a_whole_oversized_mapping():
    visited = 0

    class ObservedMapping(dict):
        def __len__(self):
            return 1000

        def items(self):
            nonlocal visited
            for index in range(1000):
                visited += 1
                assert visited <= 32, "Input traversal must stop before materializing discarded fields"
                yield f"field-{index}", "value"

    result = ACPServer(commands=object())._safe_payload(ObservedMapping())
    assert visited <= 32
    assert result["truncated"] is True


@pytest.mark.asyncio
async def test_closed_output_channel_is_not_silently_ignored():
    server = ACPServer(commands=object())
    writer = MockWriter()
    writer.closed = True
    server._writer = writer
    with pytest.raises(ConnectionError, match="output channel"):
        await server._write({"jsonrpc": "2.0", "method": "permission/request", "params": {}})


def test_oversized_notification_strings_are_discarded_before_secret_scanning(monkeypatch):
    from harness.plugins.bridges.acp import server as server_module

    original = server_module.sanitize_output

    def bounded_scan(text, secret_values=()):
        assert len(text) <= 4096, "Discard oversized strings before scanning or copying them"
        return original(text, secret_values)

    monkeypatch.setattr(server_module, "sanitize_output", bounded_scan)
    oversized = "untrusted-payload-" * 100000
    payload = ACPServer(commands=object())._safe_payload({
        "session_id": "owned-session", "details": {"value": oversized, oversized: "ignored"},
    })
    assert payload["details"]["value"] == "…[truncated]"
    assert payload["truncated"] is True


@pytest.mark.parametrize("name", ["ANTHROPIC_API_KEY", "OPAQUE_VENDOR_NAME"])
def test_payload_redacts_environment_credentials_from_real_secret_sources(tmp_path, monkeypatch, name):
    from types import SimpleNamespace

    from harness.kernel.secrets import Secrets

    sentinel = "synthetic-acp-environment-value-54182"
    monkeypatch.setenv(name, sentinel)
    secrets = Secrets(tmp_path / "secrets.json")
    if name == "OPAQUE_VENDOR_NAME":
        assert secrets.get(name) == sentinel
        monkeypatch.delenv(name)
    server = ACPServer(commands=SimpleNamespace(secrets=secrets))
    payload = server._safe_payload({"session_id": "owned-session", "delta": sentinel})
    assert payload["delta"] == "[REDACTED SECRET]"


def test_secret_discovery_failure_does_not_silently_disable_payload_redaction():
    from types import SimpleNamespace

    def unavailable():
        raise OSError("injected credential-discovery failure")

    server = ACPServer(commands=SimpleNamespace(secrets=SimpleNamespace(redaction_values=unavailable)))
    with pytest.raises(OSError, match="credential-discovery failure"):
        server._safe_payload({"delta": "never emit unredacted"})


def test_real_secret_file_read_failure_prevents_payload_forwarding(tmp_path, monkeypatch):
    from types import SimpleNamespace

    from harness.kernel.secrets import Secrets

    secrets = Secrets(tmp_path / "secrets.json")
    secrets.set("ACP_TEST_SECRET", "synthetic-file-value-48219")
    read_text = Path.read_text

    def unavailable(path, *args, **kwargs):
        if path == secrets.path:
            raise PermissionError("injected secret-file read failure")
        return read_text(path, *args, **kwargs)

    monkeypatch.setattr(Path, "read_text", unavailable)
    server = ACPServer(commands=SimpleNamespace(secrets=secrets))
    with pytest.raises(PermissionError, match="secret-file read failure"):
        server._safe_payload({"delta": "synthetic-file-value-48219"})


@pytest.mark.asyncio
async def test_oversized_turn_output_is_discarded_before_secret_scanning(monkeypatch):
    from types import SimpleNamespace

    from harness.plugins.bridges.acp import server as server_module

    original = server_module.sanitize_output

    def bounded_scan(text, secret_values=()):
        assert len(text) <= 8192, "Turn-output scanning must have a source bound"
        return original(text, secret_values)

    async def submit_turn(**kwargs):
        return SimpleNamespace(output="untrusted-output-" * 100000, stop_reason="end_turn", error=None)

    monkeypatch.setattr(server_module, "sanitize_output", bounded_scan)
    server = ACPServer(commands=SimpleNamespace(submit_turn=submit_turn))
    result = await server._execute_prompt("owned-session", "fixture", {})
    assert result["output"] == "…[truncated]" and result["output_truncated"] is True


@pytest.mark.asyncio
async def test_failed_write_aborts_output_before_any_queued_writer_can_send():
    class BrokenWriter(MockWriter):
        def __init__(self):
            super().__init__()
            self.transport = self

        async def drain(self):
            raise OSError("injected output failure")

        def abort(self):
            self.closed = True

    writer = BrokenWriter()
    server = ACPServer(commands=object())
    server._writer = writer
    server._connected = True
    with pytest.raises(OSError, match="injected output failure"):
        await server._write({"jsonrpc": "2.0", "id": 1, "result": {}})
    assert writer.closed, "Failure must abort output without waiting for the main loop"
    assert not server._connected
    with pytest.raises(ConnectionError):
        await server._write({"jsonrpc": "2.0", "id": 2, "result": {}})
    assert len(writer.lines) == 1


@pytest.mark.asyncio
@pytest.mark.parametrize("event_type,data", [
    ("question.asked", {"questions": []}),
    ("question.asked", {"interaction_id": "", "questions": []}),
    ("question.asked", {"interaction_id": "question-owned", "questions": []}),
    ("question.asked", {"interaction_id": "question-owned", "questions": {}}),
    ("approval.required", {"approval_id": False}),
])
async def test_malformed_owned_interaction_closes_its_channel(event_type, data):
    bus = EventBus()
    server = ACPServer(commands=object(), bus=bus)
    session_id = "session_123456789abc"
    server._owned_sessions.add(session_id)
    reader = asyncio.StreamReader()
    writer = MockWriter()
    serving = asyncio.create_task(server.run_stdio(reader, writer))
    try:
        await asyncio.wait_for(server._bus_ready.wait(), timeout=1.0)
        bus.publish(Event(type=event_type, session_id=session_id, data=data))
        with pytest.raises(ConnectionError):
            await asyncio.wait_for(asyncio.shield(serving), timeout=1.0)
        assert not writer.lines
        assert not bus.has_interactive_subscribers(session_id)
    finally:
        if not serving.done():
            reader.feed_eof()
            await asyncio.wait_for(serving, timeout=2.0)


@pytest.mark.asyncio
async def test_aggregate_approval_payload_is_truncated_without_losing_required_fields(tmp_path: Path):
    runtime = await _runtime(tmp_path)
    server = ACPServer(commands=runtime.commands, bus=runtime.bus)
    writer = MockWriter()
    server._writer = writer
    try:
        session_id = await _create_session(server)
        server._bus_task = asyncio.create_task(server._forward_bus_events())
        await asyncio.wait_for(server._bus_ready.wait(), timeout=1.0)
        runtime.bus.publish(Event(
            type="approval.required",
            session_id=session_id,
            data=ApprovalRequired(
                approval_id="approval-aggregate",
                call_id="call-aggregate",
                kind="tool_execution",
                summary="Run the approved tool",
                details={f"field-{index}": "x" * 4096 for index in range(32)},
                timeout_s=30.0,
                options=["approve", "deny"],
            ),
        ))

        async def notification():
            while not writer.lines:
                writer.wrote.clear()
                await writer.wrote.wait()
            return writer.lines[0]

        message = await asyncio.wait_for(notification(), timeout=1.0)
        assert message["method"] == "permission/request"
        assert message["params"]["request_id"] == "approval-aggregate"
        assert message["params"]["kind"] == "tool_execution"
        assert message["params"]["truncated"] is True
        assert len(json.dumps(message, ensure_ascii=True).encode("utf-8")) <= 64 * 1024
    finally:
        if server._bus_task:
            server._bus_task.cancel()
            await asyncio.wait_for(asyncio.gather(server._bus_task, return_exceptions=True), timeout=2.0)
        await runtime.registry.unload_all()


@pytest.mark.asyncio
async def test_unrepresentable_question_aborts_and_cleans_pending_owned_turn(tmp_path: Path):
    runtime = await _runtime(
        tmp_path,
        [ToolCallEnd(
            id="call-oversized-question-cleanup",
            name="run_command",
            arguments={"command": "touch oversized-question-should-not-run", "cwd": ".", "wait_ms_before_async": 0},
        ), Done(stop_reason="tool_use")],
    )
    server = ACPServer(commands=runtime.commands, bus=runtime.bus)
    pending_approvals = runtime.bus.subscribe(
        types={"approval.required"}, interactive=False, max_queue_size=8,
    )
    reader = asyncio.StreamReader()
    writer = MockWriter()
    session_id = await _create_session(server)
    server_task = asyncio.create_task(server.run_stdio(reader, writer))
    try:
        await asyncio.wait_for(server._ready.wait(), timeout=1.0)
        _send(reader, {
            "jsonrpc": "2.0", "id": 2, "method": "prompt",
            "params": {"session_id": session_id, "prompt": "wait for approval", "options": {"provider": "scripted", "model": MODEL.id}},
        })
        approval_event = await asyncio.wait_for(pending_approvals.get(), timeout=2.0)
        approval_id = approval_event.data["approval_id"]

        # This is a transport-boundary injection: an internal question event too
        # large to represent must not become a partial, unanswerable question.
        runtime.bus.publish(Event(
            type="question.asked",
            session_id=session_id,
            data={
                "interaction_id": "oversized-interaction",
                "questions": [
                    {"id": f"question-{index}", "question": "Choose", "options": [
                        {"id": "a", "label": "A"}, {"id": "b", "label": "B"},
                    ]}
                    for index in range(40)
                ],
                "timeout_s": 30.0,
            },
        ))

        with pytest.raises(ConnectionError):
            await asyncio.wait_for(asyncio.shield(server_task), timeout=2.0)
        assert not any(message.get("method") == "question/request" for message in writer.lines)
        assert runtime.commands.gate.approvals.peek(approval_id) is None
        assert not (runtime.commands.workspace / "oversized-question-should-not-run").exists()
        assert not server._question_subscribers
        assert not runtime.bus.has_interactive_subscribers(session_id)
    finally:
        runtime.bus.unsubscribe(pending_approvals)
        if not server_task.done():
            reader.feed_eof()
            await asyncio.wait_for(server_task, timeout=2.0)
        await runtime.registry.unload_all()


@pytest.mark.asyncio
async def test_existing_requested_session_ids_are_never_claimed(tmp_path: Path):
    runtime = await _runtime(tmp_path)
    server = ACPServer(commands=runtime.commands, bus=runtime.bus)
    persisted = await runtime.commands.sessions_create(session_id="session_aaaaaaaaaaaa")
    try:
        duplicate = await server.handle_request({
            "jsonrpc": "2.0", "id": 1, "method": "session/new",
            "params": {"session_id": persisted["session_id"]},
        })
        assert duplicate["error"]["code"] == -32001
        assert server._owned_sessions == set()
    finally:
        await runtime.registry.unload_all()


@pytest.mark.asyncio
async def test_turn_output_redacts_configured_secret_and_reports_truncation(tmp_path: Path):
    secret = "configured-acp-secret-123456"
    runtime = await _runtime(tmp_path, [TextDelta(text=secret + "x" * 9000), Done(stop_reason="end_turn")])
    runtime.commands.secrets.set("ACP_TEST_SECRET", secret)
    server = ACPServer(commands=runtime.commands, bus=runtime.bus)
    try:
        session_id = await _create_session(server)
        response = await server.handle_request({
            "jsonrpc": "2.0", "id": 2, "method": "prompt",
            "params": {"session_id": session_id, "prompt": "large output", "options": {"provider": "scripted", "model": MODEL.id}},
        })
        assert response["result"]["status"] == "completed"
        assert response["result"]["output_truncated"] is True
        assert response["result"]["output"].endswith("…[truncated]")
        assert "[REDACTED SECRET]" in response["result"]["output"]
        assert secret not in response["result"]["output"]
    finally:
        await runtime.registry.unload_all()
