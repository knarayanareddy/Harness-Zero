"""Small MCP Streamable HTTP echo server for transport, timeout and reconnect tests.

Can also run as a standalone fixture:

    python tests/fixtures/mcp_echo_http_server.py --host 127.0.0.1 --port 8931 --flaky 3

The ``--flaky`` mode drops every Nth ``tools/call`` request by returning 503. The in-process ``create_app``
entry point exposes state for deterministic tests without binding a fixed port.
"""
from __future__ import annotations

import argparse
import asyncio
import json
import uuid
from typing import Any

from aiohttp import web

MCP_STATE_KEY = web.AppKey("mcp_state", dict)


def _response(request_id: Any, *, result: Any = None, error: dict[str, Any] | None = None) -> dict[str, Any]:
    message: dict[str, Any] = {"jsonrpc": "2.0", "id": request_id}
    if error is not None:
        message["error"] = error
    else:
        message["result"] = result
    return message


def _tools(state: dict[str, Any]) -> list[dict[str, Any]]:
    tools = [
        {
            "name": "echo",
            "description": "Echoes back the input message",
            "inputSchema": {
                "type": "object",
                "properties": {"message": {"type": "string"}},
                "required": ["message"],
            },
            "annotations": {"readOnlyHint": True},
        },
        {
            "name": "slow_echo",
            "description": "Echoes after a controlled delay",
            "inputSchema": {
                "type": "object",
                "properties": {"message": {"type": "string"}},
                "required": ["message"],
            },
            "annotations": {"readOnlyHint": True},
        },
    ]
    if state.get("include_extra_tool"):
        tools.append(
            {
                "name": "extra",
                "description": "An optional tool used to test list changes",
                "inputSchema": {"type": "object", "properties": {}},
                "annotations": {"readOnlyHint": True},
            }
        )
    return tools


async def _sse_response(request: web.Request, message: dict[str, Any], headers: dict[str, str]) -> web.StreamResponse:
    response = web.StreamResponse(status=200, headers={**headers, "Content-Type": "text/event-stream"})
    await response.prepare(request)
    payload = json.dumps(message, separators=(",", ":"))
    await response.write(f"event: message\ndata: {payload}\n\n".encode())
    await response.write_eof()
    return response


async def _handle_post(request: web.Request) -> web.StreamResponse:
    state: dict[str, Any] = request.app[MCP_STATE_KEY]
    try:
        incoming = await request.json()
    except Exception:
        return web.json_response(_response(None, error={"code": -32700, "message": "Invalid JSON"}), status=400)

    method = incoming.get("method")
    request_id = incoming.get("id")
    params = incoming.get("params") or {}
    state["requests"].append({"method": method, "id": request_id, "params": params, "headers": dict(request.headers)})

    session_id = request.headers.get("Mcp-Session-Id")
    if method == "initialize":
        session_id = f"mcp-{uuid.uuid4().hex[:12]}"
        state["sessions"].add(session_id)
        result = {
            "protocolVersion": "2025-03-26",
            "capabilities": {"tools": {"listChanged": True}},
            "serverInfo": {"name": "harness-http-echo", "version": "1.0.0"},
        }
        return web.json_response(
            _response(request_id, result=result),
            headers={"Mcp-Session-Id": session_id, "MCP-Protocol-Version": "2025-03-26"},
        )

    if method == "notifications/initialized":
        return web.Response(status=202)

    if method == "notifications/cancelled":
        state["cancelled"].append(params)
        return web.Response(status=202)

    if session_id not in state["sessions"]:
        return web.Response(status=404, text="Unknown MCP session")

    result: Any
    if method == "tools/list":
        result = {"tools": _tools(state)}
    elif method == "tools/call":
        state["tool_calls"] += 1
        if state["flaky_every"] and state["tool_calls"] % state["flaky_every"] == 0:
            # Simulate a transient endpoint failure; a client must stop using this session and reconnect.
            return web.Response(status=503, text="fixture dropped the connection")
        tool_name = params.get("name")
        arguments = params.get("arguments") or {}
        if tool_name in {"echo", "slow_echo"}:
            if tool_name == "slow_echo" or arguments.get("delay"):
                await asyncio.sleep(float(state.get("slow_delay_s", 0.25)))
            result = {
                "content": [{"type": "text", "text": f"echo: {arguments.get('message', '')}"}],
                "isError": False,
            }
        elif tool_name == "extra":
            result = {"content": [{"type": "text", "text": "extra tool"}], "isError": False}
        else:
            return web.json_response(
                _response(request_id, error={"code": -32601, "message": f"Unknown tool {tool_name}"}),
                headers={"Mcp-Session-Id": session_id},
            )
    else:
        return web.json_response(
            _response(request_id, error={"code": -32601, "message": f"Unknown method {method}"}),
            headers={"Mcp-Session-Id": session_id},
        )

    message = _response(request_id, result=result)
    headers = {"Mcp-Session-Id": session_id}
    if state["sse_responses"]:
        return await _sse_response(request, message, headers)
    return web.json_response(message, headers=headers)


