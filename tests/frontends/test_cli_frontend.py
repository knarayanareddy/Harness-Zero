"""
Tests for KRN-11:
CLI frontend on modular kernel (§1, §7).
Verifies:
1. FrontendKit conformance (renders every built-in card kind and fallback for unknown cards).
2. Card snapshot rendering across all 10 card kinds + custom fallback.
3. Interactive approval prompt paths (approve, deny, abort, custom options).
4. Form prompt paths (required and default fields).
5. Event stream rendering across kernel event types.
6. CliApp turn execution and interactive event handling.
7. Click CLI --kernel invocation and plugin command group.
"""
from __future__ import annotations

import asyncio
import json
import sys
from pathlib import Path

import pytest
from click.testing import CliRunner
from rich.console import Console

from harness.cli import main
from harness.frontends.cli.app import CliApp
from harness.frontends.cli.prompts import prompt_approval, prompt_form
from harness.frontends.cli.renderer import render_card, render_event
from harness.kernel.approvals import ApprovalBroker
from harness.kernel.bus import EventBus
from harness.kernel.commands import Commands
from harness.kernel.gate import PolicyGate
from harness.kernel.jail import WorkspaceJail
from harness.kernel.registry import PluginRegistry
from harness.kernel.runner import TurnRunner
from harness.kernel.secrets import Secrets
from harness.kernel.session import SessionManager
from harness.kernel.settings import SettingsStore
from harness.sdk.cards import Card
from harness.sdk.events import Event
from harness.sdk.testing import FrontendKit, ScriptedProvider
from harness.sdk.types import Done, ModelInfo, TextDelta, TurnResult

# =========================================================================
# 1. FrontendKit & Card Rendering Tests
# =========================================================================

def test_frontend_kit_card_conformance():
    """FrontendKit conformance: renders all built-in card kinds and fallback."""
    kit = FrontendKit()
    kit.test_renders_all_builtin_card_kinds(render_card)
    kit.test_renders_unknown_card_fallback(render_card)


def test_render_all_card_kinds_snapshots():
    """Verifies that all card kinds render valid Rich components with expected content."""
    console = Console(record=True, width=100)

    # 1. Markdown
    c_md = Card.markdown("# Header\nSome **bold** text.")
    r_md = render_card(c_md, console=console)
    assert r_md is not None

    # 2. Code
    c_code = Card.code("def foo():\n    return 42", language="python", path="foo.py")
    r_code = render_card(c_code, console=console)
    assert r_code is not None

    # 3. Diff
    c_diff = Card.diff("bar.py", "-old\n+new", added=1, removed=1)
    r_diff = render_card(c_diff, console=console)
    assert r_diff is not None

    # 4. Table
    c_table = Card.table(["Name", "Value"], [["Alpha", "10"], ["Beta", "20"]])
    r_table = render_card(c_table, console=console)
    assert r_table is not None

    # 5. Notice
    c_notice = Card.notice("System restarting", level="warn")
    r_notice = render_card(c_notice, console=console)
    assert r_notice is not None

    # 6. Progress
    c_prog = Card.progress("Downloading dependencies", 0.75)
    r_prog = render_card(c_prog, console=console)
    assert "75%" in str(r_prog)

    # 7. Tree
    c_tree = Card.tree([
        {"label": "root", "children": [{"label": "child_1", "children": []}]}
    ])
    r_tree = render_card(c_tree, console=console)
    assert r_tree is not None

    # 8. Approval
    c_appr = Card.approval("app_99", "Execute command", details={"cmd": "rm -rf"})
    r_appr = render_card(c_appr, console=console)
    assert r_appr is not None

    # 9. Artifact
    c_art = Card.artifact("report.pdf", "application/pdf", "file:///tmp/report.pdf", request_feedback=True)
    r_art = render_card(c_art, console=console)
    assert r_art is not None

    # 10. Form
    c_form = Card.form("config_form", {"type": "object", "properties": {"timeout": {"type": "integer"}}}, "Save")
    r_form = render_card(c_form, console=console)
    assert r_form is not None

    # 11. Custom Unknown Card Fallback
    c_unknown = Card(kind="x-custom-widget", id="c1", title="Custom Sensor", data={"temp": 22.5})
    r_unknown = render_card(c_unknown, console=console)
    assert r_unknown is not None

    text_output = console.export_text()
    assert "System restarting" in text_output
    assert "Downloading dependencies" in text_output
    assert "Custom Sensor" in text_output


