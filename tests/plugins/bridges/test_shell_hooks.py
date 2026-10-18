"""
Unit, integration, and conformance tests for Claude Code-compatible Shell Hooks Bridge (§10, BRG-01, S13).
"""
from __future__ import annotations

import sys
from pathlib import Path

import pytest

from harness.kernel.bus import EventBus
from harness.plugins.bridges.shell_hooks import (
    ShellHook,
    ShellHooksDispatcherHook,
    matches_tool,
    parse_claude_hooks_config,
)
from harness.sdk.contracts import ToolCall
from harness.sdk.testing import HookKit
from harness.sdk.types import HookEvent


class MockHookContext:
    def __init__(self, workspace: Path, session_id: str = "sess_test", bus: EventBus | None = None, settings: dict | None = None) -> None:
        self.workspace = workspace
        self.session_id = session_id
        self.bus = bus
        self.settings = settings or {}


@pytest.mark.asyncio
async def test_hook_kit_conformance(tmp_path: Path):
    """Verifies ShellHook and ShellHooksDispatcherHook against HookKit."""
    kit = HookKit()
    ctx = MockHookContext(workspace=tmp_path)

    hook = ShellHook(
        name="echo_continue",
        event=HookEvent.PRE_TOOL_USE,
        command=f"{sys.executable} -c 'import sys; sys.exit(0)'",
    )
    await kit.test_hook_outcome_action(hook, ctx)

    dispatcher = ShellHooksDispatcherHook()
    await kit.test_hook_outcome_action(dispatcher, ctx)


def test_d16_matcher_aliasing():
    """Verifies D16 tool aliases (Bash, Read, Write, Edit, Grep) match Harness tool names."""
    # Bash matches run_command
    assert matches_tool("Bash", "run_command")
    assert matches_tool("bash", "run_command")
    assert matches_tool("shell_run|Bash", "run_command")
    assert not matches_tool("Bash", "write_to_file")

    # Write matches write_to_file
    assert matches_tool("Write", "write_to_file")
    assert matches_tool("write", "write_to_file")

    # Edit matches edit_file
    assert matches_tool("Edit", "edit_file")

    # Grep matches search_codebase
    assert matches_tool("Grep", "search_codebase")

    # Direct Harness names match directly
    assert matches_tool("run_command", "run_command")
    assert matches_tool(".*", "anything")
    assert matches_tool(None, "anything")


def test_parse_claude_hooks_config_nested_and_flat():
    """Verifies parsing of Claude Code JSON configs."""
    nested_config = {
        "hooks": {
            "PreToolUse": [
                {
                    "matcher": "Bash|Write",
                    "hooks": [
                        {"command": "python check_rm.py", "timeout": 15}
                    ]
                }
            ],
            "PostToolUse": [
                {
                    "matcher": ".*",
                    "command": "python log_tool.py"
                }
            ]
        }
    }
    rules = parse_claude_hooks_config(nested_config)
    assert len(rules) == 2
    assert rules[0].event == HookEvent.PRE_TOOL_USE
    assert rules[0].matcher == "Bash|Write"
    assert rules[0].timeout_s == 15.0
    assert rules[1].event == HookEvent.POST_TOOL_USE


@pytest.mark.asyncio
async def test_shell_hook_block_exit_2(tmp_path: Path):
    """Exit 2 signals explicit BLOCK, with reason read from stderr."""
    script_path = tmp_path / "block_script.py"
    script_path.write_text(
        "import sys, json\n"
        "payload = json.loads(sys.stdin.read())\n"
        "sys.stderr.write('Forbidden operation: deletion is not allowed')\n"
        "sys.exit(2)\n",
        encoding="utf-8",
    )

    hook = ShellHook(
        name="test_block",
        event=HookEvent.PRE_TOOL_USE,
        command=f"{sys.executable} {script_path}",
    )

    ctx = MockHookContext(workspace=tmp_path)
    call = ToolCall(id="call_1", name="run_command", arguments={"command": "rm -rf /tmp/data"})
    outcome = await hook.on(HookEvent.PRE_TOOL_USE, {"call": call}, ctx)

    assert outcome.action == "block"
    assert "Forbidden operation: deletion is not allowed" in outcome.reason


@pytest.mark.asyncio
async def test_shell_hook_mutate_json_stdout(tmp_path: Path):
    """Exit 0 with updatedInput JSON mutates tool arguments."""
    script_path = tmp_path / "mutate_script.py"
    script_path.write_text(
        "import sys, json\n"
        "payload = json.loads(sys.stdin.read())\n"
        "print(json.dumps({'decision': 'approve', 'updatedInput': {'command': 'echo safe_command'}}))\n"
        "sys.exit(0)\n",
        encoding="utf-8",
    )

    hook = ShellHook(
        name="test_mutate",
        event=HookEvent.PRE_TOOL_USE,
        command=f"{sys.executable} {script_path}",
    )

    ctx = MockHookContext(workspace=tmp_path)
    call = ToolCall(id="call_2", name="run_command", arguments={"command": "unsafe_command"})
    outcome = await hook.on(HookEvent.PRE_TOOL_USE, {"call": call}, ctx)

    assert outcome.action == "mutate"
    assert outcome.arguments == {"command": "echo safe_command"}


