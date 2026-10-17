"""
Tests for Checkpoints Hook and Tool Plugin (§PLG-22).
Verifies HookKit and ToolKit conformance, shadow git repo snapshotting and restore,
and plugin registration.
"""
from __future__ import annotations

from pathlib import Path
from unittest.mock import MagicMock

import pytest

from harness.kernel.bus import EventBus
from harness.kernel.registry import PluginRegistry
from harness.kernel.secrets import Secrets
from harness.kernel.settings import SettingsStore
from harness.plugins.hooks.checkpoints.hook import CheckpointHook
from harness.plugins.hooks.checkpoints.shadow_repo import ShadowRepo
from harness.plugins.hooks.checkpoints.tools import (
    CHECKPOINT_RESTORE_SPEC,
    handle_checkpoint_restore,
)
from harness.sdk.testing.kits import HookKit, ToolKit
from harness.sdk.types import RiskClass, ToolCall, ToolSpec


def test_checkpoints_toolkit_conformance():
    """CHECKPOINT_RESTORE_SPEC adheres to ToolKit invariants."""
    kit = ToolKit()
    kit.test_input_schema_valid_and_closed(CHECKPOINT_RESTORE_SPEC)
    kit.test_declared_risk_class_bound(CHECKPOINT_RESTORE_SPEC)


@pytest.mark.asyncio
async def test_checkpoints_hook_kit_conformance(tmp_path: Path):
    """CheckpointHook adheres to HookKit invariants."""
    hook = CheckpointHook()
    kit = HookKit()
    ctx = MagicMock()
    ctx.workspace = tmp_path
    await kit.test_hook_outcome_action(hook, ctx)


@pytest.mark.asyncio
async def test_shadow_repo_snapshot_and_restore(tmp_path: Path):
    """ShadowRepo captures snapshots and restores workspace files accurately."""
    ws = tmp_path / "ws"
    ws.mkdir()
    f1 = ws / "code.py"
    f1.write_text("print('version 1')\n", encoding="utf-8")

    repo = ShadowRepo(ws)

    # 1. First snapshot
    sha1, diff1 = await repo.snapshot("Initial snapshot")
    assert len(sha1) == 40

    # 2. Mutate workspace
    f1.write_text("print('version 2')\n", encoding="utf-8")
    f2 = ws / "untracked.py"
    f2.write_text("print('untracked')\n", encoding="utf-8")

    # 3. Second snapshot
    sha2, diff2 = await repo.snapshot("Modified code")
    assert sha2 != sha1
    assert diff2 is not None
    assert "version 2" in diff2

    # 4. Restore to first snapshot
    await repo.restore(sha1)
    assert f1.read_text(encoding="utf-8") == "print('version 1')\n"
    assert not f2.exists()


@pytest.mark.asyncio
async def test_checkpoint_hook_skips_read_only(tmp_path: Path):
    """CheckpointHook snapshots on mutating tools and skips on READ tools."""
    ws = tmp_path / "ws"
    ws.mkdir()
    (ws / "file.txt").write_text("data\n", encoding="utf-8")

    hook = CheckpointHook()
    ctx = MagicMock()
    ctx.workspace = ws

    read_spec = ToolSpec(
        name="view_file",
        description="read file",
        input_schema={"type": "object", "additionalProperties": False},
        risk_class=RiskClass.READ,
    )
    read_call = ToolCall(id="c1", name="view_file", arguments={})

    outcome_read = await hook.execute(read_call, read_spec, ctx)
    assert outcome_read.action == "continue"

    write_spec = ToolSpec(
        name="write_to_file",
        description="write file",
        input_schema={"type": "object", "additionalProperties": False},
        risk_class=RiskClass.WRITE,
    )
    write_call = ToolCall(id="c2", name="write_to_file", arguments={})

    outcome_write = await hook.execute(write_call, write_spec, ctx)
    assert outcome_write.action == "continue"

    # Verify shadow repo was created and has a commit
    repo = ShadowRepo(ws)
    checkpoints = await repo.list_checkpoints()
    assert len(checkpoints) >= 1
    assert "pre_write_to_file_c2" in checkpoints[0]["label"]


@pytest.mark.asyncio
async def test_checkpoint_restore_tool(tmp_path: Path):
    """checkpoint_restore tool successfully restores previous commit."""
    ws = tmp_path / "ws"
    ws.mkdir()
    test_file = ws / "target.txt"
    test_file.write_text("initial state\n", encoding="utf-8")

    repo = ShadowRepo(ws)
    sha1, _ = await repo.snapshot("step 1")

    test_file.write_text("corrupted state\n", encoding="utf-8")

    ctx = MagicMock()
    ctx.workspace = ws

    result = await handle_checkpoint_restore({"checkpoint_id": sha1}, ctx)
    assert not result.is_error
    assert test_file.read_text(encoding="utf-8") == "initial state\n"
    assert len(result.cards) == 1
    assert result.cards[0].kind == "notice"


@pytest.mark.asyncio
async def test_checkpoints_plugin_registration(tmp_path: Path):
    """hook.checkpoints registers both CheckpointHook and checkpoint_restore tool."""
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

    assert any(h[0].name == "checkpoints" for h in registry.hooks)
    assert "checkpoint_restore" in registry.tools
    spec, _, _ = registry.tools["checkpoint_restore"]
    assert spec.risk_class == RiskClass.DESTRUCTIVE
    assert {
        "plugins.hook-checkpoints.changes",
        "plugins.hook-checkpoints.diff",
        "plugins.hook-checkpoints.restore",
    } <= set(registry.commands)
    assert "present" in registry.tools
    assert registry.tools["present"][0].risk_class == RiskClass.READ
