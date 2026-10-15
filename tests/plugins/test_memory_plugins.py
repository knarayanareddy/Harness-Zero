"""
Tests for Memory Plugins (PLG-14):
FTS5 search index, search_memory & remember tools, and memories context source.
"""
from __future__ import annotations

import json
from pathlib import Path
from unittest.mock import MagicMock

import pytest

from harness.kernel.bus import EventBus
from harness.kernel.registry import PluginRegistry
from harness.kernel.secrets import Secrets
from harness.kernel.settings import SettingsStore
from harness.plugins.context.memories.source import MemoriesContextSource
from harness.plugins.stores.fts5_search.index import FTS5SearchIndex
from harness.plugins.tools.memory.tools import RememberHandler, SearchMemoryHandler
from harness.sdk.context import ToolContext
from harness.sdk.testing import ContextKit


@pytest.mark.asyncio
async def test_fts5_index_and_search(tmp_path: Path):
    """Verifies indexing turns and searching via SQLite FTS5."""
    db_path = tmp_path / "memory.db"
    index = FTS5SearchIndex(db_path)

    await index.index(
        session_id="sess_1",
        record={
            "step_index": 1,
            "source": "USER",
            "type": "USER_INPUT",
            "content": "How do I configure Postgres database connection pooling?",
        },
    )
    await index.index(
        session_id="sess_1",
        record={
            "step_index": 2,
            "source": "MODEL",
            "type": "PLANNER_RESPONSE",
            "content": "You can use asyncpg with create_pool for database pooling.",
        },
    )
    await index.index(
        session_id="sess_2",
        record={
            "step_index": 1,
            "source": "USER",
            "type": "USER_INPUT",
            "content": "Configure Redis cache client with redis-py.",
        },
    )

    # Search workspace-wide
    results = await index.search("database pooling")
    assert len(results) >= 1
    assert any("asyncpg" in r["snippet"] or "Postgres" in r["snippet"] for r in results)

    # Search session-scoped
    sess2_results = await index.search("Redis", scope="session", session_id="sess_2")
    assert len(sess2_results) == 1
    assert sess2_results[0]["conversation_id"] == "sess_2"

    sess1_results = await index.search("Redis", scope="session", session_id="sess_1")
    assert len(sess1_results) == 0

    index.close()


@pytest.mark.asyncio
async def test_fts5_remember_and_context_source(tmp_path: Path):
    """Verifies storing persistent memories and injecting them via MemoriesContextSource."""
    db_path = tmp_path / ".harness" / "memory.db"
    index = FTS5SearchIndex(db_path)

    id1 = await index.remember(text="Use Python 3.12 syntax and uv package manager", key="style")
    assert id1 >= 1
    id2 = await index.remember(text="All database queries must use parameterized statements", key="security")
    assert id2 >= 1
    index.close()

    # ContextSource verification
    ctx_mock = MagicMock()
    ctx_mock.workspace = tmp_path

    source = MemoriesContextSource()
    blocks = await source.blocks(ctx_mock)

    assert len(blocks) == 1
    mem_block = blocks[0]
    assert mem_block.id == "memories"
    assert "style" in mem_block.text
    assert "uv package manager" in mem_block.text
    assert "security" in mem_block.text

    # Conformance with ContextKit
    kit = ContextKit()
    await kit.test_block_ids_unique(source, ctx_mock)


@pytest.mark.asyncio
async def test_memory_tools_execution(tmp_path: Path):
    """Verifies search_memory and remember tools."""
    data_dir = tmp_path / ".harness"
    data_dir.mkdir(parents=True, exist_ok=True)
    db_path = data_dir / "memory.db"

    # 1. Test remember tool
    remember_handler = RememberHandler()
    ctx_mock = MagicMock(spec=ToolContext)
    ctx_mock.workspace = tmp_path  # tools resolve <workspace>/.harness/memory.db

    rem_res = await remember_handler(
        {"text": "Always deploy to production using staging canary", "key": "deployment"},
        ctx_mock,
    )
    assert not rem_res.is_error
    assert "Permanently remembered fact" in rem_res.output
    assert len(rem_res.cards) == 1

    # Index a turn for searching
    index = FTS5SearchIndex(db_path)
    await index.index(
        session_id="s_test",
        record={
            "step_index": 5,
            "source": "MODEL",
            "type": "PLANNER_RESPONSE",
            "content": "Applied Terraform manifest for Kubernetes ingress.",
        },
    )
    index.close()

    # 2. Test search_memory tool
    search_handler = SearchMemoryHandler()
    search_res = await search_handler({"query": "Kubernetes ingress"}, ctx_mock)
    assert not search_res.is_error
    assert "Found 1 matches" in search_res.output
    assert len(search_res.cards) == 1


@pytest.mark.asyncio
async def test_reindex_from_transcripts(tmp_path: Path):
    """Verifies automatic reindexing from past session transcript files."""
    data_dir = tmp_path / ".harness"
    sessions_dir = data_dir / "sessions"
    sess_dir = sessions_dir / "hist_sess_01"
    sess_dir.mkdir(parents=True, exist_ok=True)

    transcript_file = sess_dir / "transcript.jsonl"
    lines = [
        json.dumps({"step_index": 1, "source": "USER", "type": "USER_INPUT", "content": "Set up Prometheus metrics"}),
        json.dumps({"step_index": 2, "source": "MODEL", "type": "PLANNER_RESPONSE", "content": "Added /metrics endpoint using prometheus_client"}),
    ]
    transcript_file.write_text("\n".join(lines), encoding="utf-8")

    db_path = data_dir / "memory.db"
    index = FTS5SearchIndex(db_path)

    reindexed = index.maybe_reindex_from_sessions(sessions_dir)
    assert reindexed == 2

    results = await index.search("Prometheus")
    assert len(results) >= 1
    index.close()


@pytest.mark.asyncio
async def test_memory_plugins_discovery_and_registration(tmp_path: Path):
    """Verifies store-fts5-search, tool-memory, and context-memories are discovered and registered."""
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

    assert "store-fts5-search" in registry.discovered
    assert "tool-memory" in registry.discovered
    assert "context-memories" in registry.discovered

    await registry.load_all()

    # Verify store registration
    assert "search" in registry.stores
    store_inst, _ = registry.stores["search"]
    assert isinstance(store_inst, FTS5SearchIndex)

    # Verify tools registration
    tool_names = list(registry.tools.keys())
    assert "search_memory" in tool_names
    assert "remember" in tool_names

    # Verify context source registration
    assert any(cs.name == "memories" for cs, _ in registry.context_sources)
