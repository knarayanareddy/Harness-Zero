"""An aborted MCP tool call sends notifications/cancelled and ignores its eventual reply."""
from __future__ import annotations

import asyncio

import pytest
from aiohttp import web

from harness.plugins.bridges.mcp_client import MCPClient
from tests.fixtures.mcp_echo_http_server import MCP_STATE_KEY, start_server


async def _wait_for(predicate, timeout_s: float = 2.0) -> None:
    deadline = asyncio.get_running_loop().time() + timeout_s
    while asyncio.get_running_loop().time() < deadline:
        if predicate():
            return
        await asyncio.sleep(0.005)
    raise AssertionError("condition was not met before timeout")


async def _start_delayed_cancel_peer(*, ack_delay_s: float = 0.3, ack_status: int = 202):
    loop = asyncio.get_running_loop()
    state = {
        "remote_cancelled": asyncio.Event(),
        "cancel_acknowledged": asyncio.Event(),
        "tool_call_request_id": None,
        "cancelled": [],
    }

    async def post(request: web.Request) -> web.StreamResponse:
        message = await request.json()
        method = message.get("method")
        request_id = message.get("id")
        if method == "initialize":
            return web.json_response(
                {
                    "jsonrpc": "2.0",
                    "id": request_id,
                    "result": {
                        "protocolVersion": "2025-03-26",
                        "capabilities": {"tools": {}},
                        "serverInfo": {"name": "delayed-cancel-peer", "version": "1"},
                    },
                },
                headers={"Mcp-Session-Id": "delayed-cancel-session", "MCP-Protocol-Version": "2025-03-26"},
            )
        if method == "notifications/initialized":
            return web.Response(status=202)
        if method == "tools/list":
            return web.json_response(
                {
                    "jsonrpc": "2.0",
                    "id": request_id,
                    "result": {"tools": [{"name": "blocked", "inputSchema": {"type": "object"}}]},
                },
                headers={"Mcp-Session-Id": "delayed-cancel-session"},
            )
        if method == "tools/call":
            state["tool_call_request_id"] = request_id
            state["tool_call_entered_at"] = loop.time()
            await state["remote_cancelled"].wait()
            return web.json_response(
                {"jsonrpc": "2.0", "id": request_id, "result": {"content": [], "isError": False}},
                headers={"Mcp-Session-Id": "delayed-cancel-session"},
            )
        if method == "notifications/cancelled":
            state["cancelled"].append(message["params"])
            state["cancel_entered_at"] = loop.time()
            state["remote_cancelled"].set()
            await asyncio.sleep(ack_delay_s)
            state["cancel_acknowledged_at"] = loop.time()
            state["cancel_acknowledged"].set()
            return web.Response(status=ack_status)
        raise AssertionError(f"unexpected MCP method: {method}")

    async def get(_request: web.Request) -> web.Response:
        return web.Response(status=405)

    app = web.Application()
    app.router.add_post("/mcp", post)
    app.router.add_get("/mcp", get)
    server = web.AppRunner(app)
    await server.setup()
    site = web.TCPSite(server, "127.0.0.1", 0)
    await site.start()
    port = site._server.sockets[0].getsockname()[1]
    return server, f"http://127.0.0.1:{port}/mcp", state


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


@pytest.mark.asyncio
@pytest.mark.parametrize("cancel_via_event", [False, True])
async def test_cancellation_returns_promptly_and_sends_one_remote_notification(cancel_via_event: bool) -> None:
    server, url, app = await start_server(slow_delay_s=0.25)
    client = MCPClient(
        name="prompt_cancel_echo",
        transport="http",
        url=url,
        call_timeout_s=2.0,
        connect_timeout_s=1.0,
    )
    cancel_event = asyncio.Event()
    try:
        await client.start()
        task = asyncio.create_task(
            client.call_tool("slow_echo", {"message": "cancel promptly"}, cancel_event=cancel_event)
        )
        await _wait_for(lambda: any(r["method"] == "tools/call" for r in app[MCP_STATE_KEY]["requests"]))
        call_request = next(r for r in app[MCP_STATE_KEY]["requests"] if r["method"] == "tools/call")
        cancelled_at = asyncio.get_running_loop().time()
        if cancel_via_event:
            cancel_event.set()
        else:
            task.cancel()
        with pytest.raises(asyncio.CancelledError):
            await asyncio.wait_for(task, timeout=0.2)
        assert asyncio.get_running_loop().time() - cancelled_at < 0.2

        await _wait_for(lambda: bool(app[MCP_STATE_KEY]["cancelled"]))
        assert app[MCP_STATE_KEY]["cancelled"] == [
            {"requestId": call_request["id"], "reason": "cancelled by harness"}
        ]
        await _wait_for(lambda: not client._cancel_cleanup_tasks)
        assert not client._pending_requests
        assert not client._cancel_cleanup_tasks
    finally:
        await client.stop()
        app[MCP_STATE_KEY]["stop_streams"].set()
        await server.cleanup()


