"""
SQLite FTS5 Search Index and Memory Store implementation (PLG-14).
Absorbs LOOP-06 and LOOP-07.
"""
from __future__ import annotations

import json
import logging
import re
import sqlite3
from datetime import UTC, datetime
from pathlib import Path
from typing import Any, Literal

logger = logging.getLogger(__name__)


def record_row(record: dict[str, Any]) -> tuple[int, str, str, str] | None:
    """Project one session-log record onto an FTS row: ``(step_index, source, step_type, content)``.

    The session log is the source of truth (WS-04) and this table is derived from it, so the only rule is
    what a record looks like: v2 names itself with `kind` and `role`, and the v1 fields (`type`, `source`)
    are accepted for old workspaces. The live hook (`FTS5SearchIndex.index`) and the rebuild walk go
    through here, so a reindexed workspace cannot end up with different rows than a session that was
    indexed while it ran. Returns ``None`` for a record with no text to search.
    """
    content = record.get("content") or ""
    if isinstance(content, list):
        # Extract text from content blocks
        parts = []
        for item in content:
            if isinstance(item, dict) and "text" in item:
                parts.append(str(item["text"]))
            elif isinstance(item, str):
                parts.append(item)
        content = " ".join(parts)
    elif not isinstance(content, str):
        content = str(content)
    if not content.strip():
        return None
    try:
        step_index = int(record.get("step_index", 0))
    except (TypeError, ValueError):
        step_index = 0
    source = str(record.get("role") or record.get("source") or "UNKNOWN")
    step_type = str(record.get("kind") or record.get("type") or "UNKNOWN")
    return step_index, source, step_type, content


def _sanitize_fts_query(query: str) -> str:
    """Escapes special characters in query string to prevent FTS5 syntax errors."""
    tokens = re.findall(r"\w+", query)
    if not tokens:
        return ""
    return " ".join(f'"{t}"' for t in tokens)


