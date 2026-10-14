"""
Tests for Architect/Editor Strategy Plugin (§PLG-19).
Absorbs ORCH-01.
Verifies StrategyKit conformance, two-phase planning and surgical editing,
verification test execution, and safe file-scoped staging and commit guards.
"""
from __future__ import annotations

import asyncio
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock

import pytest

from harness.kernel.bus import EventBus
from harness.kernel.registry import PluginRegistry
from harness.kernel.secrets import Secrets
from harness.kernel.settings import SettingsStore
from harness.plugins.strategies.architect_editor.strategy import ArchitectEditorStrategy
from harness.sdk.cards import Card
from harness.sdk.testing.kits import StrategyKit
from harness.sdk.types import (
    Message,
    ModelParams,
    ModelRequest,
    Role,
    TextBlock,
    ToolCall,
    ToolResult,
    ToolUseBlock,
)


@pytest.mark.asyncio
async def test_architect_editor_strategy_kit_conformance(tmp_path: Path):
    """ArchitectEditorStrategy conforms to StrategyKit requirements."""
    strategy = ArchitectEditorStrategy()
    kit = StrategyKit()

    turn_ctx = MagicMock()
    turn_ctx.cancel = asyncio.Event()
    turn_ctx.options = {}
    turn_ctx.prompt = "Fix the login bug"
    turn_ctx.settings = MagicMock()
    turn_ctx.settings.get.return_value = None

    kernel_services = MagicMock()
    kernel_services.compact_if_needed = AsyncMock(return_value=False)
    dummy_req = ModelRequest(
        model="mock",
        system=[],
        messages=[Message(role=Role.USER, content=[TextBlock(text="test")])],
        tools=[],
        params=ModelParams(),
    )
    kernel_services.assemble_context = AsyncMock(return_value=dummy_req)

    # First call: architect emits plan and tool call
    resp1 = Message(
        role=Role.ASSISTANT,
        content=[
            TextBlock(text="Plan: Edit user.py"),
            ToolUseBlock(
                id="call_1",
                name="edit_file",
                input={"file_path": "user.py", "diff": "---"},
            ),
        ],
    )
    # Second call: editor says done
    resp2 = Message(
        role=Role.ASSISTANT,
        content=[TextBlock(text="Completed edits.")],
    )
    kernel_services.call_model = AsyncMock(side_effect=[resp1, resp2])
    kernel_services.record = AsyncMock(return_value=1)

    diff_card = Card.diff(path="user.py", unified="---", added=1, removed=0)
    tool_res = ToolResult(
        content=[TextBlock(text="Applied diff")],
        cards=[diff_card],
        metadata={"changed_files": ["user.py"]},
    )
    kernel_services.execute_tools = AsyncMock(return_value=[tool_res])
    kernel_services.execute_tool = AsyncMock(return_value=tool_res)

    await kit.test_strategy_completes_turn(strategy, turn_ctx, kernel_services)


@pytest.mark.asyncio
async def test_architect_editor_changed_files_and_verification(tmp_path: Path):
    """ArchitectEditor tracks changed files and runs test verification."""
    strategy = ArchitectEditorStrategy()

    turn_ctx = MagicMock()
    turn_ctx.cancel = asyncio.Event()
    turn_ctx.options = {
        "files": ["auth.py"],
        "test_command": "pytest tests/test_auth.py",
        "auto_commit": False,
    }
    turn_ctx.prompt = "Update auth tokens"
    turn_ctx.settings = MagicMock()
    turn_ctx.settings.get.return_value = None

    kernel_services = MagicMock()
    kernel_services.compact_if_needed = AsyncMock(return_value=False)
    dummy_req = ModelRequest(
        model="mock",
        system=[],
        messages=[],
        tools=[],
        params=ModelParams(),
    )
    kernel_services.assemble_context = AsyncMock(return_value=dummy_req)

    resp1 = Message(
        role=Role.ASSISTANT,
        content=[
            TextBlock(text="I will update auth.py"),
            ToolUseBlock(id="c1", name="edit_file", input={"file_path": "auth.py", "diff": "---"}),
        ],
    )
    resp2 = Message(role=Role.ASSISTANT, content=[TextBlock(text="Done editing")])
    kernel_services.call_model = AsyncMock(side_effect=[resp1, resp2])
    kernel_services.record = AsyncMock()

    edit_res = ToolResult(
        content=[TextBlock(text="Edited auth.py")],
        metadata={"changed_files": ["auth.py"]},
    )
    test_res = ToolResult(content=[TextBlock(text="1 passed in 0.05s")])

    kernel_services.execute_tools = AsyncMock(return_value=[edit_res])
    kernel_services.execute_tool = AsyncMock(return_value=test_res)

    result = await strategy.run(turn_ctx, kernel_services)
    assert "auth.py" in result.changed_files
    assert "Verification test passed" in result.output