@pytest.mark.asyncio
async def test_timeout_deadline_is_not_extended_by_delayed_cancel_http_ack() -> None:
    server, url, state = await _start_delayed_cancel_peer()
    loop = asyncio.get_running_loop()
    client = MCPClient(
        name="delayed_cancel_ack",
        transport="http",
        url=url,
        call_timeout_s=1.0,
        connect_timeout_s=1.0,
    )
    try:
        await client.start()
        state["operation_started_at"] = loop.time()

        async def invoke() -> None:
            try:
                with pytest.raises(TimeoutError, match="timed out"):
                    await client.call_tool("blocked", timeout=0.03)
            finally:
                state["caller_returned_at"] = loop.time()

        await invoke()
        await asyncio.wait_for(state["cancel_acknowledged"].wait(), timeout=1.0)

        assert state["cancelled"] == [
            {"requestId": state["tool_call_request_id"], "reason": "MCP call timed out"}
        ]
        timings = {
            key: round(value - state["operation_started_at"], 3)
            for key, value in state.items()
            if key.endswith("_at")
        }
        assert state["caller_returned_at"] - state["operation_started_at"] < 0.2, (
            f"caller deadline extended by awaited cancellation acknowledgement: {timings}"
        )
        assert state["operation_started_at"] < state["tool_call_entered_at"]
        assert state["tool_call_entered_at"] < state["cancel_entered_at"]
        assert state["cancel_acknowledged_at"] - state["cancel_entered_at"] >= 0.28
        assert state["caller_returned_at"] < state["cancel_acknowledged_at"]
        await _wait_for(lambda: not client._cancel_cleanup_tasks)
        assert not client._cancel_cleanup_tasks
        await client.stop()
        assert not client._pending_requests
        assert not client._cancel_cleanup_tasks
    finally:
        state["remote_cancelled"].set()
        await client.stop()
        await server.cleanup()


@pytest.mark.asyncio
async def test_stop_settles_owned_cleanup_before_delayed_cancel_ack() -> None:
    server, url, state = await _start_delayed_cancel_peer(ack_delay_s=0.5)
    client = MCPClient(
        name="stop_delayed_cancel_ack",
        transport="http",
        url=url,
        call_timeout_s=1.0,
        connect_timeout_s=1.0,
    )
    try:
        await client.start()
        with pytest.raises(TimeoutError, match="timed out"):
            await client.call_tool("blocked", timeout=0.03)
        await asyncio.wait_for(state["remote_cancelled"].wait(), timeout=0.5)
        assert client._cancel_cleanup_tasks

        started = asyncio.get_running_loop().time()
        await asyncio.wait_for(client.stop(), timeout=0.2)
        assert asyncio.get_running_loop().time() - started < 0.2
        assert not client._pending_requests
        assert not client._cancel_cleanup_tasks
        assert not any(
            task.get_name().startswith("mcp-cancel-cleanup-")
            for task in asyncio.all_tasks()
            if task is not asyncio.current_task()
        )
    finally:
        state["remote_cancelled"].set()
        if client.status != "stopped":
            await client.stop()
        await server.cleanup()


@pytest.mark.asyncio
async def test_immediate_stop_does_not_drop_unstarted_remote_cancellation() -> None:
    server, url, state = await _start_delayed_cancel_peer(ack_delay_s=0.3)
    client = MCPClient(
        name="immediate_stop_cancel",
        transport="http",
        url=url,
        call_timeout_s=1.0,
        connect_timeout_s=1.0,
    )
    try:
        await client.start()
        loop = asyncio.get_running_loop()
        call_started = loop.time()
        with pytest.raises(TimeoutError, match="timed out"):
            await client.call_tool("blocked", timeout=0.03)
        caller_returned_at = loop.time()

        stop_started = loop.time()
        await asyncio.wait_for(client.stop(), timeout=0.8)
        stop_returned_at = loop.time()

        assert state["cancelled"] == [
            {"requestId": state["tool_call_request_id"], "reason": "MCP call timed out"}
        ]
        assert state["cancel_entered_at"] < stop_returned_at
        assert stop_returned_at - stop_started < 0.8
        await asyncio.wait_for(state["cancel_acknowledged"].wait(), timeout=0.5)
        assert state["cancel_acknowledged"].is_set()
        assert stop_returned_at < state["cancel_acknowledged_at"]
        assert caller_returned_at - call_started < 0.2
        assert not client._pending_requests
        assert not client._cancel_cleanup_tasks
        assert not any(
            task.get_name().startswith("mcp-cancel-cleanup-")
            for task in asyncio.all_tasks()
            if task is not asyncio.current_task()
        )
    finally:
        state["remote_cancelled"].set()
        if client.status != "stopped":
            await client.stop()
        await server.cleanup()


