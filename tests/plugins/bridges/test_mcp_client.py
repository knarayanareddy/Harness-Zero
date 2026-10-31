"""
Unit, integration, and conformance tests for Model Context Protocol Client Bridge (§10, BRG-02, SIM-01, S14).
"""
from __future__ import annotations

import asyncio
import sys
from pathlib import Path

import pytest

from harness.plugins.bridges.mcp_client import (
    MCPClient,
    MCPClientBridgePlugin,
    create_mcp_tool_handler,
    create_mcp_tool_spec,
    derive_risk_class,
)
from harness.sdk.contracts import ToolSpec
from harness.sdk.events import McpServerStatus, SystemNotice
from harness.sdk.testing import ToolKit
from harness.sdk.types import RiskClass, ToolResult


class MockToolContext:
    def __init__(self, workspace: Path) -> None:
        self.call_id = "test_call"
        self.workspace = workspace
        self.data_dir = workspace
        self.capabilities = frozenset()
        self.cancel_event = asyncio.Event()


class MockPluginContext:
    def __init__(self, settings: dict | None = None) -> None:
        self.settings = settings or {}
        self.tools: list[tuple[ToolSpec, object]] = []
        self.events: list[object] = []

    def register_tool(self, spec: ToolSpec, handler: object) -> None:
        self.tools.append((spec, handler))

    def emit_event(self, payload: object) -> None:
        self.events.append(payload)


@pytest.mark.asyncio
@pytest.mark.parametrize("prior_bytes", [0, 4])
async def test_oversized_mcp_image_is_rejected_before_encoding_copy(tmp_path, prior_bytes):
    from harness.plugins.bridges.mcp_client.tools import MCP_IMAGE_DATA_B64_LIMIT

    copied = False

    class ObservedString(str):
        def encode(self, *args, **kwargs):
            nonlocal copied
            copied = True
            return super().encode(*args, **kwargs)

    # Observe the allocation boundary without allocating an enormous fixture or
    # pretending that this handler-level check bounds transport buffering.
    data = ObservedString("A" * (MCP_IMAGE_DATA_B64_LIMIT + (4 if not prior_bytes else 0)))
    content = ([{"type": "image", "mimeType": "image/png", "data": "AAAA"}] if prior_bytes else [])
    content.append({"type": "image", "mimeType": "image/png", "data": data})

    class Client:
        async def call_tool(self, name, arguments, *, cancel_event):
            return {"content": content, "isError": False}

    result = await create_mcp_tool_handler(Client(), "fixture")({}, MockToolContext(tmp_path))
    assert not copied, "Oversized input must be refused before making an ASCII copy"
    assert result.is_error
    assert "size limit" in result.output


@pytest.mark.asyncio
@pytest.mark.parametrize("content", [[{"type": "text", "text": ""}], [""], ["", {"type": "text", "text": ""}]])
async def test_empty_mcp_text_is_not_reported_as_success(tmp_path, content):
    class Client:
        async def call_tool(self, name, arguments, *, cancel_event):
            return {"content": content, "isError": False}

    result = await create_mcp_tool_handler(Client(), "fixture")({}, MockToolContext(tmp_path))
    assert result.is_error
    assert "no supported content" in result.output


@pytest.mark.asyncio
async def test_empty_mcp_text_does_not_hide_real_text_or_change_its_whitespace(tmp_path):
    class Client:
        async def call_tool(self, name, arguments, *, cancel_event):
            return {"content": ["", {"type": "text", "text": ""}, {"type": "text", "text": " \n "}], "isError": False}

    result = await create_mcp_tool_handler(Client(), "fixture")({}, MockToolContext(tmp_path))
    assert not result.is_error
    assert len(result.content) == 1
    assert result.content[0].text == " \n "


def test_derive_risk_class():
    """Maps MCP tool annotations to Harness RiskClass."""
    # Spec-standard ToolAnnotations names (MCP 2025-03-26)
    assert derive_risk_class({"readOnlyHint": True}) == RiskClass.READ
    assert derive_risk_class({"destructiveHint": True}) == RiskClass.DESTRUCTIVE
    assert derive_risk_class({"openWorldHint": True}) == RiskClass.NETWORK
    assert derive_risk_class({"readOnlyHint": False, "destructiveHint": False}) == RiskClass.WRITE
    # Legacy spellings still accepted
    assert derive_risk_class({"readOnly": True}) == RiskClass.READ
    assert derive_risk_class({"destructive": True}) == RiskClass.DESTRUCTIVE
    assert derive_risk_class({"openWorld": True}) == RiskClass.NETWORK
    assert derive_risk_class({}) == RiskClass.WRITE
    assert derive_risk_class(None) == RiskClass.WRITE


@pytest.mark.asyncio
async def test_mcp_stdio_roundtrip():
    """Verifies stdio JSON-RPC handshake, tool call, resource read, and prompt query against echo server."""
    echo_server_py = Path(__file__).resolve().parents[2] / "fixtures" / "mcp_echo_server.py"
    assert echo_server_py.is_file()

    client = MCPClient(
        name="test_echo",
        command=sys.executable,
        args=[str(echo_server_py)],
    )

    try:
        await client.start()
        assert client.status == "connected"
        assert client.last_error is None

        # 1. Tools list & call
        tools = await client.list_tools()
        assert len(tools) >= 3
        tool_names = [t["name"] for t in tools]
        assert "echo" in tool_names
        assert "mutate_data" in tool_names

        res_call = await client.call_tool("echo", {"message": "hello world"})
        assert res_call["content"][0]["text"] == "echo: hello world"
        assert res_call.get("isError") is False

        # Error tool
        res_fail = await client.call_tool("fail_tool", {})
        assert res_fail.get("isError") is True

        # 2. Resources list & read
        resources = await client.list_resources()
        assert len(resources) >= 1
        assert resources[0]["uri"] == "memo://today"

        res_read = await client.read_resource("memo://today")
        assert "memo://today" in res_read["contents"][0]["text"]

        # 3. Prompts list & get
        prompts = await client.list_prompts()
        assert len(prompts) >= 1
        assert prompts[0]["name"] == "summarize"

        res_prompt = await client.get_prompt("summarize")
        assert "messages" in res_prompt
    finally:
        await client.stop()
        assert client.status == "stopped"


