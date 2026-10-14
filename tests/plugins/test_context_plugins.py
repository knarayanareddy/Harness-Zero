"""
Conformance and unit tests for context plugins (PLG-07).
Absorbs LOOP-07 (part) and CONF-04.
"""
from __future__ import annotations

from pathlib import Path

import pytest

from harness.kernel.bus import EventBus
from harness.kernel.context import ContextAssembler
from harness.kernel.registry import PluginRegistry
from harness.kernel.secrets import Secrets
from harness.kernel.settings import SettingsStore
from harness.plugins.context.identity.source import IdentityContextSource
from harness.plugins.context.instructions_files.source import InstructionsFilesContextSource
from harness.plugins.context.tool_guidance.source import ToolGuidanceContextSource
from harness.sdk.testing.kits import ContextKit
from harness.sdk.types import Stability


class DummyTurnContext:
    def __init__(self, workspace: Path):
        self.workspace = workspace


@pytest.mark.asyncio
async def test_contextkit_conformance(tmp_path: Path):
    kit = ContextKit()
    ctx = DummyTurnContext(tmp_path)

    id_src = IdentityContextSource()
    tg_src = ToolGuidanceContextSource()
    inst_src = InstructionsFilesContextSource()

    # Create dummy instruction files
    (tmp_path / "AGENTS.md").write_text("Agents instructions here", encoding="utf-8")
    (tmp_path / "CLAUDE.md").write_text("Claude instructions here", encoding="utf-8")

    for src in (id_src, tg_src, inst_src):
        await kit.test_static_blocks_byte_identical(src, ctx)
        await kit.test_block_ids_unique(src, ctx)


@pytest.mark.asyncio
async def test_identity_context_source(tmp_path: Path):
    src = IdentityContextSource()
    blocks = await src.blocks(DummyTurnContext(tmp_path))
    assert len(blocks) == 1
    b = blocks[0]
    assert b.id == "identity"
    assert b.stability == Stability.STATIC
    assert "Harness-Zero" in b.text
    assert "test-driven" in b.text


@pytest.mark.asyncio
async def test_tool_guidance_context_source(tmp_path: Path):
    src = ToolGuidanceContextSource()
    blocks = await src.blocks(DummyTurnContext(tmp_path))
    assert len(blocks) == 1
    b = blocks[0]
    assert b.id == "tool_guidance"
    assert b.stability == Stability.STATIC
    assert "view_file" in b.text
    assert "edit_file" in b.text


@pytest.mark.asyncio
async def test_instructions_files_context_source(tmp_path: Path):
    src = InstructionsFilesContextSource()

    # 1. No files in workspace
    empty_blocks = await src.blocks(DummyTurnContext(tmp_path))
    assert len(empty_blocks) == 0

    # 2. Add AGENTS.md and .harness/INSTRUCTIONS.md
    (tmp_path / "AGENTS.md").write_text("Strict adherence to TDD", encoding="utf-8")
    harness_dir = tmp_path / ".harness"
    harness_dir.mkdir()
    (harness_dir / "INSTRUCTIONS.md").write_text("Never commit directly to main", encoding="utf-8")

    blocks = await src.blocks(DummyTurnContext(tmp_path))
    assert len(blocks) == 2
    ids = [b.id for b in blocks]
    assert "instructions_AGENTS_md" in ids
    assert "instructions__harness_INSTRUCTIONS_md" in ids
    assert all(b.stability == Stability.STATIC for b in blocks)


@pytest.mark.asyncio
async def test_context_assembler_integration(tmp_path: Path):
    # Setup workspace instruction file
    (tmp_path / "AGENTS.md").write_text("Integrate with assembler", encoding="utf-8")

    bus = EventBus()
    settings = SettingsStore(path=tmp_path / "settings.json")
    secrets = Secrets(path=tmp_path / "secrets.json")
    registry = PluginRegistry(
        workspace=tmp_path,
        settings=settings,
        secrets=secrets,
        bus=bus,
    )
    plugin_dir = Path(__file__).resolve().parents[2] / "harness" / "plugins"
    registry.discover_all(builtin_dir=plugin_dir)
    await registry.load_all()

    assembler = ContextAssembler(registry=registry, settings=settings)

    history = [
        {"role": "user", "content": "Hello agent", "type": "USER_INPUT"},
        {"role": "assistant", "content": "Hello user", "type": "PLANNER_RESPONSE"},
    ]

    req = await assembler.assemble(
        session_id="test_sess",
        user_prompt="Run the build",
        history_records=history,
    )

    # Verify system blocks contain identity, tool_guidance, and instructions
    system_text = " ".join(b.text for b in req.system)
    assert "Harness-Zero" in system_text
    assert "Tool Execution Rules" in system_text
    assert "Integrate with assembler" in system_text

    # Verify history messages and latest prompt are present
    assert len(req.messages) >= 3
    assert req.messages[-1].content[0].text == "Run the build"
