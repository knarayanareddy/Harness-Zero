"""
Tests for Worktree Executor Plugin (§PLG-18).
Absorbs ORCH-03.
Verifies ExecutorKit conformance, async git worktree provisioning,
base commit tracking, diffing, and stale branch handling.
"""
from __future__ import annotations

import asyncio
from pathlib import Path

import pytest

from harness.kernel.bus import EventBus
from harness.kernel.registry import PluginRegistry
from harness.kernel.secrets import Secrets
from harness.kernel.settings import SettingsStore
from harness.plugins.executors.worktree.executor import WorktreeExecutor
from harness.plugins.executors.worktree.worktree import WorktreeManager
from harness.sdk.testing.kits import ExecutorKit


async def _run_cmd(cmd: list[str], cwd: Path) -> None:
    proc = await asyncio.create_subprocess_exec(
        *cmd,
        cwd=cwd,
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.PIPE,
    )
    await proc.communicate()
    assert proc.returncode == 0


@pytest.fixture
async def git_repo(tmp_path: Path) -> Path:
    repo = tmp_path / "repo"
    repo.mkdir()
    await _run_cmd(["git", "init"], repo)
    await _run_cmd(["git", "config", "user.name", "Test Runner"], repo)
    await _run_cmd(["git", "config", "user.email", "test@example.com"], repo)

    initial_file = repo / "README.md"
    initial_file.write_text("# Initial Repo\n", encoding="utf-8")
    await _run_cmd(["git", "add", "README.md"], repo)
    await _run_cmd(["git", "commit", "-m", "Initial commit"], repo)
    return repo


@pytest.mark.asyncio
async def test_worktree_executor_kit_conformance(tmp_path: Path):
    """WorktreeExecutor passes standard ExecutorKit conformance tests."""
    executor = WorktreeExecutor(repo_root=tmp_path)
    kit = ExecutorKit()
    await kit.test_echo_round_trip(executor, tmp_path)
    await kit.test_cwd_enforced(executor, tmp_path)
    await kit.test_kill_idempotency(executor, tmp_path)


@pytest.mark.asyncio
async def test_worktree_manager_lifecycle(git_repo: Path):
    """WorktreeManager creates worktree, records base commit, diffs, and cleans up."""
    mgr = WorktreeManager(repo_root=git_repo)
    head_commit = await mgr.get_head_commit()
    assert len(head_commit) == 40

    # 1. Create worktree
    wt_dir, base_commit = await mgr.create_worktree("agent_42")
    assert wt_dir.exists()
    assert base_commit == head_commit
    assert (wt_dir / "README.md").exists()

    # 2. Make changes in worktree
    new_file = wt_dir / "feature.py"
    new_file.write_text("print('hello from subagent')\n", encoding="utf-8")
    await _run_cmd(["git", "add", "feature.py"], wt_dir)
    await _run_cmd(["git", "commit", "-m", "Add feature.py"], wt_dir)

    # 3. Verify diff against recorded base commit
    diff = await mgr.get_diff("agent_42")
    assert "print('hello from subagent')" in diff
    assert "diff --git a/feature.py b/feature.py" in diff

    # 4. Remove worktree
    await mgr.remove_worktree("agent_42", delete_branch=True)
    assert not wt_dir.exists()


@pytest.mark.asyncio
async def test_worktree_manager_stale_branch_and_dir_recovery(git_repo: Path):
    """WorktreeManager recovers cleanly if a branch or directory is stale."""
    mgr = WorktreeManager(repo_root=git_repo)

    # First creation
    wt_dir_1, _ = await mgr.create_worktree("agent_stale")
    assert wt_dir_1.exists()

    # Re-create without explicit removal (simulates crash / retry)
    wt_dir_2, _ = await mgr.create_worktree("agent_stale")
    assert wt_dir_2.exists()

    # Cleanup
    await mgr.remove_worktree("agent_stale")
    assert not wt_dir_2.exists()


@pytest.mark.asyncio
async def test_worktree_executor_plugin_registration(tmp_path: Path):
    """WorktreeExecutorPlugin is discovered and registered in PluginRegistry."""
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

    assert "worktree" in registry.executors
    executor, origin = registry.executors["worktree"]
    assert executor.isolation == "worktree"