@pytest.mark.asyncio
async def test_cancel_notification_http_error_does_not_leak_owned_cleanup() -> None:
    server, url, state = await _start_delayed_cancel_peer(ack_delay_s=0, ack_status=503)
    client = MCPClient(
        name="failed_cancel_ack",
        transport="http",
        url=url,
        call_timeout_s=1.0,
        connect_timeout_s=1.0,
    )
    try:
        await client.start()
        started = asyncio.get_running_loop().time()
        with pytest.raises(TimeoutError, match="timed out"):
            await client.call_tool("blocked", timeout=0.03)
        await asyncio.wait_for(state["cancel_acknowledged"].wait(), timeout=0.5)
        await _wait_for(lambda: not client._cancel_cleanup_tasks)

        assert asyncio.get_running_loop().time() - started < 0.2
        assert state["cancelled"] == [
            {"requestId": state["tool_call_request_id"], "reason": "MCP call timed out"}
        ]
        assert client.status == "connected"
        assert not client._pending_requests
        assert not client._cancel_cleanup_tasks
    finally:
        state["remote_cancelled"].set()
        await client.stop()
        await server.cleanup()


@pytest.mark.asyncio
async def test_immediate_stop_settles_failed_cancel_ack_without_leaking_tasks() -> None:
    server, url, state = await _start_delayed_cancel_peer(ack_delay_s=0.3, ack_status=503)
    client = MCPClient(
        name="immediate_stop_failed_cancel_ack",
        transport="http",
        url=url,
        call_timeout_s=1.0,
        connect_timeout_s=1.0,
    )
    try:
        await client.start()
        loop = asyncio.get_running_loop()
        call_started = loop.time()
        with pytest.raises(TimeoutError, match="timed out"):
            await client.call_tool("blocked", timeout=0.03)
        caller_returned_at = loop.time()

        stop_started = loop.time()
        await asyncio.wait_for(client.stop(), timeout=0.8)
        stop_returned_at = loop.time()
        assert state["cancelled"] == [
            {"requestId": state["tool_call_request_id"], "reason": "MCP call timed out"}
        ]
        assert state["cancel_entered_at"] < stop_returned_at
        await asyncio.wait_for(state["cancel_acknowledged"].wait(), timeout=0.5)

        assert caller_returned_at - call_started < 0.2
        assert stop_returned_at - stop_started < 0.8
        assert stop_returned_at < state["cancel_acknowledged_at"]
        assert not client._pending_requests
        assert not client._cancel_cleanup_tasks
        assert not any(
            task.get_name().startswith("mcp-cancel-cleanup-")
            for task in asyncio.all_tasks()
            if task is not asyncio.current_task()
        )
    finally:
        state["remote_cancelled"].set()
        if client.status != "stopped":
            await client.stop()
        await server.cleanup()


@pytest.mark.asyncio
async def test_late_cleanup_cannot_notify_replacement_transport_unit_held_race() -> None:
    class HeldTransport:
        def __init__(self) -> None:
            self.alive = True
            self.sent: list[dict[str, object]] = []
            self.cancel_started = asyncio.Event()
            self.release_cancel = asyncio.Event()

        async def send(self, message: dict[str, object]) -> None:
            self.sent.append(message)
            if message.get("method") == "tools/call":
                try:
                    await asyncio.Event().wait()
                except asyncio.CancelledError:
                    self.cancel_started.set()
                    await self.release_cancel.wait()
                    raise

        async def close(self) -> None:
            self.alive = False

    old_transport = HeldTransport()
    new_transport = HeldTransport()
    client = MCPClient(name="held_connection_race", call_timeout_s=1.0)
    client.status = "connected"
    client._transport = old_transport
    try:
        with pytest.raises(TimeoutError, match="timed out"):
            await client.request("tools/call", {"name": "held"}, timeout=0.03)
        await asyncio.wait_for(old_transport.cancel_started.wait(), timeout=0.2)
        assert not client._pending_requests
        assert client._cancel_cleanup_tasks

        client._transport = new_transport
        old_transport.release_cancel.set()
        await _wait_for(lambda: not client._cancel_cleanup_tasks)
        assert not any(message.get("method") == "notifications/cancelled" for message in old_transport.sent)
        assert not any(message.get("method") == "notifications/cancelled" for message in new_transport.sent)
    finally:
        old_transport.release_cancel.set()
        await client.stop()