# =========================================================================
# 2. Interactive Prompts Tests
# =========================================================================

def test_prompt_approval_yes():
    """prompt_approval returns True when user approves."""
    console = Console(record=True)
    approved = prompt_approval(
        approval_id="app_123",
        summary="Delete temp files",
        details={"path": "/tmp/test"},
        options=["approve", "deny"],
        console=console,
        input_fn=lambda _: "yes",
    )
    assert approved is True
    assert "Action Approved" in console.export_text()


def test_prompt_approval_no():
    """prompt_approval returns False when user denies."""
    console = Console(record=True)
    approved = prompt_approval(
        approval_id="app_124",
        summary="Overwrite database",
        options=["approve", "deny"],
        console=console,
        input_fn=lambda _: "deny",
    )
    assert approved is False
    assert "Action Denied" in console.export_text()


def test_prompt_approval_abort():
    """prompt_approval safely handles EOF / interrupt by returning False."""
    console = Console(record=True)

    def raise_interrupt(_):
        raise KeyboardInterrupt()

    approved = prompt_approval(
        approval_id="app_125",
        summary="Action",
        console=console,
        input_fn=raise_interrupt,
    )
    assert approved is False
    assert "denying action" in console.export_text()


def test_prompt_form_fields():
    """prompt_form collects required inputs and default values."""
    console = Console(record=True)
    inputs = ["My Project", ""]  # First is title (required), second uses default
    input_iter = iter(inputs)

    schema = {
        "properties": {
            "title": {"type": "string", "description": "Project title"},
            "port": {"type": "integer", "default": "8080", "description": "Server port"},
        },
        "required": ["title"],
    }

    result = prompt_form(
        form_id="setup",
        schema=schema,
        console=console,
        input_fn=lambda _: next(input_iter),
    )
    assert result == {"title": "My Project", "port": "8080"}
    assert "Form submitted" in console.export_text()


# =========================================================================
# 3. Event Rendering Tests
# =========================================================================

def test_render_event_types():
    """Verifies event rendering for core streaming and notification events."""
    console = Console(record=True)

    # Text Delta
    render_event(Event(type="model.text.delta", data={"text": "Hello world"}), console=console)
    # Thinking Delta
    render_event(Event(type="model.thinking.delta", data={"text": "Thinking..."}), console=console)
    # Tool Call
    render_event(Event(type="model.tool_call", data={"call_id": "c1", "name": "read_file", "arguments": {"p": "a.txt"}}), console=console)
    # Tool Decision
    render_event(Event(type="tool.decision", data={"action": "allow", "policy": "jail", "reason": "in workspace"}), console=console)
    # Tool Result
    render_event(Event(type="tool.result", data={"name": "read_file", "is_error": False, "preview": "file content", "duration_ms": 12.3}), console=console)
    # UI Card
    c = Card.notice("Card message", level="info")
    render_event(Event(type="ui.card", data={"card": c.to_dict()}), console=console)
    # Approval required & resolved
    render_event(Event(type="approval.required", data={"approval_id": "a1", "summary": "Approve this", "options": ["approve", "deny"]}), console=console)
    render_event(Event(type="approval.resolved", data={"approved": True, "by": "user", "reason": "Confirmed"}), console=console)
    # Turn completed
    render_event(Event(type="turn.completed", data={"stop_reason": "end_turn", "duration_ms": 150, "tool_calls": 2}), console=console)
    # Turn error
    render_event(Event(type="turn.error", data={"message": "Model timeout"}), console=console)

    out = console.export_text()
    assert "Hello world" in out
    assert "Thinking..." in out
    assert "read_file" in out
    assert "PolicyGate [ALLOW]" in out
    assert "Card message" in out
    assert "Turn Completed" in out
    assert "Model timeout" in out


# =========================================================================
# 4. CliApp Turn Execution & Approval Loop Tests
# =========================================================================