async def _handle_get(request: web.Request) -> web.StreamResponse:
    state: dict[str, Any] = request.app[MCP_STATE_KEY]
    session_id = request.headers.get("Mcp-Session-Id")
    if session_id not in state["sessions"]:
        return web.Response(status=404, text="Unknown MCP session")
    state["get_streams"] += 1
    response = web.StreamResponse(
        status=200,
        headers={"Content-Type": "text/event-stream", "Mcp-Session-Id": session_id},
    )
    await response.prepare(request)
    if state["emit_get_notification"]:
        payload = json.dumps({"jsonrpc": "2.0", "method": "notifications/tools/list_changed", "params": {}})
        await response.write(f"event: message\ndata: {payload}\n\n".encode())
    try:
        await asyncio.wait_for(state["stop_streams"].wait(), timeout=0.4)
    except TimeoutError:
        pass
    try:
        await response.write_eof()
    except ConnectionResetError:
        pass
    return response


def create_app(
    *,
    flaky_every: int = 0,
    slow_delay_s: float = 0.25,
    sse_responses: bool = False,
    include_extra_tool: bool = False,
    emit_get_notification: bool = False,
) -> web.Application:
    """Creates the test server app and attaches its observable state through ``MCP_STATE_KEY``."""
    app = web.Application()
    app[MCP_STATE_KEY] = {
        "flaky_every": max(0, int(flaky_every)),
        "slow_delay_s": max(0.0, float(slow_delay_s)),
        "sse_responses": bool(sse_responses),
        "include_extra_tool": bool(include_extra_tool),
        "emit_get_notification": bool(emit_get_notification),
        "requests": [],
        "sessions": set(),
        "cancelled": [],
        "tool_calls": 0,
        "get_streams": 0,
        "stop_streams": asyncio.Event(),
    }
    app.router.add_post("/mcp", _handle_post)
    app.router.add_get("/mcp", _handle_get)
    return app


async def start_server(
    *,
    host: str = "127.0.0.1",
    port: int = 0,
    **options: Any,
) -> tuple[web.AppRunner, str, web.Application]:
    """Starts an in-process server on an ephemeral port and returns ``(runner, url, app)``."""
    app = create_app(**options)
    runner = web.AppRunner(app)
    await runner.setup()
    site = web.TCPSite(runner, host, port)
    await site.start()
    sockets = getattr(site, "_server", None).sockets
    actual_port = int(sockets[0].getsockname()[1])
    return runner, f"http://{host}:{actual_port}/mcp", app


async def _shutdown(app: web.Application) -> None:
    app[MCP_STATE_KEY]["stop_streams"].set()


def main() -> None:
    parser = argparse.ArgumentParser(description="MCP Streamable HTTP echo fixture")
    parser.add_argument("--host", default="127.0.0.1")
    parser.add_argument("--port", type=int, default=8931)
    parser.add_argument("--flaky", type=int, default=0, help="Return 503 on every Nth tools/call")
    parser.add_argument("--sse", action="store_true", help="Return tool results as SSE instead of JSON")
    args = parser.parse_args()
    app = create_app(flaky_every=args.flaky, sse_responses=args.sse)
    app.on_shutdown.append(_shutdown)
    web.run_app(app, host=args.host, port=args.port)


if __name__ == "__main__":
    main()
