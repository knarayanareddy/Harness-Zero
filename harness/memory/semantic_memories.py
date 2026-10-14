"""
Persistent Cross-Session Memories Store (Hermes Agent Pattern).
Dissected from ~/.hermes/memories/.
Stores project rules, user preferences, and architectural invariants across sessions.
"""
from __future__ import annotations

import json
from dataclasses import dataclass, field
from datetime import UTC, datetime
from pathlib import Path


@dataclass
class MemoryItem:
    key: str
    category: str  # convention, preference, invariant, tool_note
    content: str
    created_at: str = field(
        default_factory=lambda: datetime.now(UTC).isoformat()
    )


class SemanticMemoryStore:
    def __init__(self, memory_dir: Path | None = None):
        self.memory_dir = (
            Path(memory_dir)
            if memory_dir
            else Path.home() / ".harness" / "memories"
        )
        self.memory_dir.mkdir(parents=True, exist_ok=True)
        self.index_file = self.memory_dir / "memory_index.json"
        self._memories: dict[str, MemoryItem] = {}
        self._load()

    def _load(self) -> None:
        self._memories.clear()
        if self.index_file.exists():
            try:
                data = json.loads(self.index_file.read_text(encoding="utf-8"))
                for k, v in data.items():
                    self._memories[k] = MemoryItem(
                        key=v.get("key", k),
                        category=v.get("category", "general"),
                        content=v.get("content", ""),
                        created_at=v.get("created_at", ""),
                    )
            except Exception:
                pass

    def _save(self) -> None:
        data = {
            k: {
                "key": m.key,
                "category": m.category,
                "content": m.content,
                "created_at": m.created_at,
            }
            for k, m in self._memories.items()
        }
        self.index_file.write_text(json.dumps(data, indent=2), encoding="utf-8")

    def remember(self, key: str, content: str, category: str = "convention") -> MemoryItem:
        item = MemoryItem(key=key, category=category, content=content)
        self._memories[key] = item
        self._save()
        return item

    def recall(self, key: str) -> str | None:
        item = self._memories.get(key)
        return item.content if item else None

    def list_memories(self, category: str | None = None) -> list[MemoryItem]:
        if category:
            return [m for m in self._memories.values() if m.category == category]
        return list(self._memories.values())

    def render_memory_context(self) -> str:
        """Renders persistent memories for system prompt injection."""
        if not self._memories:
            return ""
        lines = ["## Persistent Project Memories & Rules:"]
        for m in sorted(self._memories.values(), key=lambda x: x.key):
            lines.append(f"- [{m.category.upper()}] **{m.key}**: {m.content}")
        return "\n".join(lines)
