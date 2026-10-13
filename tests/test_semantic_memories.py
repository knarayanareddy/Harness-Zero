import pytest
import tempfile
from pathlib import Path
from harness.memory.semantic_memories import SemanticMemoryStore, MemoryItem

def test_semantic_memory_store_cross_session():
    with tempfile.TemporaryDirectory() as tmpdir:
        store = SemanticMemoryStore(memory_dir=Path(tmpdir))

        # Store preferences and patterns
        store.remember(
            key="test_runner",
            content="User prefers pytest-asyncio and typing annotations",
            category="preference"
        )
        store.remember(
            key="api_auth",
            content="FastAPI endpoints must use dependency injection for auth",
            category="convention"
        )

        # Recall specific memory
        val = store.recall("test_runner")
        assert val == "User prefers pytest-asyncio and typing annotations"

        # List memories
        prefs = store.list_memories(category="preference")
        assert len(prefs) == 1
        assert prefs[0].key == "test_runner"

        # Format system prompt injection
        prompt_block = store.render_memory_context()
        assert "Persistent Project Memories & Rules" in prompt_block
        assert "FastAPI endpoints must use dependency injection" in prompt_block

        # Re-open in fresh instance to verify disk persistence
        store2 = SemanticMemoryStore(memory_dir=Path(tmpdir))
        assert store2.recall("test_runner") == "User prefers pytest-asyncio and typing annotations"