class FTS5SearchIndex:
    """
    Workspace-scoped SQLite FTS5 search index and persistent memory storage.
    """

    def __init__(self, db_path: Path | str) -> None:
        self.db_path = Path(db_path).resolve()
        self.db_path.parent.mkdir(parents=True, exist_ok=True)
        self.conn = sqlite3.connect(str(self.db_path), check_same_thread=False)
        self.conn.execute("PRAGMA journal_mode=WAL;")
        self.conn.execute("PRAGMA synchronous=NORMAL;")
        self._init_schema()

    def _init_schema(self) -> None:
        with self.conn:
            self.conn.executescript(
                """
                CREATE TABLE IF NOT EXISTS conversation_summaries (
                    conversation_id TEXT PRIMARY KEY,
                    title TEXT NOT NULL DEFAULT '',
                    step_count INTEGER NOT NULL DEFAULT 0,
                    last_modified_time TEXT NOT NULL
                );

                CREATE VIRTUAL TABLE IF NOT EXISTS turns_fts USING fts5(
                    conversation_id UNINDEXED,
                    step_index UNINDEXED,
                    source,
                    step_type,
                    content,
                    tokenize = 'unicode61 remove_diacritics 2'
                );

                CREATE TABLE IF NOT EXISTS remembered_facts (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    key TEXT NOT NULL DEFAULT '',
                    text TEXT NOT NULL,
                    session_id TEXT NOT NULL DEFAULT '',
                    created_at TEXT NOT NULL
                );
                """
            )

    async def index(self, session_id: str, record: dict[str, Any]) -> None:
        """Indexes a transcript record into FTS5."""
        row = record_row(record)
        if row is None:
            return
        step_index, source, step_type, content = row

        with self.conn:
            self.conn.execute(
                """
                INSERT INTO turns_fts (conversation_id, step_index, source, step_type, content)
                VALUES (?, ?, ?, ?, ?)
                """,
                (session_id, step_index, source, step_type, content),
            )

            now_str = datetime.now(UTC).isoformat()
            self.conn.execute(
                """
                INSERT INTO conversation_summaries (conversation_id, step_count, last_modified_time)
                VALUES (?, 1, ?)
                ON CONFLICT(conversation_id) DO UPDATE SET
                    step_count = step_count + 1,
                    last_modified_time = excluded.last_modified_time;
                """,
                (session_id, now_str),
            )

    async def search(
        self,
        query: str,
        *,
        scope: Literal["session", "workspace"] = "workspace",
        session_id: str | None = None,
        limit: int = 10,
    ) -> list[dict[str, Any]]:
        """Searches FTS5 index matching query."""
        safe_query = _sanitize_fts_query(query)
        if not safe_query:
            return []

        cursor = self.conn.cursor()
        results: list[dict[str, Any]] = []

        if scope == "session" and session_id:
            sql = """
                SELECT conversation_id, step_index, source, step_type,
                       snippet(turns_fts, 4, '[', ']', '...', 32) as snip, rank
                FROM turns_fts
                WHERE turns_fts MATCH ? AND conversation_id = ?
                ORDER BY rank
                LIMIT ?
            """
            cursor.execute(sql, (safe_query, session_id, limit))
        else:
            sql = """
                SELECT conversation_id, step_index, source, step_type,
                       snippet(turns_fts, 4, '[', ']', '...', 32) as snip, rank
                FROM turns_fts
                WHERE turns_fts MATCH ?
                ORDER BY rank
                LIMIT ?
            """
            cursor.execute(sql, (safe_query, limit))

        for row in cursor.fetchall():
            results.append({
                "conversation_id": row[0],
                "step_index": row[1],
                "source": row[2],
                "step_type": row[3],
                "snippet": row[4],
                "rank": float(row[5]),
            })

        return results

    async def remember(self, text: str, key: str | None = None, session_id: str = "") -> int:
        """Stores a persistent remembered fact."""
        now_str = datetime.now(UTC).isoformat()
        with self.conn:
            cur = self.conn.execute(
                """
                INSERT INTO remembered_facts (key, text, session_id, created_at)
                VALUES (?, ?, ?, ?)
                """,
                (key or "", text, session_id, now_str),
            )
            return cur.lastrowid or 0

    async def list_memories(self, limit: int = 50) -> list[dict[str, Any]]:
        """Lists remembered facts in reverse chronological order."""
        cursor = self.conn.cursor()
        cursor.execute(
            """
            SELECT id, key, text, session_id, created_at
            FROM remembered_facts
            ORDER BY id DESC
            LIMIT ?
            """,
            (limit,),
        )
        return [
            {
                "id": r[0],
                "key": r[1],
                "text": r[2],
                "session_id": r[3],
                "created_at": r[4],
            }
            for r in cursor.fetchall()
        ]

    def maybe_reindex_from_sessions(self, sessions_dir: Path) -> int:
        """Walks session directories and indexes transcripts if database is fresh."""
        cursor = self.conn.cursor()
        cursor.execute("SELECT count(*) FROM turns_fts")
        count = cursor.fetchone()[0]
        if count > 0:
            return 0
        return self.rebuild_from_sessions(sessions_dir)[1]

    def rebuild_from_sessions(self, sessions_dir: Path, *, dry_run: bool = False) -> tuple[int, int]:
        """Re-index every session transcript from scratch; returns ``(sessions, rows)``.

        FTS5 is a *derived* index: the session log is the source of truth (WS-04), so throwing the table
        away and rebuilding it from ``transcript.jsonl`` is always safe. That is what
        ``harness sessions reindex`` does, and what to run after a crash, a partial write, or a record
        format upgrade. Records are read as v2 (``kind``/``role``) with a fallback to the v1 fields, so an
        old workspace rebuilds into a searchable index rather than a table of ``UNKNOWN`` rows.
        """
        dir_path = Path(sessions_dir)
        if not dir_path.exists():
            return 0, 0
        if not dry_run:
            with self.conn:
                self.conn.execute("DELETE FROM turns_fts")
                self.conn.execute("DELETE FROM conversation_summaries")

        indexed = 0
        per_session: dict[str, int] = {}
        for session_folder in sorted(dir_path.iterdir()):
            if not session_folder.is_dir():
                continue
            transcript_file = session_folder / "transcript.jsonl"
            if not transcript_file.exists():
                continue
            session_id = session_folder.name
            per_session[session_id] = 0
            try:
                for line in transcript_file.read_text(encoding="utf-8").splitlines():
                    line = line.strip()
                    if not line:
                        continue
                    row = record_row(json.loads(line))
                    if row is None:
                        continue
                    step_index, source, step_type, content = row
                    if not dry_run:
                        with self.conn:
                            self.conn.execute(
                                """
                                INSERT INTO turns_fts (conversation_id, step_index, source, step_type, content)
                                VALUES (?, ?, ?, ?, ?)
                                """,
                                (session_id, step_index, source, step_type, content),
                            )
                    per_session[session_id] += 1
                    indexed += 1
            except Exception as e:
                logger.debug("Failed re-indexing session %s: %s", session_id, e)

        if not dry_run:
            # The summaries table is the session list the UI reads; rebuild it from the same walk so a
            # reindex also repairs a count that drifted from the log.
            now_str = datetime.now(UTC).isoformat()
            with self.conn:
                for session_id, count in per_session.items():
                    self.conn.execute(
                        """
                        INSERT INTO conversation_summaries (conversation_id, step_count, last_modified_time)
                        VALUES (?, ?, ?)
                        ON CONFLICT(conversation_id) DO UPDATE SET
                            step_count = excluded.step_count,
                            last_modified_time = excluded.last_modified_time
                        """,
                        (session_id, count, now_str),
                    )
        return len(per_session), indexed

    def close(self) -> None:
        self.conn.close()
