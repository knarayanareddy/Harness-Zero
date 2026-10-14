"""
Memories Context Source implementation (PLG-14).
"""
from __future__ import annotations

import logging
import sqlite3
from typing import Any

from harness.sdk.types import ContextBlock, Stability

logger = logging.getLogger(__name__)


class MemoriesContextSource:
    """
    ContextSource reading persistent remembered facts and injecting them as a SESSION context block.
    """

    name: str = "memories"

    async def blocks(self, ctx: Any) -> list[ContextBlock]:
        data_dir = getattr(ctx, "data_dir", None)
        if not data_dir:
            workspace = getattr(ctx, "workspace", None)
            if workspace:
                data_dir = workspace / ".harness"

        if not data_dir:
            return []

        db_path = data_dir / "memory.db"
        if not db_path.exists():
            return []

        try:
            conn = sqlite3.connect(str(db_path))
            cursor = conn.cursor()
            cursor.execute(
                """
                SELECT id, key, text
                FROM remembered_facts
                ORDER BY id DESC
                LIMIT 25
                """
            )
            rows = cursor.fetchall()
            conn.close()
        except Exception as e:
            logger.debug("Failed reading memories: %s", e)
            return []

        if not rows:
            return []

        lines = ["# Remembered Facts & Context", ""]
        for _, key, text in reversed(rows):  # chronological order
            if key:
                lines.append(f"- **{key}**: {text}")
            else:
                lines.append(f"- {text}")

        return [
            ContextBlock(
                id="memories",
                text="\n".join(lines),
                stability=Stability.SESSION,
            )
        ]
