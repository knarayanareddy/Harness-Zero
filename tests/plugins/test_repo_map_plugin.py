"""
Tests for Repo Map Context Plugin (§PLG-20).
Absorbs LOOP-07 (repo map) and D13.
Verifies ContextKit conformance, AST & generic symbol parsing,
token budgeting, file threshold guard, and fingerprint caching.
"""
from __future__ import annotations

from pathlib import Path
from unittest.mock import MagicMock

import pytest

from harness.kernel.bus import EventBus
from harness.kernel.registry import PluginRegistry
from harness.kernel.secrets import Secrets
from harness.kernel.settings import SettingsStore
from harness.plugins.context.repo_map.map import RepoMapGenerator, calculate_workspace_fingerprint
from harness.plugins.context.repo_map.source import RepoMapContextSource
from harness.sdk.testing.kits import ContextKit
from harness.sdk.types import Stability


@pytest.fixture
def sample_workspace(tmp_path: Path) -> Path:
    ws = tmp_path / "workspace"
    ws.mkdir()

    # Python source
    py_file = ws / "calculator.py"
    py_file.write_text(
        "class Calculator:\n"
        "    def add(self, a, b):\n"
        "        return a + b\n\n"
        "def helper(x):\n"
        "    return x * 2\n",
        encoding="utf-8",
    )

    # TypeScript source
    ts_file = ws / "service.ts"
    ts_file.write_text(
        "export interface UserProfile {\n"
        "  name: string;\n"
        "}\n\n"
        "export async function fetchUser(id: string): Promise<UserProfile> {\n"
        "  return { name: id };\n"
        "}\n",
        encoding="utf-8",
    )

    return ws


@pytest.mark.asyncio
async def test_repo_map_context_kit_conformance(sample_workspace: Path):
    """RepoMapContextSource conforms to ContextKit."""
    source = RepoMapContextSource()
    kit = ContextKit()

    ctx = MagicMock()
    ctx.workspace = sample_workspace
    ctx.settings = MagicMock()
    ctx.settings.get.return_value = None

    await kit.test_static_blocks_byte_identical(source, ctx)
    await kit.test_block_ids_unique(source, ctx)


@pytest.mark.asyncio
async def test_repo_map_symbols_extracted(sample_workspace: Path):
    """RepoMap extracts classes, functions, and interfaces into markdown."""
    source = RepoMapContextSource()
    ctx = MagicMock()
    ctx.workspace = sample_workspace
    ctx.settings = MagicMock()
    ctx.settings.get.return_value = None

    blocks = await source.blocks(ctx)
    assert len(blocks) == 1
    block = blocks[0]
    assert block.id == "repo_map"
    assert block.stability == Stability.SESSION

    text = block.text
    assert "calculator.py:" in text
    assert "class Calculator" in text
    assert "def add(a, b)" in text
    assert "def helper(x)" in text
    assert "service.ts:" in text
    assert "interface UserProfile" in text
    assert "function fetchUser" in text


@pytest.mark.asyncio
async def test_repo_map_token_budget_truncation(sample_workspace: Path):
    """RepoMap limits output according to max_tokens budget."""
    gen = RepoMapGenerator(max_tokens=10)  # Very tight token budget (~40 chars)
    map_text = gen.generate(sample_workspace)
    assert "truncated" in map_text or "omitted" in map_text


@pytest.mark.asyncio
async def test_repo_map_file_threshold_guard(tmp_path: Path):
    """RepoMap skips when codebase file count exceeds threshold without force."""
    ws = tmp_path / "huge_workspace"
    ws.mkdir()

    # Create dummy files
    for i in range(15):
        (ws / f"file_{i}.py").write_text(f"def f_{i}(): pass\n", encoding="utf-8")

    # Set threshold lower than file count
    gen = RepoMapGenerator(max_files_threshold=10)
    skipped_text = gen.generate(ws, force=False)
    assert "Repo map skipped" in skipped_text
    assert "exceeding threshold" in skipped_text

    # With force=True, it generates anyway
    forced_text = gen.generate(ws, force=True)
    assert "file_0.py:" in forced_text


@pytest.mark.asyncio
async def test_repo_map_fingerprint_cache(sample_workspace: Path):
    """Fingerprint remains identical when workspace files do not change."""
    fp1 = calculate_workspace_fingerprint(sample_workspace)
    fp2 = calculate_workspace_fingerprint(sample_workspace)
    assert fp1 == fp2

    # Mutate a file -> fingerprint changes
    (sample_workspace / "new.py").write_text("def new(): pass\n", encoding="utf-8")
    fp3 = calculate_workspace_fingerprint(sample_workspace)
    assert fp1 != fp3


@pytest.mark.asyncio
async def test_repo_map_plugin_registration(tmp_path: Path):
    """context.repo_map is discovered and registered in PluginRegistry."""
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

    assert any(s[0].name == "repo_map" for s in registry.context_sources)