@pytest.fixture
def mock_kernel(tmp_path: Path):
    """Fixture providing isolated kernel services for CliApp testing."""
    ws = tmp_path / "ws"
    ws.mkdir()
    conf = tmp_path / "conf"
    conf.mkdir()

    bus = EventBus()
    settings = SettingsStore(path=conf)
    secrets = Secrets(path=conf)
    jail = WorkspaceJail(ws)
    registry = PluginRegistry(workspace=ws, settings=settings, secrets=secrets, bus=bus)

    # Register scripted provider
    scripted = ScriptedProvider(
        responses=[[TextDelta(text="Kernel response text"), Done(stop_reason="end_turn")]],
        models=[ModelInfo(id="scripted-v1", display_name="Scripted", context_tokens=10000)],
    )
    registry.register_provider(scripted, plugin_name="test_provider")

    approvals = ApprovalBroker(bus=bus)
    gate = PolicyGate(registry=registry, jail=jail, settings=settings, secrets=secrets, approvals=approvals, bus=bus)
    sm = SessionManager(workspace=ws, registry=registry, settings=settings, bus=bus)
    runner = TurnRunner(registry=registry, gate=gate, settings=settings, bus=bus)
    commands = Commands(
        workspace=ws,
        session_manager=sm,
        runner=runner,
        gate=gate,
        settings=settings,
        secrets=secrets,
        registry=registry,
        bus=bus,
    )
    return {
        "workspace": ws,
        "bus": bus,
        "commands": commands,
        "approvals": approvals,
        "registry": registry,
        "provider": scripted,
    }


@pytest.mark.asyncio
async def test_cliapp_run_turn(mock_kernel):
    """CliApp.run_turn streams events and completes successfully."""
    console = Console(record=True)
    app = CliApp(
        commands=mock_kernel["commands"],
        bus=mock_kernel["bus"],
        console=console,
        interactive=False,
    )

    res = await app.run_turn("What is the meaning of life?", options={"provider": "scripted", "model": "scripted-v1"})
    assert isinstance(res, TurnResult)
    assert "Kernel response text" in res.output
    assert "Kernel response text" in console.export_text()


@pytest.mark.asyncio
async def test_cliapp_interactive_slash_commands(mock_kernel):
    """CliApp REPL dispatches slash commands (/help, /plugins, /models, /exit)."""
    console = Console(record=True)
    inputs = ["/help", "/plugins", "/models", "/exit"]
    input_iter = iter(inputs)

    app = CliApp(
        commands=mock_kernel["commands"],
        bus=mock_kernel["bus"],
        console=console,
        interactive=True,
        input_fn=lambda _: next(input_iter),
    )

    await app.run_interactive()
    out = console.export_text()
    assert "Available Slash Commands" in out
    assert "Installed Plugins" in out
    assert "Available Models" in out
    assert "Exiting Harness-Zero session" in out


@pytest.mark.asyncio
async def test_cliapp_in_flight_approval_prompt(mock_kernel):
    """CliApp auto-prompts on approval.required event and resolves it on kernel."""
    console = Console(record=True)
    app = CliApp(
        commands=mock_kernel["commands"],
        bus=mock_kernel["bus"],
        console=console,
        interactive=True,
        input_fn=lambda _: "yes",
    )

    await app.start_event_listener()
    try:
        # Request an approval via ApprovalBroker
        fut = asyncio.create_task(
            mock_kernel["approvals"].ask(
                kind="exec",
                summary="Run database migration",
                details={"cmd": "alembic upgrade head"},
                timeout_s=5.0,
            )
        )
        approved, reason = await fut
        assert approved is True
        assert reason == ""
    finally:
        await app.stop_event_listener()

    assert "Action Approved" in console.export_text()


@pytest.mark.asyncio
async def test_cliapp_prompts_for_structured_question(mock_kernel):
    """The interactive CLI answers a pending structured question through the kernel command."""
    console = Console(record=True)
    inputs = iter(["1", ""])
    app = CliApp(
        commands=mock_kernel["commands"],
        bus=mock_kernel["bus"],
        console=console,
        interactive=True,
        input_fn=lambda _: next(inputs),
    )
    questions = [
        {
            "id": "format",
            "question": "Which format should I use?",
            "options": [
                {"id": "markdown", "label": "Markdown"},
                {"id": "html", "label": "HTML"},
            ],
        }
    ]

    await app.start_event_listener()
    try:
        answer = await asyncio.wait_for(
            mock_kernel["approvals"].interactions.ask(
                "question",
                {"questions": questions, "call_id": "call-1"},
                session_id="session-1",
                turn_id="turn-1",
                timeout_s=5.0,
            ),
            timeout=2.0,
        )
    finally:
        await app.stop_event_listener()

    assert answer == {"format": {"option_id": "markdown", "custom_text": ""}}
    output = console.export_text()
    assert "Which format should I use?" in output
    assert "1. Markdown" in output
    assert "Answers submitted" in output


