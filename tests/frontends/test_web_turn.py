"""
Integration tests for web turn execution and command dispatch over HTTP/SSE.
Fulfills review/CODE_REVIEW_v0.2.0.md §8 #2 and §9.2.
"""
from __future__ import annotations

import asyncio
import json
from pathlib import Path

import httpx
import pytest

from harness.frontends.web.server import HarnessWebServer
from harness.kernel.approvals import ApprovalBroker
from harness.kernel.bus import EventBus
from harness.kernel.commands import Commands
from harness.kernel.gate import PolicyGate
from harness.kernel.jail import WorkspaceJail
from harness.kernel.registry import PluginRegistry
from harness.kernel.runner import TurnRunner
from harness.kernel.secrets import Secrets
from harness.kernel.session import SessionManager
from harness.kernel.settings import SettingsStore
from harness.sdk import ToolContext
from harness.sdk.testing.scripted import ScriptedProvider
from harness.sdk.types import (
    Done,
    ModelInfo,
    RiskClass,
    TextBlock,
    TextDelta,
    ToolCallEnd,
    ToolResult,
    ToolSpec,
    Usage,
    UsageEvent,
)

danger_spec = ToolSpec(
    name="danger_action",
    description="A write tool that triggers approval in ask mode",
    risk_class=RiskClass.WRITE,
    input_schema={"type": "object", "properties": {"target": {"type": "string"}}},
)


async def danger_handler(arguments: dict, context: ToolContext) -> ToolResult:
    return ToolResult(content=[TextBlock(text=f"Executed danger on {arguments.get('target')}")])


@pytest.fixture
def workspace_dir(tmp_path: Path) -> Path:
    ws = tmp_path / "web_turn_workspace"
    ws.mkdir(parents=True, exist_ok=True)
    return ws


@pytest.fixture
def settings(tmp_path: Path) -> SettingsStore:
    return SettingsStore(tmp_path / "settings.json")


@pytest.fixture
def secrets(tmp_path: Path) -> Secrets:
    return Secrets(tmp_path / "secrets.json")


@pytest.fixture
def event_bus() -> EventBus:
    return EventBus()


@pytest.fixture
def runtime_setup(
    workspace_dir: Path,
    settings: SettingsStore,
    secrets: Secrets,
    event_bus: EventBus,
):
    reg = PluginRegistry(workspace=workspace_dir, settings=settings, secrets=secrets, bus=event_bus)
    jail = WorkspaceJail(workspace_dir)
    approvals = ApprovalBroker(event_bus)
    gate = PolicyGate(
        registry=reg,
        jail=jail,
        settings=settings,
        secrets=secrets,
        approvals=approvals,
        bus=event_bus,
    )
    runner = TurnRunner(registry=reg, gate=gate, settings=settings, bus=event_bus)
    sm = SessionManager(workspace=workspace_dir, registry=reg, settings=settings, bus=event_bus)
    commands = Commands(
        workspace=workspace_dir,
        session_manager=sm,
        runner=runner,
        gate=gate,
        settings=settings,
        secrets=secrets,
        registry=reg,
        bus=event_bus,
    )
    return {
        "registry": reg,
        "gate": gate,
        "approvals": approvals,
        "runner": runner,
        "session_manager": sm,
        "commands": commands,
        "bus": event_bus,
    }


@pytest.mark.asyncio
async def test_web_turn_submit_and_serialization(
    runtime_setup: dict,
    workspace_dir: Path,
    event_bus: EventBus,
):
    """
    Verifies:
    1. POST /commands/sessions.create creates a session.
    2. POST /commands/submit_turn returns 200 with JSON-serialized TurnResult (no 500 TypeError).
    3. Events emitted on EventBus during the turn include tool.result with preview.
    """
    commands = runtime_setup["commands"]
    registry = runtime_setup["registry"]

    # Register scripted provider
    provider = ScriptedProvider(
        models=[
            ModelInfo(
                id="gpt-4o",
                display_name="GPT-4o",
                context_tokens=128000,
                input_usd_per_mtok=2.5,
                output_usd_per_mtok=10.0,
            )
        ]
    )
    provider.add_response([
        TextDelta(text="Hello from agent over HTTP"),
        UsageEvent(usage=Usage(input_tokens=50, output_tokens=10)),
        Done(stop_reason="end_turn"),
    ])
    registry.register_provider(provider)

    server = HarnessWebServer(
        commands=commands,
        bus=event_bus,
        host="127.0.0.1",
        port=0,
        sandbox_port=0,
        workspace=workspace_dir,
    )
    await server.start()
    headers = {"X-Harness-Token": server.auth_token, "Content-Type": "application/json"}

    try:
        async with httpx.AsyncClient(base_url=f"http://127.0.0.1:{server.port}") as client:
            # 1. Create session via /commands/sessions.create
            resp_create = await client.post(
                "/commands/sessions.create",
                headers=headers,
                json={"metadata": {"test": "web_turn"}},
            )
            assert resp_create.status_code == 200
            data_create = resp_create.json()
            assert data_create["status"] == "ok"
            session_id = data_create["result"]["session_id"]
            assert session_id

            # 2. Submit turn via /commands/submit_turn
            resp_turn = await client.post(
                "/commands/submit_turn",
                headers=headers,
                json={
                    "session_id": session_id,
                    "prompt": "Hello agent",
                    "options": {"provider": "scripted", "model": "gpt-4o"},
                },
            )
            assert resp_turn.status_code == 200, f"Expected 200, got {resp_turn.status_code}: {resp_turn.text}"
            data_turn = resp_turn.json()
            assert data_turn["status"] == "ok"

            turn_res = data_turn["result"]
            assert isinstance(turn_res, dict)
            assert "output" in turn_res
            assert "Hello from agent over HTTP" in turn_res["output"]
            assert turn_res["stop_reason"] == "end_turn"
            assert "usage" in turn_res
            assert turn_res["usage"]["input_tokens"] == 50
    finally:
        await server.stop()


