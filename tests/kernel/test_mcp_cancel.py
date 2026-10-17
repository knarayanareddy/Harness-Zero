"""An aborted MCP tool call sends notifications/cancelled and ignores its eventual reply."""
from __future__ import annotations

import asyncio

import pytest

from harness.plugins.bridges.mcp_client import MCPClient
from tests.fixtures.mcp_echo_http_server import MCP_STATE_KEY, start_server


async def _wait_for(predicate, timeout_s: float = 2.0) -> None:
    deadline = asyncio.get_running_loop().time() + timeout_s
    while asyncio.get_running_loop().time() < deadline:
        if predicate():
            return
        await asyncio.sleep(0.005)
    raise AssertionError("condition was not met before timeout")


@pytest.mark.asyncio
async def test_call_timeout_is_bounded_and_cancels_remote_request() -> None:
    server, url, app = await start_server(slow_delay_s=0.25)
    client = MCPClient(
        name="timeout_echo",
        transport="http",
        url=url,
        call_timeout_s=0.05,
        connect_timeout_s=1.0,
    )
    try:
        await client.start()
        started = asyncio.get_running_loop().time()
        with pytest.raises(TimeoutError, match="timed out"):
            await client.call_tool("slow_echo", {"message": "too slow"})
        assert asyncio.get_running_loop().time() - started < 0.2
        await _wait_for(lambda: bool(app[MCP_STATE_KEY]["cancelled"]))
        call_request = next(r for r in app[MCP_STATE_KEY]["requests"] if r["method"] == "tools/call")
        notification = app[MCP_STATE_KEY]["cancelled"][0]
        assert notification["requestId"] == call_request["id"]
        assert notification["reason"] == "MCP call timed out"
        assert not client._pending_requests

        recovered = await client.call_tool("echo", {"message": "still connected"}, timeout=1.0)
        assert recovered["content"][0]["text"] == "echo: still connected"
    finally:
        await client.stop()
        app[MCP_STATE_KEY]["stop_streams"].set()
        await server.cleanup()


@pytest.mark.asyncio
async def test_cancel_sends_notification_and_discards_late_result() -> None:
    server, url, app = await start_server(slow_delay_s=0.25)
    client = MCPClient(
        name="cancel_echo",
        transport="http",
        url=url,
        call_timeout_s=2.0,
        connect_timeout_s=1.0,
    )
    cancel_event = asyncio.Event()
    try:
        await client.start()
        task = asyncio.create_task(
            client.call_tool(
                "slow_echo",
                {"message": "this answer arrives too late"},
                cancel_event=cancel_event,
            )
        )
        await _wait_for(lambda: any(r["method"] == "tools/call" for r in app[MCP_STATE_KEY]["requests"]))
        call_request = next(r for r in app[MCP_STATE_KEY]["requests"] if r["method"] == "tools/call")
        cancel_event.set()
        with pytest.raises(asyncio.CancelledError):
            await asyncio.wait_for(task, timeout=0.5)

        await _wait_for(lambda: bool(app[MCP_STATE_KEY]["cancelled"]))
        notification = app[MCP_STATE_KEY]["cancelled"][0]
        assert notification["requestId"] == call_request["id"]
        assert notification["reason"] == "cancelled by harness"
        assert not client._pending_requests

        # Let the fixture's original response complete. The cancelled request has no pending future to resolve.
        await asyncio.sleep(0.3)
        assert not client._pending_requests
    finally:
        await client.stop()
        app[MCP_STATE_KEY]["stop_streams"].set()
        await server.cleanup()
