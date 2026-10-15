"""
An MCP tool executed through the real kernel (registry -> gate -> handler).

Regression for review/CODE_REVIEW_v0.2.1.md Addendum A.2: the bridge handlers
were written as `handler(call: ToolCall, ctx)` while the gate calls
`handler(args: dict, ctx)`, so every MCP tool failed inside the kernel with
"'dict' object has no attribute 'arguments'" although the plugin's unit tests
(which invoked the handler directly with a ToolCall) were green.

Also covers: the plugin reads its own settings namespace
(`plugins.bridge-mcp-client.servers`) and spec-standard annotations
(`readOnlyHint`) classify the tool as READ, so it runs without approval in the
default `ask` mode.
"""
from __future__ import annotations

import sys
from pathlib import Path

import pytest

from harness.kernel.bootstrap import bootstrap_kernel
from harness.sdk.testing.scripted import ScriptedProvider
from harness.sdk.types import (
    Done,
    ModelInfo,
    RiskClass,
    TextBlock,
    TextDelta,
    ToolCallEnd,
    ToolResultBlock,
)

ECHO_SERVER = Path(__file__).resolve().parents[1] / "fixtures" / "mcp_echo_server.py"
MODEL = ModelInfo(id="scripted-v1", display_name="Scripted", context_tokens=128000,
                  input_usd_per_mtok=0.0, output_usd_per_mtok=0.0)


@pytest.mark.asyncio
async def test_mcp_tool_call_executes_through_the_gate(tmp_path: Path):
    ws = tmp_path / "ws"
    ws.mkdir()
    conf = tmp_path / "conf"
    conf.mkdir()

    rt = await bootstrap_kernel(
        workspace=ws,
        config_dir=conf,
        overrides={
            # The plugin's own namespace (resolved by PluginSettingsView from the bare key "servers").
            "plugins.bridge-mcp-client.servers": {
                "echo": {"command": sys.executable, "args": [str(ECHO_SERVER)]},
            },
        },
    )
    try:
        spec = rt.registry.get_tool_spec("mcp__echo__echo")
        assert spec is not None, sorted(rt.registry.tools)
        # `readOnlyHint: true` in the fixture -> READ -> no approval needed in `ask` mode.
        assert spec.risk_class == RiskClass.READ
        assert rt.registry.get_tool_spec("mcp__echo__mutate_data").risk_class == RiskClass.DESTRUCTIVE

        provider = ScriptedProvider(models=[MODEL])
        provider.add_response([
            TextDelta(text="Calling echo."),
            ToolCallEnd(id="c1", name="mcp__echo__echo", arguments={"message": "through the kernel"}),
            Done(stop_reason="tool_use"),
        ])
        provider.add_response([TextDelta(text="Echo done."), Done(stop_reason="end_turn")])
        rt.registry.register_provider(provider, plugin_name="test_scripted")

        session = await rt.session_manager.create_session()
        result = await rt.runner.execute_turn(
            session=session.info,
            prompt="echo something via MCP",
            options={"provider": "scripted", "model": MODEL.id},
        )
        assert "Echo done." in result.output

        # The model saw a *successful* tool result containing the echoed text.
        tool_results = [
            b
            for m in provider.requests[1].messages
            for b in m.content
            if isinstance(b, ToolResultBlock)
        ]
        assert len(tool_results) == 1, provider.requests[1].messages
        text = "\n".join(c.text for c in tool_results[0].content if isinstance(c, TextBlock))
        assert "through the kernel" in text, text
        assert "MCP tool error" not in text and "Approval denied" not in text, text
        assert not tool_results[0].is_error
    finally:
        await rt.registry.unload_all()