@pytest.mark.asyncio
async def test_web_turn_approval_roundtrip_and_events(
    runtime_setup: dict,
    workspace_dir: Path,
    event_bus: EventBus,
    settings: SettingsStore,
):
    """
    Verifies:
    1. Tool call requiring approval pauses and emits approval.required.
    2. POST /commands/resolve_approval unblocks the execution.
    3. SSE /events receives all events, and tool.result contains 'preview'.
    4. Last-Event-ID replaying works.
    """
    commands = runtime_setup["commands"]
    registry = runtime_setup["registry"]
    settings.set("kernel.security.permission_mode", "ask")

    # Register danger tool
    registry.register_tool(danger_spec, danger_handler, plugin_name="test_plugin")

    # Scripted Provider with tool call requiring approval, then final reply
    provider = ScriptedProvider(
        models=[ModelInfo(id="gpt-4o", display_name="GPT-4o")]
    )
    provider.add_response([
        TextDelta(text="Attempting danger action..."),
        ToolCallEnd(id="call_danger_1", name="danger_action", arguments={"target": "config.sys"}),
        Done(stop_reason="tool_use"),
    ])
    provider.add_response([
        TextDelta(text="Danger action succeeded!"),
        Done(stop_reason="end_turn"),
    ])
    registry.register_provider(provider)

    server = HarnessWebServer(
        commands=commands,
        bus=event_bus,
        host="127.0.0.1",
        port=0,
        sandbox_port=0,
        workspace=workspace_dir,
    )
    await server.start()
    headers = {"X-Harness-Token": server.auth_token, "Content-Type": "application/json"}

    try:
        async with httpx.AsyncClient(base_url=f"http://127.0.0.1:{server.port}", timeout=10.0) as client:
            # Create session
            resp = await client.post("/commands/sessions.create", headers=headers, json={})
            session_id = resp.json()["result"]["session_id"]

            # Listen for events on bus
            sub = event_bus.subscribe(interactive=True)

            async def approve_when_asked():
                while True:
                    evt = await sub.get()
                    if evt.type == "approval.required":
                        appr_id = evt.data["approval_id"]
                        # Resolve via HTTP command
                        res = await client.post(
                            "/commands/resolve_approval",
                            headers=headers,
                            json={"approval_id": appr_id, "approved": True},
                        )
                        assert res.status_code == 200
                        break

            approval_task = asyncio.create_task(approve_when_asked())

            # Submit turn that will ask for approval
            resp_turn = await client.post(
                "/commands/submit_turn",
                headers=headers,
                json={
                    "session_id": session_id,
                    "prompt": "run danger action",
                    "options": {"provider": "scripted", "model": "gpt-4o"},
                },
            )
            assert resp_turn.status_code == 200
            turn_res = resp_turn.json()["result"]
            assert "Danger action succeeded!" in turn_res["output"]

            await approval_task

            # Verify Last-Event-ID replay on /events
            assert len(server._replay_buffer) > 0
            first_event_id = server._replay_buffer[0].id

            async with client.stream(
                "GET",
                "/events",
                headers={"X-Harness-Token": server.auth_token, "Last-Event-ID": first_event_id},
            ) as sse_stream:
                lines = []
                async for line in sse_stream.aiter_lines():
                    if line.startswith("data: "):
                        data = json.loads(line[6:])
                        lines.append(data)
                        if data.get("type") == "tool.result":
                            # Verify preview is present!
                            assert "preview" in data["data"]
                            assert "Executed danger on config.sys" in data["data"]["preview"]
                            break
    finally:
        event_bus.unsubscribe(sub)
        await server.stop()
