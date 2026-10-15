"""
Memories Context Source implementation (PLG-14).
"""
from __future__ import annotations

import logging
import sqlite3
from typing import Any

from harness.sdk.paths import memory_db_path
from harness.sdk.types import ContextBlock, Stability

logger = logging.getLogger(__name__)


class MemoriesContextSource:
    """
    ContextSource reading persistent remembered facts and injecting them as a SESSION context block.
    """

    name: str = "memories"

    async def blocks(self, ctx: Any) -> list[ContextBlock]:
        workspace = getattr(ctx, "workspace", None)
        if not workspace:
            return []

        # Same file the `remember` tool writes and the FTS5 store indexes
        # (see harness.sdk.paths). HookContext carries no data_dir, and the
        # per-plugin data_dir would be the wrong place anyway.
        db_path = memory_db_path(workspace)
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
