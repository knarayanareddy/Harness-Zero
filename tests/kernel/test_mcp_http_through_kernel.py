"""Streamable HTTP MCP servers work through registry, gate and the real scripted model loop."""
from __future__ import annotations

import asyncio
from pathlib import Path

import pytest

from harness.kernel.bootstrap import bootstrap_kernel
from harness.kernel.context import derive_messages
from harness.kernel.secrets import Secrets
from harness.sdk.testing.scripted import ScriptedProvider
from harness.sdk.types import Done, ModelInfo, TextBlock, TextDelta, ToolCallEnd, ToolResultBlock
from tests.fixtures.mcp_echo_http_server import MCP_STATE_KEY, start_server

MODEL = ModelInfo(
    id="mcp-http-scripted",
    display_name="MCP HTTP Scripted",
    context_tokens=128_000,
    input_usd_per_mtok=0.0,
    output_usd_per_mtok=0.0,
)


@pytest.mark.parametrize("sse_responses", [False, True], ids=["json-response", "sse-response"])
@pytest.mark.asyncio
async def test_http_echo_tool_roundtrip(tmp_path: Path, sse_responses: bool) -> None:
    """MCP Streamable HTTP initialize, session header, tools/list, and tools/call work through the gate."""
    server, url, app = await start_server(sse_responses=sse_responses, emit_get_notification=True)
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    config = tmp_path / "config"
    config.mkdir()
    runtime = await bootstrap_kernel(
        workspace=workspace,
        config_dir=config,
        overrides={
            "kernel.security.permission_mode": "allow",
            "plugins.bridge-mcp-client.servers": {
                "http_echo": {"transport": "http", "url": url},
            },
            "plugins.bridge-mcp-client.call_timeout_s": 3.0,
            "plugins.bridge-mcp-client.connect_timeout_s": 2.0,
        },
        resume_session=False,
    )
    try:
        spec = runtime.registry.get_tool_spec("mcp__http_echo__echo")
        assert spec is not None, sorted(runtime.registry.tools)
        assert spec.risk_class.value == "read"

        provider = ScriptedProvider(models=[MODEL])
        provider.add_response([
            TextDelta(text="Calling the HTTP MCP echo."),
            ToolCallEnd(
                id="http-call-1",
                name="mcp__http_echo__echo",
                arguments={"message": "through streamable HTTP"},
            ),
            Done(stop_reason="tool_use"),
        ])
        provider.add_response([TextDelta(text="HTTP echo complete."), Done(stop_reason="end_turn")])
        runtime.registry.register_provider(provider, plugin_name="test_mcp_http")
        session = await runtime.session_manager.create_session()
        result = await runtime.runner.execute_turn(
            session=session.info,
            prompt="Echo via streamable HTTP",
            options={"provider": "scripted", "model": MODEL.id},
        )
        assert "HTTP echo complete." in result.output

        tool_results = [
            block
            for message in provider.requests[1].messages
            for block in message.content
            if isinstance(block, ToolResultBlock)
        ]
        assert len(tool_results) == 1, provider.requests[1].messages
        text = "\n".join(block.text for block in tool_results[0].content if isinstance(block, TextBlock))
        assert "echo: through streamable HTTP" in text
        assert not tool_results[0].is_error, text

        observed = app[MCP_STATE_KEY]["requests"]
        methods = [request["method"] for request in observed]
        assert methods[:3] == ["initialize", "notifications/initialized", "tools/list"]
        assert "tools/call" in methods
        init_headers = observed[0]["headers"]
        assert "Authorization" not in init_headers
        assert any(
            request["headers"].get("Mcp-Session-Id") in app[MCP_STATE_KEY]["sessions"]
            for request in observed
            if request["method"] in {"notifications/initialized", "tools/list", "tools/call"}
        )
    finally:
        await runtime.registry.unload_all()
        app[MCP_STATE_KEY]["stop_streams"].set()
        await server.cleanup()