@pytest.mark.asyncio
async def test_shell_hook_context_injection(tmp_path: Path):
    """Exit 0 with additionalContext JSON injects context."""
    script_path = tmp_path / "context_script.py"
    script_path.write_text(
        "import sys, json\n"
        "print(json.dumps({'additionalContext': 'Rule 42: always run tests'}))\n"
        "sys.exit(0)\n",
        encoding="utf-8",
    )

    hook = ShellHook(
        name="test_ctx",
        event=HookEvent.USER_PROMPT_SUBMIT,
        command=f"{sys.executable} {script_path}",
    )

    ctx = MockHookContext(workspace=tmp_path)
    outcome = await hook.on(HookEvent.USER_PROMPT_SUBMIT, {"prompt": "Write a feature"}, ctx)

    assert outcome.action == "continue"
    assert outcome.additional_context == "Rule 42: always run tests"


@pytest.mark.asyncio
async def test_shell_hook_timeout(tmp_path: Path):
    """Hook exceeding timeout_s is killed and fails open with notice."""
    hook = ShellHook(
        name="test_timeout",
        event=HookEvent.PRE_TOOL_USE,
        command=f"{sys.executable} -c 'import time; time.sleep(5)'",
        timeout_s=0.2,
    )

    ctx = MockHookContext(workspace=tmp_path)
    outcome = await hook.on(HookEvent.PRE_TOOL_USE, {}, ctx)

    assert outcome.action == "continue"
    assert "timed out" in outcome.reason


@pytest.mark.asyncio
async def test_s13_claude_code_hook_acceptance_and_events(tmp_path: Path):
    """
    S13 Scenario: Put an existing PreToolUse hook (exit 2 on rm) under plugins.shell-hooks.hooks.
    Verify tool blocked, notice card emitted, and hook.fired event published.
    """
    rm_blocker_script = tmp_path / "rm_blocker.py"
    rm_blocker_script.write_text(
        "import sys, json\n"
        "data = json.loads(sys.stdin.read())\n"
        "cmd = data.get('tool_input', {}).get('command', '')\n"
        "if 'rm ' in cmd or cmd.startswith('rm'):\n"
        "    sys.stderr.write('Security Violation: rm command is blocked by Claude Code security hook.')\n"
        "    sys.exit(2)\n"
        "sys.exit(0)\n",
        encoding="utf-8",
    )

    # Configure hook under settings (as specified in S13)
    settings = {
        "plugins.shell-hooks.hooks": {
            "hooks": {
                "PreToolUse": [
                    {
                        "matcher": "Bash",  # Tests D16 alias: Bash matches run_command
                        "hooks": [
                            {
                                "command": f"{sys.executable} {rm_blocker_script}",
                                "timeout": 5,
                            }
                        ]
                    }
                ]
            }
        }
    }

    bus = EventBus()
    sub = bus.subscribe()

    ctx = MockHookContext(workspace=tmp_path, bus=bus, settings=settings)

    # 1. Non-rm command -> succeeds
    dispatcher = ShellHooksDispatcherHook()
    safe_call = ToolCall(id="c_safe", name="run_command", arguments={"command": "ls -la"})
    safe_outcome = await dispatcher.on(HookEvent.PRE_TOOL_USE, {"call": safe_call}, ctx)
    assert safe_outcome.action == "continue"

    # Drain events
    events_1 = []
    while not sub.empty():
        events_1.append(sub.get_nowait())

    # Verify hook.fired event published
    fired_events = [e for e in events_1 if e.type == "hook.fired"]
    assert len(fired_events) >= 1
    assert fired_events[-1].data["event"] == "pre_tool_use"

    # 2. rm command -> blocked (S13)
    rm_call = ToolCall(id="c_rm", name="run_command", arguments={"command": "rm -rf /dangerous/path"})
    blocked_outcome = await dispatcher.on(HookEvent.PRE_TOOL_USE, {"call": rm_call}, ctx)
    assert blocked_outcome.action == "block"
    assert "Security Violation: rm command is blocked" in blocked_outcome.reason

    # Drain events
    events_2 = []
    while not sub.empty():
        events_2.append(sub.get_nowait())

    # Verify notice card event emitted (S13 expected outcome)
    # ui.card payload is {card: Card.to_dict()} — the SDK card shape {kind, id, title, data}.
    notice_cards = [e for e in events_2 if e.type == "ui.card" and e.data["card"]["kind"] == "notice"]
    assert len(notice_cards) >= 1
    assert notice_cards[-1].data["card"]["data"]["level"] == "error"
    assert "Security Violation" in notice_cards[-1].data["card"]["data"]["body"]
