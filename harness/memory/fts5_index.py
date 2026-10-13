"""
Embedded SQLite FTS5 Memory Engine.
Indexes session turns with byte-offsets into transcript_full.jsonl
for O(1) recall, BM25 full-text search, and cross-session memory.
"""
from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone
import json
from pathlib import Path
import sqlite3
from typing import Any, Dict, List, Optional


@dataclass
class SearchResult:
    conversation_id: str
    step_index: int
    file_path: str
    byte_offset: int
    snippet: str
    rank: float
    full_content: Optional[str] = None


class FTS5MemoryIndex:
    def __init__(self, db_path: Path):
        self.db_path = Path(db_path)
        self.db_path.parent.mkdir(parents=True, exist_ok=True)
        self.conn = sqlite3.connect(str(self.db_path), check_same_thread=False)
        self.conn.execute("PRAGMA journal_mode=WAL;")
        self.conn.execute("PRAGMA synchronous=NORMAL;")
        self._init_schema()

    def _init_schema(self) -> None:
        with self.conn:
            self.conn.executescript(
                """
                -- 1. Conversation Summaries and Metadata
                CREATE TABLE IF NOT EXISTS conversation_summaries (
                    conversation_id TEXT PRIMARY KEY,
                    title TEXT NOT NULL DEFAULT '',
                    preview TEXT NOT NULL DEFAULT '',
                    step_count INTEGER NOT NULL DEFAULT 0,
                    last_modified_time TEXT NOT NULL,
                    workspace_uris TEXT NOT NULL DEFAULT '',
                    status TEXT NOT NULL DEFAULT 'ACTIVE',
                    agent_name TEXT NOT NULL DEFAULT 'harness-zero',
                    parent_conversation_id TEXT NOT NULL DEFAULT ''
                );

                -- 2. FTS5 Virtual Table for Fast Full-Text Search
                CREATE VIRTUAL TABLE IF NOT EXISTS conversations_fts USING fts5(
                    conversation_id UNINDEXED,
                    step_index UNINDEXED,
                    file_path UNINDEXED,
                    byte_offset UNINDEXED,
                    source,
                    step_type,
                    content,
                    tokenize = 'unicode61 remove_diacritics 2'
                );
                """
            )

    def register_conversation(
        self,
        conversation_id: str,
        title: str = "",
        workspace_uri: str = "",
        agent_name: str = "harness-zero",
        parent_id: str = "",
    ) -> None:
        now_str = datetime.now(timezone.utc).isoformat()
        with self.conn:
            self.conn.execute(
                """
                INSERT INTO conversation_summaries
                (conversation_id, title, last_modified_time, workspace_uris, agent_name, parent_conversation_id)
                VALUES (?, ?, ?, ?, ?, ?)
                ON CONFLICT(conversation_id) DO UPDATE SET
                    title=excluded.title,
                    last_modified_time=excluded.last_modified_time,
                    workspace_uris=excluded.workspace_uris;
                """,
                (conversation_id, title, now_str, workspace_uri, agent_name, parent_id),
            )

    def index_turn(
        self,
        conversation_id: str,
        step_index: int,
        transcript_path: Path,
        byte_offset: int,
        source: str,
        step_type: str,
        content: str,
    ) -> None:
        if not content.strip():
            return

        with self.conn:
            self.conn.execute(
                """
                INSERT INTO conversations_fts
                (conversation_id, step_index, file_path, byte_offset, source, step_type, content)
                VALUES (?, ?, ?, ?, ?, ?, ?);
                """,
                (
                    conversation_id,
                    step_index,
                    str(transcript_path),
                    byte_offset,
                    source,
                    step_type,
                    content,
                ),
            )
            # Update step count
            self.conn.execute(
                """
                UPDATE conversation_summaries
                SET step_count = MAX(step_count, ?),
                    last_modified_time = ?
                WHERE conversation_id = ?;
                """,
                (step_index, datetime.now(timezone.utc).isoformat(), conversation_id),
            )

    def search(
        self,
        query: str,
        limit: int = 5,
        fetch_exact: bool = True,
        conversation_id: Optional[str] = None,
    ) -> List[SearchResult]:
        """
        Runs BM25 FTS5 search. If fetch_exact=True, uses byte_offset to seek directly
        into transcript_full.jsonl and retrieve the raw JSON record in microseconds.
        """
        # Sanitize query for FTS5 (avoid syntax errors on special characters)
        sanitized = "".join(c if c.isalnum() or c.isspace() else " " for c in query).strip()
        if not sanitized:
            return []

        # Formulate MATCH expression
        terms = sanitized.split()
        match_expr = " OR ".join(f'"{t}"' for t in terms)

        sql = """
            SELECT conversation_id, step_index, file_path, byte_offset,
                   snippet(conversations_fts, 6, '>>>', '<<<', '...', 15) as snip,
                   rank
            FROM conversations_fts
            WHERE conversations_fts MATCH ?
        """
        params: List[Any] = [match_expr]

        if conversation_id:
            sql += " AND conversation_id = ?"
            params.append(conversation_id)

        sql += " ORDER BY rank LIMIT ?"
        params.append(limit)

        results: List[SearchResult] = []
        cursor = self.conn.execute(sql, params)
        for row in cursor.fetchall():
            conv_id, step_idx, fpath, byte_off, snip, rank = row
            byte_off = int(byte_off)
            step_idx = int(step_idx)

            full_content = None
            if fetch_exact and fpath and Path(fpath).exists():
                try:
                    with open(fpath, "rb") as f:
                        f.seek(byte_off)
                        line = f.readline().decode("utf-8", errors="replace")
                        record = json.loads(line)
                        full_content = record.get("content", "")
                except Exception:
                    pass

            results.append(
                SearchResult(
                    conversation_id=conv_id,
                    step_index=step_idx,
                    file_path=fpath,
                    byte_offset=byte_off,
                    snippet=snip,
                    rank=float(rank),
                    full_content=full_content,
                )
            )

        return results

    def close(self) -> None:
        self.conn.close()
