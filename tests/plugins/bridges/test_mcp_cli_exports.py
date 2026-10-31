"""Actual CLI subprocess controls for MCP export authorization."""
from __future__ import annotations

import json
import os
import subprocess
import sys
from pathlib import Path
from types import SimpleNamespace

import pytest

REPO_ROOT = Path(__file__).resolve().parents[3]


def _run_mcp_cli(tmp_path: Path, settings: dict[str, object], unlisted_tool: str = "view_file") -> list[dict[str, object]]:
    workspace = tmp_path / "workspace"
    config_dir = tmp_path / "config"
    workspace.mkdir()
    config_dir.mkdir()
    (config_dir / "settings.json").write_text(json.dumps(settings), encoding="utf-8")
    (workspace / "mcp-denial-canary.txt").write_text("MCP_EXECUTOR_RAN", encoding="utf-8")
    env = os.environ.copy()
    env["HARNESS_CONFIG_DIR"] = str(config_dir)
    env["PYTHONPATH"] = str(REPO_ROOT) + os.pathsep + env.get("PYTHONPATH", "")
    requests = [
        {"jsonrpc": "2.0", "id": 1, "method": "initialize"},
        {"jsonrpc": "2.0", "id": 2, "method": "tools/list"},
        {"jsonrpc": "2.0", "id": 3, "method": "tools/call", "params": {
            "name": unlisted_tool,
            "arguments": {"file_path": "mcp-denial-canary.txt"} if unlisted_tool == "view_file"
            else {"uri": "file:///mcp-denial-canary.txt"},
        }},
    ]
    completed = subprocess.run(
        [sys.executable, "-m", "harness.cli", "mcp", "--workspace", str(workspace)],
        cwd=REPO_ROOT, env=env, input="\n".join(json.dumps(request) for request in requests) + "\n",
        text=True, capture_output=True, timeout=30, check=False,
    )
    assert completed.returncode == 0, completed.stderr
    return [json.loads(line) for line in completed.stdout.splitlines()]


def test_cli_empty_canonical_expose_denies_known_tool_call(tmp_path):
    responses = _run_mcp_cli(tmp_path, {"plugins": {"bridge-mcp-server": {"expose": []}}})
    called = next(response for response in responses if response.get("id") == 3)
    assert called["result"]["isError"] is True
    assert "not allowed or not found" in called["result"]["content"][0]["text"]
    assert "MCP_EXECUTOR_RAN" not in json.dumps(responses)
    listed = next(response for response in responses if response.get("id") == 2)
    assert listed["result"]["tools"] == []


def test_cli_canonical_expose_controls_list_and_unlisted_call(tmp_path):
    responses = _run_mcp_cli(tmp_path, {"plugins": {"bridge-mcp-server": {"expose": ["view_file"]}}}, "read_resource")
    listed = next(response for response in responses if response.get("id") == 2)
    assert [tool["name"] for tool in listed["result"]["tools"]] == ["view_file"]
    called = next(response for response in responses if response.get("id") == 3)
    assert called["result"]["isError"] is True
    assert "not allowed or not found" in called["result"]["content"][0]["text"]


def test_cli_legacy_expose_alias_remains_compatible(tmp_path):
    responses = _run_mcp_cli(tmp_path, {"plugins": {"mcp-server": {"expose": ["view_file"]}}}, "read_resource")
    listed = next(response for response in responses if response.get("id") == 2)
    assert [tool["name"] for tool in listed["result"]["tools"]] == ["view_file"]
    called = next(response for response in responses if response.get("id") == 3)
    assert called["result"]["isError"] is True
    assert "not allowed or not found" in called["result"]["content"][0]["text"]


def test_cli_canonical_empty_wins_conflict_with_legacy_alias(tmp_path):
    responses = _run_mcp_cli(tmp_path, {"plugins": {
        "bridge-mcp-server": {"expose": []}, "mcp-server": {"expose": ["view_file"]},
    }})
    listed = next(response for response in responses if response.get("id") == 2)
    assert listed["result"]["tools"] == []
    called = next(response for response in responses if response.get("id") == 3)
    assert called["result"]["isError"] is True
    assert "not allowed or not found" in called["result"]["content"][0]["text"]
    assert "MCP_EXECUTOR_RAN" not in json.dumps(responses)


def test_cli_missing_export_configuration_exposes_nothing(tmp_path):
    responses = _run_mcp_cli(tmp_path, {})
    assert next(r for r in responses if r.get("id") == 2)["result"]["tools"] == []
    assert "not allowed or not found" in next(r for r in responses if r.get("id") == 3)["result"]["content"][0]["text"]


def test_cli_explicit_canonical_list_wins_nonempty_conflict(tmp_path):
    responses = _run_mcp_cli(tmp_path, {"plugins": {
        "bridge-mcp-server": {"expose": ["view_file"]}, "mcp-server": {"expose": ["read_resource"]},
    }}, "read_resource")
    assert [tool["name"] for tool in next(r for r in responses if r.get("id") == 2)["result"]["tools"]] == ["view_file"]
    assert "not allowed or not found" in next(r for r in responses if r.get("id") == 3)["result"]["content"][0]["text"]


MISSING = object()


@pytest.mark.asyncio
@pytest.mark.parametrize("canonical,legacy,expected", [
    (MISSING, MISSING, []),
    ([], ["echo"], []),
    (["echo"], ["other"], ["echo"]),
    (MISSING, ["echo"], ["echo"]),
    (None, ["echo"], []),
    ([], MISSING, []),
])
async def test_plugin_and_cli_use_the_same_canonical_export_precedence(tmp_path, canonical, legacy, expected):
    from harness.kernel.settings import PluginSettingsView, SettingsStore
    from harness.plugins.bridges.mcp_server.plugin import MCPServerBridgePlugin
    from harness.sdk.types import ToolSpec

    settings = SettingsStore(tmp_path / "settings.json")
    schema = json.loads((REPO_ROOT / "harness/plugins/bridges/mcp_server/settings.schema.json").read_text())
    settings.register_schema("plugins.bridge-mcp-server", schema)
    if canonical is not MISSING:
        settings.set_override("plugins.bridge-mcp-server.expose", canonical)
    if legacy is not MISSING:
        settings.set_override("plugins.mcp-server.expose", legacy)
    spec = ToolSpec(name="echo", description="fixture", input_schema={"type": "object"})
    ctx = SimpleNamespace(settings=PluginSettingsView(settings, "plugins.bridge-mcp-server"),
                          registry=SimpleNamespace(tools={"echo": (spec, None, "fixture")}))
    plugin = MCPServerBridgePlugin()
    await plugin.setup(ctx)
    try:
        assert [tool.name for tool in plugin.server.get_exposed_tools()] == expected
    finally:
        await plugin.teardown()


@pytest.mark.parametrize("invalid", ["echo", {"echo": True}, ["echo", 42]])
def test_invalid_export_allowlist_is_not_interpreted_as_authorization(invalid):
    from harness.plugins.bridges.mcp_server.server import MCPServer

    with pytest.raises(ValueError, match="allow-list"):
        MCPServer(tools_provider=lambda: [], tool_executor=None, expose_list=invalid)