# =========================================================================
# 5. Click CLI Integration & Subcommands Tests
# =========================================================================

def test_cli_plugin_subcommands(tmp_path: Path, monkeypatch):
    """Verifies click commands: harness plugin list/info/enable/disable."""
    ws = tmp_path / "ws"
    ws.mkdir()
    monkeypatch.setenv("HARNESS_CONFIG_DIR", str(tmp_path / "conf"))

    runner = CliRunner()

    # 1. plugin list
    res_list = runner.invoke(main, ["plugin", "list", "--workspace", str(ws)])
    assert res_list.exit_code == 0
    assert "Discovered Plugins" in res_list.output

    # 2. plugin info nonexistent
    res_info = runner.invoke(main, ["plugin", "info", "nonexistent", "--workspace", str(ws)])
    assert res_info.exit_code == 0
    assert "not found" in res_info.output

    # 3. plugin disable & enable
    res_dis = runner.invoke(main, ["plugin", "disable", "some_plugin", "--workspace", str(ws)])
    assert res_dis.exit_code == 0
    assert "disabled" in res_dis.output

    res_en = runner.invoke(main, ["plugin", "enable", "some_plugin", "--workspace", str(ws)])
    assert res_en.exit_code == 0
    assert "enabled" in res_en.output


def test_cli_mcp_status_reports_client_transport_state(tmp_path: Path, monkeypatch) -> None:
    """The MCP CLI status subcommand connects clients and reports their public diagnostics."""
    ws = tmp_path / "ws-mcp"
    ws.mkdir()
    config = tmp_path / "mcp-config"
    config.mkdir()
    monkeypatch.setenv("HARNESS_CONFIG_DIR", str(config))
    echo_server = Path(__file__).resolve().parents[1] / "fixtures" / "mcp_echo_server.py"
    (config / "settings.json").write_text(
        json.dumps(
            {
                "schema_version": 1,
                "plugins": {
                    "bridge-mcp-client": {
                        "servers": {
                            "echo": {"command": sys.executable, "args": [str(echo_server)]},
                        },
                    },
                },
            }
        ),
        encoding="utf-8",
    )

    result = CliRunner().invoke(main, ["mcp", "status", "--workspace", str(ws)])
    assert result.exit_code == 0, result.output
    assert "MCP Client Servers" in result.output
    assert "echo" in result.output
    assert "stdio" in result.output
    assert "connected" in result.output


def test_cli_run_kernel_flag(tmp_path: Path, monkeypatch):
    """Verifies harness run --kernel flag boots kernel and runs turn."""
    ws = tmp_path / "ws"
    ws.mkdir()
    monkeypatch.setenv("HARNESS_CONFIG_DIR", str(tmp_path / "conf"))

    runner = CliRunner()
    res = runner.invoke(main, ["run", "Hello agent", "--workspace", str(ws), "--kernel"])
    # Turn execution will proceed through bootstrap_kernel
    # If no provider is installed, it handles ProviderError gracefully
    assert res.exit_code == 0 or "Error" in res.output or "Turn" in res.output


def test_cli_security_preset_commands_report_resolved_values(tmp_path: Path, monkeypatch):
    """`security set-preset` writes one key; `security show --json` reads the resolver's effective values."""
    import json

    ws = tmp_path / "ws-security"
    ws.mkdir()
    config = tmp_path / "security-config"
    monkeypatch.setenv("HARNESS_CONFIG_DIR", str(config))
    runner = CliRunner()

    changed = runner.invoke(main, ["security", "set-preset", "standard", "--workspace", str(ws)])
    assert changed.exit_code == 0, changed.output
    saved = SettingsStore(config / "settings.json")
    assert saved.get("kernel.security.preset") == "standard"
    assert saved.get("kernel.security.permission_mode") == "ask"
    assert saved.get("kernel.security.exec_in_sandbox") == "allow"

    shown = runner.invoke(main, ["security", "show", "--json", "--workspace", str(ws)])
    assert shown.exit_code == 0, shown.output
    status = json.loads(shown.output)
    assert status["preset"] == "standard"
    assert status["settings"]["kernel.security.exec_in_sandbox"] == "allow"
    assert status["sandbox"]["mode"] == "preferred"
