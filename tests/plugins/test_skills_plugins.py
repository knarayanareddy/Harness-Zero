"""
Tests for Skills Plugins (PLG-15):
Skills catalog context source, view_skill tool handler, and ContextKit conformance.
"""
from __future__ import annotations

from pathlib import Path
from unittest.mock import MagicMock

import pytest

from harness.kernel.bus import EventBus
from harness.kernel.registry import PluginRegistry
from harness.kernel.secrets import Secrets
from harness.kernel.settings import SettingsStore
from harness.plugins.context.skills_catalog.source import SkillsCatalogContextSource
from harness.plugins.tools.skills.tools import ViewSkillHandler
from harness.sdk.context import ToolContext
from harness.sdk.testing import ContextKit


@pytest.mark.asyncio
async def test_skills_catalog_context_source_and_kit(tmp_path: Path):
    """Verifies SkillsCatalogContextSource discovers builtin & workspace skills and passes ContextKit."""
    # Create a workspace custom skill
    ws_skill_dir = tmp_path / ".harness" / "skills" / "custom-deployer"
    ws_skill_dir.mkdir(parents=True, exist_ok=True)
    (ws_skill_dir / "SKILL.md").write_text(
        "---\nname: custom-deployer\ndescription: Custom production deployment playbook.\n---\n# Deployer Steps",
        encoding="utf-8",
    )

    ctx_mock = MagicMock()
    ctx_mock.workspace = tmp_path

    source = SkillsCatalogContextSource()
    blocks = await source.blocks(ctx_mock)

    assert len(blocks) == 1
    cat_block = blocks[0]
    assert cat_block.id == "skills_catalog"
    assert "code-architect" in cat_block.text
    assert "custom-deployer" in cat_block.text
    assert "view_skill" in cat_block.text

    # Conformance test with ContextKit
    kit = ContextKit()
    await kit.test_static_blocks_byte_identical(source, ctx_mock)
    await kit.test_block_ids_unique(source, ctx_mock)


@pytest.mark.asyncio
async def test_view_skill_tool_execution(tmp_path: Path):
    """Verifies view_skill tool loads full SKILL.md contents."""
    # Create a custom skill in workspace
    ws_skill_dir = tmp_path / ".harness" / "skills" / "k8s-debugger"
    ws_skill_dir.mkdir(parents=True, exist_ok=True)
    skill_content = "# Kubernetes Debugger Playbook\n\n1. Run kubectl get pods\n2. Inspect pod logs"
    (ws_skill_dir / "SKILL.md").write_text(skill_content, encoding="utf-8")

    handler = ViewSkillHandler()
    ctx_mock = MagicMock(spec=ToolContext)
    ctx_mock.workspace = tmp_path

    # 1. View custom skill
    res = await handler({"skill_name": "k8s-debugger"}, ctx_mock)
    assert not res.is_error
    assert "Kubernetes Debugger Playbook" in res.output
    assert len(res.cards) == 1

    # 2. View builtin skill
    res_builtin = await handler({"skill_name": "code-architect"}, ctx_mock)
    assert not res_builtin.is_error
    assert "Code Architect Playbook" in res_builtin.output
    assert len(res_builtin.cards) == 1

    # 3. View non-existent skill
    res_missing = await handler({"skill_name": "non-existent-skill"}, ctx_mock)
    assert res_missing.is_error
    assert "not found" in res_missing.output


@pytest.mark.asyncio
async def test_skills_plugins_discovery_and_registration(tmp_path: Path):
    """Verifies context-skills-catalog and tool-skills plugins are discovered and loaded."""
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

    assert "context-skills-catalog" in registry.discovered
    assert "tool-skills" in registry.discovered

    await registry.load_all()

    # Verify tool registration
    assert "view_skill" in registry.tools

    # Verify context source registration
    assert any(cs.name == "skills_catalog" for cs, _ in registry.context_sources)