@pytest.mark.asyncio
async def test_s14_mcp_bridge_plugin_and_disconnect_status(tmp_path: Path):
    """
    S14 Scenario:
    1. Configure fixture stdio echo server in settings.
    2. Plugin registers tools as mcp__<server>__<tool> with real schemas.
    3. Tool execution round-trips.
    4. Terminating the server enters reconnecting state, then recovers and serves tools again.
    """
    echo_server_py = Path(__file__).resolve().parents[2] / "fixtures" / "mcp_echo_server.py"

    settings = {
        "plugins.mcp-client.servers": {
            "echo": {
                "command": sys.executable,
                "args": [str(echo_server_py)],
            }
        }
    }

    ctx = MockPluginContext(settings=settings)
    plugin = MCPClientBridgePlugin()

    try:
        await plugin.setup(ctx)

        # Check status shows connected
        status_map = plugin.get_status()
        assert "echo" in status_map
        assert status_map["echo"]["status"] == "connected"
        assert any(isinstance(event, McpServerStatus) and event.state == "connected" for event in ctx.events)
        assert any(isinstance(event, SystemNotice) and event.code == "mcp.settings.deprecated" for event in ctx.events)

        # Check registered tools
        tool_specs = [spec for spec, _ in ctx.tools]
        tool_names = [spec.name for spec in tool_specs]

        assert "mcp__echo__echo" in tool_names
        assert "mcp__echo__mutate_data" in tool_names
        assert "read_resource" in tool_names

        # Verify annotation-derived risk class
        echo_spec = next(s for s in tool_specs if s.name == "mcp__echo__echo")
        assert echo_spec.risk_class == RiskClass.READ

        mutate_spec = next(s for s in tool_specs if s.name == "mcp__echo__mutate_data")
        assert mutate_spec.risk_class == RiskClass.DESTRUCTIVE

        # Execute tool through registered handler
        echo_handler = next(h for s, h in ctx.tools if s.name == "mcp__echo__echo")
        tool_ctx = MockToolContext(workspace=tmp_path)
        # Kernel contract: ToolGate invokes handler(args: dict, ctx) — not a ToolCall.
        result: ToolResult = await echo_handler({"message": "S14 test message"}, tool_ctx)

        assert not result.is_error
        assert "echo: S14 test message" in result.content[0].text

        # Test read_resource handler
        read_res_handler = next(h for s, h in ctx.tools if s.name == "read_resource")
        res_result: ToolResult = await read_res_handler({"uri": "memo://today"}, tool_ctx)
        assert not res_result.is_error
        assert "memo://today" in res_result.content[0].text

        # S14: A killed stdio process is surfaced as a reconnecting status and is relaunched.
        echo_client = plugin.clients["echo"]
        original_pid = echo_client._proc.pid if echo_client._proc else None
        if echo_client._proc:
            echo_client._proc.kill()
            await echo_client._proc.wait()

        # A transient disconnect is visible immediately, then the same configured server is relaunched.
        deadline = asyncio.get_running_loop().time() + 1.0
        while echo_client.get_status()["state"] != "reconnecting":
            assert asyncio.get_running_loop().time() < deadline, echo_client.get_status()
            await asyncio.sleep(0.01)
        assert echo_client.get_status()["attempt"] >= 0
        assert any(isinstance(event, McpServerStatus) and event.state == "reconnecting" for event in ctx.events)

        deadline = asyncio.get_running_loop().time() + 4.0
        while echo_client.status != "connected" or (
            original_pid is not None and echo_client._proc is not None and echo_client._proc.pid == original_pid
        ):
            assert asyncio.get_running_loop().time() < deadline, echo_client.get_status()
            await asyncio.sleep(0.05)
        recovered = await echo_client.call_tool("echo", {"message": "after reconnect"})
        assert recovered["content"][0]["text"] == "echo: after reconnect"

    finally:
        await plugin.teardown()
        assert len(plugin.clients) == 0


@pytest.mark.asyncio
async def test_toolkit_conformance_for_mcp_tools(tmp_path: Path):
    """Verifies generated MCP ToolSpecs against ToolKit."""
    kit = ToolKit()
    echo_server_py = Path(__file__).resolve().parents[2] / "fixtures" / "mcp_echo_server.py"

    client = MCPClient(
        name="echo",
        command=sys.executable,
        args=[str(echo_server_py)],
    )
    await client.start()

    try:
        tools = await client.list_tools()
        echo_mcp_tool = next(t for t in tools if t["name"] == "echo")
        spec = create_mcp_tool_spec("echo", echo_mcp_tool)
        handler = create_mcp_tool_handler(client, "echo")

        kit.test_declared_risk_class_bound(spec)

        tool_ctx = MockToolContext(workspace=tmp_path)
        res = await handler({"message": "conformance test"}, tool_ctx)
        assert not res.is_error
        assert "echo: conformance test" in res.content[0].text
    finally:
        await client.stop()
