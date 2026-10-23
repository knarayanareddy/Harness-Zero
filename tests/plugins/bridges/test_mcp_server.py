"""
Unit and integration tests for Model Context Protocol Server Bridge (§10, BRG-03).
Verifies round-trip with the MCP client bridge against itself.
"""
from __future__ import annotations

import sys
from pathlib import Path

import pytest

from harness.plugins.bridges.mcp_client import MCPClient
from harness.plugins.bridges.mcp_server import MCPServer
from harness.sdk.contracts import ToolCall, ToolSpec
from harness.sdk.types import RiskClass, TextBlock, ToolResult


@pytest.mark.asyncio
async def test_mcp_server_in_memory_protocol():
    """Unit test for MCPServer request handling without subprocess."""
    dummy_tools = [
        ToolSpec(
            name="echo_test",
            description="Echo test",
            input_schema={"type": "object", "properties": {"msg": {"type": "string"}}},
            risk_class=RiskClass.READ,
        )
    ]

    async def _exec(call: ToolCall) -> ToolResult:
        msg = call.arguments.get("msg", "")
        return ToolResult(content=[TextBlock(text=f"server: {msg}")])

    server = MCPServer(
        tools_provider=lambda: dummy_tools,
        tool_executor=_exec,
        expose_list=["echo_test"],
    )

    # 1. Initialize
    res_init = await server.handle_request({"method": "initialize", "id": 1})
    assert res_init["result"]["serverInfo"]["name"] == "harness-zero-mcp-server"

    # 2. Tools list
    res_list = await server.handle_request({"method": "tools/list", "id": 2})
    assert len(res_list["result"]["tools"]) == 1
    assert res_list["result"]["tools"][0]["name"] == "echo_test"
    assert res_list["result"]["tools"][0]["annotations"]["readOnly"] is True

    # 3. Tools call
    res_call = await server.handle_request({
        "method": "tools/call",
        "id": 3,
        "params": {"name": "echo_test", "arguments": {"msg": "hello from client"}},
    })
    assert res_call["result"]["content"][0]["text"] == "server: hello from client"
    assert res_call["result"]["isError"] is False


@pytest.mark.asyncio
async def test_mcp_server_client_roundtrip_stdio():
    """
    BRG-03 Integration Test:
    Spawns MCPServer as child process over stdio and connects MCPClient against it.
    """
    runner_py = Path(__file__).resolve().parents[2] / "fixtures" / "harness_mcp_server_runner.py"
    assert runner_py.is_file()

    client = MCPClient(
        name="harness_server_bridge",
        command=sys.executable,
        args=[str(runner_py)],
    )

    try:
        await client.start()
        assert client.status == "connected"

        # Discover tools
        tools = await client.list_tools()
        names = [t["name"] for t in tools]
        assert "math_add" in names
        assert "secret_admin_tool" in names

        # Call math_add tool
        res = await client.call_tool("math_add", {"a": 7, "b": 8})
        assert "Sum: 15" in res["content"][0]["text"]
        assert res.get("isError") is False
    finally:
        await client.stop()


@pytest.mark.asyncio
async def test_mcp_server_expose_allowlist_filtering():
    """
    Verifies that plugins.mcp-server.expose[] allow-list filters exposed tools
    and forbids unlisted tools.
    """
    runner_py = Path(__file__).resolve().parents[2] / "fixtures" / "harness_mcp_server_runner.py"

    # Pass --filter-math to instruct server runner to expose ONLY math_add
    client = MCPClient(
        name="harness_filtered_server",
        command=sys.executable,
        args=[str(runner_py), "--filter-math"],
    )

    try:
        await client.start()
        tools = await client.list_tools()
        names = [t["name"] for t in tools]

        # Only math_add is exposed
        assert "math_add" in names
        assert "secret_admin_tool" not in names

        # Calling unexposed tool returns isError: True
        res = await client.call_tool("secret_admin_tool", {})
        assert res.get("isError") is True
        assert "not allowed or not found" in res["content"][0]["text"]
    finally:
        await client.stop()
