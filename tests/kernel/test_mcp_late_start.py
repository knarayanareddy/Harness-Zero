"""T21: late MCP recovery must update the actual callable kernel catalogue."""
from __future__ import annotations

import asyncio
import sys
from pathlib import Path
from typing import Any

import pytest

from harness.kernel.bootstrap import bootstrap_kernel
from harness.sdk.types import ToolCall

TOOL_NAME = "mcp__late__echo"

_STDIO_MCP_PEER = r'''
import json
import sys
from pathlib import Path

hold = Path(__file__).with_suffix(".hold")
if hold.exists():
    sys.exit(1)
catalog_mode = "initial"

def current_tools():
    if catalog_mode == "removed":
        return []
    description = "Updated echo definition." if catalog_mode == "redefined" else "Echo from late-start fixture."
    return [{"name": "echo", "description": description,
             "inputSchema": {"type": "object", "properties": {"message": {"type": "string"}}, "required": ["message"]},
             "annotations": {"readOnlyHint": True}}]

def tools_changed():
    print(json.dumps({"jsonrpc": "2.0", "method": "notifications/tools/list_changed", "params": {}}), flush=True)

def respond(request, result):
    print(json.dumps({"jsonrpc": "2.0", "id": request["id"], "result": result}), flush=True)

for line in sys.stdin:
    request = json.loads(line)
    method = request.get("method")
    if method == "initialize":
        respond(request, {"protocolVersion": "2025-03-26", "capabilities": {"tools": {"listChanged": True}},
                          "serverInfo": {"name": "late-start-fixture", "version": "1.0"}})
    elif method == "notifications/initialized":
        continue
    elif method == "tools/list":
        respond(request, {"tools": current_tools()})
    elif method == "tools/call":
        message = request.get("params", {}).get("arguments", {}).get("message", "")
        if message == "redefine-schema":
            catalog_mode = "redefined"
            tools_changed()
        elif message == "remove-tool":
            catalog_mode = "removed"
            tools_changed()
        elif message == "disconnect-after-response":
            hold.write_text("parent controls reconnect readiness")
        respond(request, {"content": [{"type": "text", "text": "echo: " + message}], "isError": False})
        if message == "disconnect-after-response":
            break
'''


async def _wait_for_event_data(subscriber: Any, predicate, timeout_s: float = 8.0):
    async with asyncio.timeout(timeout_s):
        while True:
            event = await subscriber.get()
            if predicate(event.data):
                return event


async def _call_registered_echo(runtime, message: str):
    return await runtime.gate.execute(
        ToolCall(id=f"late-{message}", name=TOOL_NAME, arguments={"message": message}),
        session_id="session_000000000001", cancel_event=asyncio.Event(),
    )


@pytest.mark.asyncio
async def test_late_stdio_server_registers_tools_in_kernel_once_and_reconnects_unchanged(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
):
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    config = tmp_path / "config"
    config.mkdir()
    peer = tmp_path / "late_stdio_peer"
    assert not peer.exists()
    runtime = await bootstrap_kernel(workspace=workspace, config_dir=config, resume_session=False,
        overrides={"plugins.bridge-mcp-client.servers": {
            "late": {"transport": "stdio", "command": sys.executable, "args": [str(peer)]},
        }})
    tools_changed = runtime.bus.subscribe(types=["mcp.tools.changed"], interactive=False)
    statuses = runtime.bus.subscribe(types=["mcp.server.status"], interactive=False)
    register_calls = []
    original_register = runtime.registry._register_tool

    def observe_registration(spec, handler, plugin_name):
        if spec.name == TOOL_NAME:
            register_calls.append(spec.name)
        return original_register(spec, handler, plugin_name)

    monkeypatch.setattr(runtime.registry, "_register_tool", observe_registration)
    try:
        assert runtime.registry.get_tool_spec(TOOL_NAME) is None
        peer.write_text(_STDIO_MCP_PEER, encoding="utf-8")
        await _wait_for_event_data(statuses, lambda d: d.get("server") == "late" and d.get("state") == "connected")
        # Plugin publishes this only after registry updates: use that lifecycle
        # boundary, not a sleep/poll loop or connection status alone.
        changed = await _wait_for_event_data(tools_changed, lambda d: d.get("server") == "late" and d.get("added") == ["echo"])
        assert changed.data["removed"] == []
        assert runtime.registry.get_tool_spec(TOOL_NAME) is not None
        assert [name for name in runtime.registry.tools if name.startswith("mcp__late__")] == [TOOL_NAME]
        assert register_calls == [TOOL_NAME]
        first = await _call_registered_echo(runtime, "through-kernel-registry")
        assert not first.is_error and first.content[0].text == "echo: through-kernel-registry"

        disconnected_result = await _call_registered_echo(runtime, "disconnect-after-response")
        assert not disconnected_result.is_error
        await _wait_for_event_data(statuses, lambda d: d.get("server") == "late" and d.get("state") == "reconnecting")
        assert peer.with_suffix(".hold").exists()
        disconnected = await _call_registered_echo(runtime, "during-disconnect")
        assert disconnected.is_error and "reconnecting" in disconnected.content[0].text
        peer.with_suffix(".hold").unlink()  # Only this synthetic readiness marker.
        await _wait_for_event_data(statuses, lambda d: d.get("server") == "late" and d.get("state") == "connected")
        recovered = await _call_registered_echo(runtime, "after-reconnect")
        assert not recovered.is_error and recovered.content[0].text == "echo: after-reconnect"
        assert tools_changed.empty(), "Unchanged reconnect must not re-register tools"
        assert register_calls == [TOOL_NAME]
        assert [name for name in runtime.registry.tools if name.startswith("mcp__late__")] == [TOOL_NAME]

        redefined = await _call_registered_echo(runtime, "redefine-schema")
        assert not redefined.is_error
        changed = await _wait_for_event_data(tools_changed, lambda d: d.get("server") == "late"
            and d.get("added") == ["echo"] and d.get("removed") == ["echo"])
        assert changed.data["added"] == ["echo"]
        assert runtime.registry.get_tool_spec(TOOL_NAME).description == "Updated echo definition."
        assert register_calls == [TOOL_NAME, TOOL_NAME]
        assert [name for name in runtime.registry.tools if name.startswith("mcp__late__")] == [TOOL_NAME]

        removed = await _call_registered_echo(runtime, "remove-tool")
        assert not removed.is_error
        changed = await _wait_for_event_data(tools_changed, lambda d: d.get("server") == "late" and d.get("removed") == ["echo"])
        assert changed.data["added"] == []
        assert runtime.registry.get_tool_spec(TOOL_NAME) is None
        assert [name for name in runtime.registry.tools if name.startswith("mcp__late__")] == []
    finally:
        runtime.bus.unsubscribe(tools_changed)
        runtime.bus.unsubscribe(statuses)
        await runtime.registry.unload_all()