@pytest.mark.asyncio
async def test_http_tool_list_changes_re_register_only_deltas(tmp_path: Path) -> None:
    """A tools/list_changed notification updates registrations and leaves a durable model-visible notice."""
    server, url, app = await start_server(emit_get_notification=True)
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    config = tmp_path / "config"
    config.mkdir()
    runtime = await bootstrap_kernel(
        workspace=workspace,
        config_dir=config,
        overrides={
            "kernel.security.permission_mode": "allow",
            "plugins.bridge-mcp-client.servers": {
                "http_echo": {"transport": "http", "url": url},
            },
            "plugins.bridge-mcp-client.connect_timeout_s": 1.0,
        },
        resume_session=False,
    )
    subscriber = runtime.bus.subscribe(types=["mcp.tools.changed"], interactive=False)
    session = await runtime.session_manager.create_session()
    try:
        assert runtime.registry.get_tool_spec("mcp__http_echo__echo") is not None
        assert runtime.registry.get_tool_spec("mcp__http_echo__extra") is None

        app[MCP_STATE_KEY]["include_extra_tool"] = True
        added = await asyncio.wait_for(subscriber.get(), timeout=3.0)
        assert added.type == "mcp.tools.changed"
        assert added.data["server"] == "http_echo"
        assert added.data["added"] == ["extra"]
        assert added.data["removed"] == []
        assert added.record is not None and added.record["kind"] == "system.notice"
        assert added.record["metadata"]["model_visible"] is True
        assert runtime.registry.get_tool_spec("mcp__http_echo__extra") is not None
        history = await session.get_history()
        assert any(
            record["kind"] == "system.notice"
            and record["metadata"].get("mcp_server") == "http_echo"
            and "extra" in record["content"]
            for record in history
        )
        model_messages = derive_messages(history)
        assert any(
            isinstance(block, TextBlock) and "tool list changed: added extra" in block.text
            for message in model_messages
            for block in message.content
        )

        # The fixture re-sends list_changed on each GET stream. Unchanged definitions must not re-register
        # tools or publish another changed event.
        await asyncio.sleep(0.65)
        assert subscriber.empty(), "an unchanged tools/list must not emit mcp.tools.changed"

        app[MCP_STATE_KEY]["include_extra_tool"] = False
        removed = await asyncio.wait_for(subscriber.get(), timeout=3.0)
        assert removed.data["added"] == []
        assert removed.data["removed"] == ["extra"]
        assert runtime.registry.get_tool_spec("mcp__http_echo__extra") is None
    finally:
        runtime.bus.unsubscribe(subscriber)
        await runtime.registry.unload_all()
        app[MCP_STATE_KEY]["stop_streams"].set()
        await server.cleanup()


@pytest.mark.asyncio
async def test_http_auth_resolves_secrets_and_environment_without_leaking_status(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """HTTP headers resolve secret references and bearer-env values without exposing them in diagnostics."""
    server, url, app = await start_server()
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    config = tmp_path / "config"
    config.mkdir()
    Secrets(config).set("MCP_API_KEY", "stored-secret-value")
    monkeypatch.setenv("MCP_FIXTURE_BEARER", "environment-secret-value")
    runtime = await bootstrap_kernel(
        workspace=workspace,
        config_dir=config,
        overrides={
            "kernel.security.permission_mode": "allow",
            "plugins.bridge-mcp-client.servers": {
                "secret_header": {
                    "transport": "http",
                    "url": url,
                    "headers": {"X-Api-Key": "${secrets.MCP_API_KEY}"},
                },
                "bearer_env": {
                    "transport": "http",
                    "url": url,
                    "auth": {"bearer_env": "MCP_FIXTURE_BEARER"},
                },
            },
        },
        resume_session=False,
    )
    try:
        settings_schema = await runtime.commands.settings_schema()
        assert "secrets.MCP_API_KEY" in settings_schema["properties"]
        requests = app[MCP_STATE_KEY]["requests"]
        assert any(
            r["method"] == "initialize" and r["headers"].get("X-Api-Key") == "stored-secret-value"
            for r in requests
        )
        assert any(
            r["method"] == "initialize"
            and r["headers"].get("Authorization") == "Bearer environment-secret-value"
            for r in requests
        )
        plugin = runtime.registry.get_loaded("bridge-mcp-client").instance
        diagnostics = str(plugin.get_status())
        assert "stored-secret-value" not in diagnostics
        assert "environment-secret-value" not in diagnostics
    finally:
        await runtime.registry.unload_all()
        app[MCP_STATE_KEY]["stop_streams"].set()
        await server.cleanup()
