"""
Memory Tool handlers: search_memory and remember (PLG-14).
"""
from __future__ import annotations

import logging
import re
import sqlite3
from datetime import UTC, datetime
from typing import Any

from harness.sdk.cards import Card
from harness.sdk.context import ToolContext
from harness.sdk.types import RiskClass, TextBlock, ToolResult, ToolSpec

logger = logging.getLogger(__name__)

SEARCH_MEMORY_SPEC = ToolSpec(
    name="search_memory",
    description="Searches previous session turns, transcripts, and conversations using full-text search.",
    input_schema={
        "type": "object",
        "properties": {
            "query": {
                "type": "string",
                "description": "Full-text search query string.",
            },
            "scope": {
                "type": "string",
                "enum": ["workspace", "session"],
                "default": "workspace",
                "description": "Scope of search: across workspace or within current session.",
            },
            "limit": {
                "type": "integer",
                "minimum": 1,
                "maximum": 50,
                "default": 10,
                "description": "Maximum number of search results to return.",
            },
        },
        "required": ["query"],
        "additionalProperties": False,
    },
    risk_class=RiskClass.READ,
)

REMEMBER_SPEC = ToolSpec(
    name="remember",
    description="Stores a persistent fact, preference, or architectural decision across sessions.",
    input_schema={
        "type": "object",
        "properties": {
            "text": {
                "type": "string",
                "description": "The fact, preference, or architectural decision to remember permanently.",
            },
            "key": {
                "type": "string",
                "description": "Optional category or lookup key for the memory.",
            },
        },
        "required": ["text"],
        "additionalProperties": False,
    },
    risk_class=RiskClass.READ,
)


def _safe_query(query: str) -> str:
    tokens = re.findall(r"\w+", query)
    return " ".join(f'"{t}"' for t in tokens) if tokens else ""


class SearchMemoryHandler:
    """Handles search_memory tool execution."""

    async def __call__(self, args: dict[str, Any], ctx: ToolContext) -> ToolResult:
        query = args.get("query", "").strip()
        scope = args.get("scope", "workspace")
        limit = int(args.get("limit", 10))

        if not query:
            return ToolResult(content=[TextBlock(text="Error: query parameter cannot be empty")], is_error=True)

        db_path = ctx.data_dir / "memory.db"
        if not db_path.exists():
            return ToolResult(content=[TextBlock(text=f"No memories or transcripts found matching '{query}'.")])

        safe_q = _safe_query(query)
        if not safe_q:
            return ToolResult(content=[TextBlock(text=f"No results found for query '{query}'.")])

        conn = sqlite3.connect(str(db_path))
        try:
            cursor = conn.cursor()
            if scope == "session" and hasattr(ctx, "session_id") and ctx.session_id:
                cursor.execute(
                    """
                    SELECT conversation_id, step_index, source, step_type,
                           snippet(turns_fts, 4, '[', ']', '...', 32) as snip, rank
                    FROM turns_fts
                    WHERE turns_fts MATCH ? AND conversation_id = ?
                    ORDER BY rank
                    LIMIT ?
                    """,
                    (safe_q, ctx.session_id, limit),
                )
            else:
                cursor.execute(
                    """
                    SELECT conversation_id, step_index, source, step_type,
                           snippet(turns_fts, 4, '[', ']', '...', 32) as snip, rank
                    FROM turns_fts
                    WHERE turns_fts MATCH ?
                    ORDER BY rank
                    LIMIT ?
                    """,
                    (safe_q, limit),
                )
            rows = cursor.fetchall()
        except sqlite3.OperationalError as e:
            logger.debug("FTS search error: %s", e)
            return ToolResult(content=[TextBlock(text=f"No results found for query '{query}'.")])
        finally:
            conn.close()

        if not rows:
            return ToolResult(content=[TextBlock(text=f"No results found for query '{query}'.")])

        lines = [f"Found {len(rows)} matches for '{query}':\n"]
        table_rows = []
        for r in rows:
            conv_id, step_idx, source, _, snippet, _ = r
            lines.append(f"- [{conv_id} step {step_idx}] {snippet}")
            table_rows.append([str(step_idx), source, snippet])

        card = Card.table(
            columns=["Step", "Source", "Snippet"],
            rows=table_rows,
            title=f"Memory Search: {query}",
        )

        return ToolResult(
            content=[TextBlock(text="\n".join(lines))],
            cards=[card],
        )


class RememberHandler:
    """Handles remember tool execution."""

    async def __call__(self, args: dict[str, Any], ctx: ToolContext) -> ToolResult:
        text = args.get("text", "").strip()
        key = args.get("key", "").strip() or None

        if not text:
            return ToolResult(content=[TextBlock(text="Error: text parameter cannot be empty")], is_error=True)

        db_path = ctx.data_dir / "memory.db"
        db_path.parent.mkdir(parents=True, exist_ok=True)
        conn = sqlite3.connect(str(db_path))
        try:
            with conn:
                conn.execute(
                    """
                    CREATE TABLE IF NOT EXISTS remembered_facts (
                        id INTEGER PRIMARY KEY AUTOINCREMENT,
                        key TEXT NOT NULL DEFAULT '',
                        text TEXT NOT NULL,
                        session_id TEXT NOT NULL DEFAULT '',
                        created_at TEXT NOT NULL
                    )
                    """
                )
                cur = conn.execute(
                    """
                    INSERT INTO remembered_facts (key, text, session_id, created_at)
                    VALUES (?, ?, ?, ?)
                    """,
                    (key or "", text, getattr(ctx, "session_id", ""), datetime.now(UTC).isoformat()),
                )
                fact_id = cur.lastrowid or 0
        finally:
            conn.close()

        key_str = f" [{key}]" if key else ""
        output_msg = f"Permanently remembered fact #{fact_id}{key_str}: {text}"
        card = Card.notice(output_msg, level="info")

        return ToolResult(
            content=[TextBlock(text=output_msg)],
            cards=[card],
        )