@pytest.mark.asyncio
async def test_architect_editor_refuses_commit_on_unclean_index():
    """Refuses to commit if pre-existing staged changes exist in git index."""
    strategy = ArchitectEditorStrategy()

    turn_ctx = MagicMock()
    turn_ctx.cancel = asyncio.Event()
    turn_ctx.options = {
        "auto_commit": True,
        "test_command": "pytest",
    }
    turn_ctx.prompt = "Fix bug"
    turn_ctx.settings = MagicMock()
    turn_ctx.settings.get.return_value = None

    kernel_services = MagicMock()
    kernel_services.compact_if_needed = AsyncMock(return_value=False)
    kernel_services.assemble_context = AsyncMock(
        return_value=ModelRequest(
            model="mock",
            system=[],
            messages=[],
            tools=[],
            params=ModelParams(),
        )
    )

    resp1 = Message(
        role=Role.ASSISTANT,
        content=[
            ToolUseBlock(id="c1", name="edit_file", input={"file_path": "fix.py", "diff": "---"}),
        ],
    )
    resp2 = Message(role=Role.ASSISTANT, content=[TextBlock(text="Fixed")])
    kernel_services.call_model = AsyncMock(side_effect=[resp1, resp2])
    kernel_services.record = AsyncMock()

    edit_res = ToolResult(content=[TextBlock(text="OK")], metadata={"changed_files": ["fix.py"]})
    test_res = ToolResult(content=[TextBlock(text="PASS")])
    # Unclean git index: M (staged change in unrelated file)
    status_unclean_res = ToolResult(content=[TextBlock(text="M  secret.env\n?? untracked.txt\n")])

    kernel_services.execute_tools = AsyncMock(return_value=[edit_res])

    async def _mock_exec_tool(call: ToolCall) -> ToolResult:
        if "pytest" in call.arguments.get("command", ""):
            return test_res
        if "git status" in call.arguments.get("command", ""):
            return status_unclean_res
        return ToolResult.text("OK")

    kernel_services.execute_tool = AsyncMock(side_effect=_mock_exec_tool)

    result = await strategy.run(turn_ctx, kernel_services)
    assert "Refused to commit: index not clean" in result.output


@pytest.mark.asyncio
async def test_architect_editor_plugin_registration(tmp_path: Path):
    """ArchitectEditorStrategy is discovered and registered in PluginRegistry."""
    bus = EventBus()
    settings = SettingsStore(tmp_path / "settings")
    secrets = Secrets(tmp_path / "secrets")

    registry = PluginRegistry(
        workspace=tmp_path / "ws",
        settings=settings,
        secrets=secrets,
        bus=bus,
    )

    builtin_dir = Path(__file__).resolve().parents[2] / "harness" / "plugins"
    registry.discover_all(builtin_dir=builtin_dir)
    await registry.load_all()

    assert "architect_editor" in registry.strategies
    strategy, origin = registry.strategies["architect_editor"]
    assert strategy.name == "architect_editor"
