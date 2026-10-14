"""
Unit and integration tests for Agent Client Protocol (ACP) Bridge Frontend (§10, BRG-04).
"""
from __future__ import annotations

import asyncio
import json

import pytest

from harness.kernel.bus import EventBus
from harness.plugins.bridges.acp import ACPServer
from harness.sdk.events import Event


class MockCommands:
    def __init__(self) -> None:
        self.sessions: list[dict] = []
        self.resolved_approvals: list[dict] = []
        self.interrupted = False

    async def session_new(self, session_id: str | None = None, title: str | None = None) -> dict:
        sid = session_id or "sess_acp_1"
        data = {"session_id": sid, "title": title or "ACP Session"}
        self.sessions.append(data)
        return data

    async def session_list(self) -> list[dict]:
        return self.sessions

    async def run_turn(self, prompt: str, session_id: str | None = None, options: dict | None = None) -> object:
        class MockTurnResult:
            output = f"Executed prompt: {prompt}"
        return MockTurnResult()

    async def interrupt(self, session_id: str | None = None) -> dict:
        self.interrupted = True
        return {"status": "interrupted", "session_id": session_id}

    async def resolve_approval(self, approval_id: str, approved: bool, reason: str = "") -> dict:
        self.resolved_approvals.append({"approval_id": approval_id, "approved": approved, "reason": reason})
        return {"status": "resolved", "decision": "approved" if approved else "denied"}


@pytest.mark.asyncio
async def test_acp_protocol_request_handling():
    """Verifies core ACP request routing: initialize, session/new, prompt, cancel, and approvals."""
    cmds = MockCommands()
    bus = EventBus()
    server = ACPServer(commands=cmds, bus=bus)

    # 1. Initialize
    res_init = await server.handle_request({"jsonrpc": "2.0", "id": 1, "method": "initialize"})
    assert res_init["result"]["protocolVersion"] == "0.1"
    assert res_init["result"]["serverInfo"]["name"] == "harness-zero-acp"
    assert res_init["result"]["capabilities"]["streaming"] is True

    # 2. session/new
    res_session = await server.handle_request({
        "jsonrpc": "2.0",
        "id": 2,
        "method": "session/new",
        "params": {"session_id": "editor_sess_42", "title": "Editor Window 1"},
    })
    assert res_session["result"]["session_id"] == "editor_sess_42"

    # 3. prompt
    res_prompt = await server.handle_request({
        "jsonrpc": "2.0",
        "id": 3,
        "method": "prompt",
        "params": {"session_id": "editor_sess_42", "prompt": "Refactor auth logic"},
    })
    assert res_prompt["result"]["status"] == "completed"
    assert "Executed prompt: Refactor auth logic" in res_prompt["result"]["output"]

    # 4. cancel
    res_cancel = await server.handle_request({
        "jsonrpc": "2.0",
        "id": 4,
        "method": "cancel",
        "params": {"session_id": "editor_sess_42"},
    })
    assert res_cancel["result"]["status"] == "interrupted"
    assert cmds.interrupted is True

    # 5. approval/resolve
    res_approval = await server.handle_request({
        "jsonrpc": "2.0",
        "id": 5,
        "method": "approval/resolve",
        "params": {"request_id": "req_101", "approved": True, "reason": "User clicked Approve in editor"},
    })
    assert res_approval["result"]["status"] == "resolved"
    assert len(cmds.resolved_approvals) == 1
    assert cmds.resolved_approvals[0]["approval_id"] == "req_101"


@pytest.mark.asyncio
async def test_acp_bus_event_forwarding_notifications():
    """
    Verifies that EventBus events (text deltas, approvals) are forwarded as
    ACP JSON-RPC notifications over the stream writer.
    """
    cmds = MockCommands()
    bus = EventBus()
    server = ACPServer(commands=cmds, bus=bus)

    class MockWriter:
        def __init__(self):
            self.lines = []
            self.closed = False

        def write(self, data: bytes):
            for line in data.decode("utf-8").splitlines():
                if line.strip():
                    self.lines.append(json.loads(line))

        async def drain(self):
            pass

        def is_closing(self):
            return self.closed

    mock_writer = MockWriter()
    server._writer = mock_writer
    server._bus_task = asyncio.create_task(server._forward_bus_events())

    await asyncio.sleep(0.05)

    try:
        # Publish streaming text delta
        bus.publish(Event(type="model.text.delta", session_id="s1", data={"text": "chunk 1"}))
        await asyncio.sleep(0.05)

        # Publish approval request
        bus.publish(Event(
            type="approval.required",
            session_id="s1",
            data={
                "approval_id": "app_99",
                "action": "rm -rf build",
                "risk_class": "DESTRUCTIVE",
                "details": "Build directory cleanup",
            },
        ))
        await asyncio.sleep(0.05)

        notifs = mock_writer.lines
        assert len(notifs) >= 2

        delta_notif = next(n for n in notifs if n.get("method") == "turn/delta")
        assert delta_notif["params"]["delta"] == "chunk 1"
        assert delta_notif["params"]["session_id"] == "s1"

        app_notif = next(n for n in notifs if n.get("method") == "permission/request")
        assert app_notif["params"]["request_id"] == "app_99"
        assert app_notif["params"]["risk"] == "DESTRUCTIVE"

    finally:
        if server._bus_task:
            server._bus_task.cancel()
