"""MCP stdio connections recover after EOF, while tool calls fail quickly during backoff."""
from __future__ import annotations

import asyncio
import sys
import time
from pathlib import Path

import pytest

from harness.plugins.bridges.mcp_client import MCPClient
from tests.fixtures.mcp_echo_http_server import MCP_STATE_KEY, start_server

ECHO_SERVER = Path(__file__).resolve().parents[1] / "fixtures" / "mcp_echo_server.py"


async def _wait_for_state(client: MCPClient, state: str, timeout_s: float = 5.0) -> None:
    deadline = asyncio.get_running_loop().time() + timeout_s
    while asyncio.get_running_loop().time() < deadline:
        if client.get_status().get("state") == state:
            return
        await asyncio.sleep(0.01)
    raise AssertionError(f"MCP state did not become {state!r}: {client.get_status()}")


async def _kill_server(client: MCPClient) -> None:
    proc = client._proc
    assert proc is not None
    proc.kill()
    await proc.wait()
    await _wait_for_state(client, "reconnecting", timeout_s=1.0)


@pytest.mark.asyncio
async def test_server_restart_reconnects_and_tools_still_work() -> None:
    client = MCPClient(
        name="restart_echo",
        command=sys.executable,
        args=[str(ECHO_SERVER)],
        reconnect_max_attempts=5,
        max_backoff_s=2.0,
    )
    try:
        await client.start()
        initial_tools = await client.list_tools()
        assert "echo" in {tool["name"] for tool in initial_tools}
        first = await client.call_tool("echo", {"message": "before restart"})
        assert first["content"][0]["text"] == "echo: before restart"

        await _kill_server(client)
        await _wait_for_state(client, "connected", timeout_s=6.0)
        new_tools = await client.list_tools()
        assert "echo" in {tool["name"] for tool in new_tools}
        second = await client.call_tool("echo", {"message": "after restart"})
        assert second["content"][0]["text"] == "echo: after restart"
    finally:
        await client.stop()


@pytest.mark.asyncio
async def test_calls_while_disconnected_fail_fast() -> None:
    client = MCPClient(
        name="fast_fail_echo",
        command=sys.executable,
        args=[str(ECHO_SERVER)],
        reconnect_max_attempts=5,
        max_backoff_s=2.0,
    )
    try:
        await client.start()
        await _kill_server(client)
        started = time.monotonic()
        with pytest.raises(ConnectionError, match="reconnecting"):
            await client.call_tool("echo", {"message": "do not hang"})
        assert time.monotonic() - started < 0.2
        assert client.get_status()["attempt"] >= 0
    finally:
        await client.stop()


@pytest.mark.asyncio
async def test_streamable_http_server_session_recovers_after_transient_503() -> None:
    server, url, app = await start_server(flaky_every=1)
    changes: list[tuple[list[str], list[str]]] = []

    def on_tools_changed(_client, _tools, added, removed) -> None:
        changes.append((added, removed))

    client = MCPClient(
        name="http_reconnect_echo",
        transport="http",
        url=url,
        connect_timeout_s=1.0,
        call_timeout_s=1.0,
        reconnect_max_attempts=5,
        reconnect_backoff_s=0.02,
        reconnect_max_backoff_s=0.1,
        on_tools_changed=on_tools_changed,
    )
    try:
        await client.start()
        assert client.get_status()["session_id_present"] is True
        initial_sessions = set(app[MCP_STATE_KEY]["sessions"])
        initial_tools = await client.list_tools()
        assert {tool["name"] for tool in initial_tools} == {"echo", "slow_echo"}

        with pytest.raises(ConnectionError):
            await client.call_tool("echo", {"message": "trigger 503"})
        await _wait_for_state(client, "reconnecting", timeout_s=1.0)
        app[MCP_STATE_KEY]["flaky_every"] = 0
        await _wait_for_state(client, "connected", timeout_s=2.0)

        assert app[MCP_STATE_KEY]["sessions"] > initial_sessions
        assert not changes, "an unchanged tools/list after reconnect must not re-register tools"
        recovered = await client.call_tool("echo", {"message": "after HTTP reconnect"})
        assert recovered["content"][0]["text"] == "echo: after HTTP reconnect"
    finally:
        await client.stop()
        app[MCP_STATE_KEY]["stop_streams"].set()
        await server.cleanup()
