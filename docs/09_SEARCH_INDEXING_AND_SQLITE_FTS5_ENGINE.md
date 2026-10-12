# 09: Search Indexing & SQLite FTS5 Engine
*Reverse-Engineered Schema, Byte-Offset Fast Seeking, and Cross-Session Recall*

---

## 1. The Long-Term Memory Retrieval Problem

In multi-day agent operations, an agent needs to recall past conversations, code decisions, and benchmark results without loading gigabytes of past JSON transcripts into LLM context.

Antigravity solves this with an embedded **SQLite FTS5 database** stored at:
[`~/.gemini/antigravity/conversation_summaries.db`](file:///Users/brightech/.gemini/antigravity/conversation_summaries.db)

```mermaid
flowchart TD
    subgraph ConversationEngine ["Active Agent Conversations"]
        LOGS["transcript.jsonl (On-Disk Logs)"]
    end

    subgraph SQLiteDB ["Embedded SQLite Storage (WAL Mode)"]
        SUMMARIES["conversation_summaries (Metadata Table)"]
        FTS["conversations_fts (FTS5 Virtual Table)"]
    end

    subgraph QueryEngine ["Instant Search & Retrieval"]
        SEARCH["BM25 Ranking + Recency Scorer"]
        SEEK["Byte-Offset Seek directly into JSONL"]
    end

    LOGS -->|Record offsets & summary| SUMMARIES
    LOGS -->|Tokenize chunks| FTS
    SEARCH --> FTS
    FTS -->|user_step_byte_offsets| SEEK
    SEEK -->|O(1) Instant File Read| PROMPT["Injected Context Snippet"]
```

---

## 2. Reverse-Engineered SQLite Schema

Extracted directly from Antigravity's active database:

```sql
-- 1. Conversation Metadata and Subagent Tree
CREATE TABLE `conversation_summaries` (
    `conversation_id` text PRIMARY KEY,
    `title` text NOT NULL DEFAULT "",
    `preview` text NOT NULL DEFAULT "",
    `step_count` integer NOT NULL DEFAULT 0,
    `last_modified_time` datetime NOT NULL,
    `workspace_uris` text NOT NULL,
    `status` text NOT NULL DEFAULT "",
    `source` text NOT NULL DEFAULT "",
    `project_id` text NOT NULL DEFAULT "",
    `agent_name` text NOT NULL DEFAULT "",
    `parent_conversation_id` text NOT NULL DEFAULT "",
    `nesting_depth` integer NOT NULL DEFAULT 0,
    `battle_id` text NOT NULL DEFAULT "",
    `winning_conversation_id` text NOT NULL DEFAULT "",
    `not_fully_idle` numeric NOT NULL DEFAULT false,
    `killed` numeric NOT NULL DEFAULT false,
    `last_user_input_time` datetime NOT NULL,
    `last_user_input_step_index` integer NOT NULL DEFAULT -1,
    `app_data_dir` text NOT NULL DEFAULT "",
    `raw_summary` blob,
    `group_id` text NOT NULL DEFAULT ""
);

CREATE INDEX `idx_conversation_summaries_last_user_input_time` 
ON `conversation_summaries`(`last_user_input_time`);

-- 2. High-Performance Full-Text Search Table
CREATE VIRTUAL TABLE conversations_fts USING fts5(
    conversation_id UNINDEXED,
    chunk_index UNINDEXED,
    chunk_first_step UNINDEXED,
    chunk_last_step UNINDEXED,
    last_modified_time UNINDEXED,
    step_count UNINDEXED,
    last_step_created_at UNINDEXED,
    archived UNINDEXED,
    user_step_byte_offsets UNINDEXED,    -- Byte offsets into transcript.jsonl
    agent_step_byte_offsets UNINDEXED,   -- Byte offsets into transcript.jsonl
    title,
    workspace,
    project,
    user_body,
    agent_body,
    tokenize = 'unicode61 remove_diacritics 2',
    prefix = '1 2 3'
);
```

### Key Architectural Innovations Discovered
1. **Byte-Offset O(1) Fast Seeking**:
   * Instead of storing massive transcripts in SQLite, `conversations_fts` stores comma-separated byte offsets (`user_step_byte_offsets`: `"0,412,1892,5401"`).
   * When an agent queries a past turn, the engine runs `file.seek(offset)` and reads only that turn in **microseconds** without parsing the rest of the file.
2. **Subagent Trees & Arena Battles**:
   * `parent_conversation_id` and `nesting_depth` represent subagent hierarchies.
   * `battle_id` and `winning_conversation_id` record parallel agent competitions.

---

## 3. Custom Harness Implementation (Python / SQLite3)

```python
"""
conversation_search_engine.py - SQLite FTS5 Indexer with Byte-Offset Seeking
"""
import sqlite3
from pathlib import Path
from typing import Any

class ConversationIndex:
    def __init__(self, db_path: Path):
        self.db_path = db_path
        self.conn = sqlite3.connect(str(db_path))
        self.conn.execute("PRAGMA journal_mode=WAL;")
        self._init_schema()

    def _init_schema(self):
        with self.conn:
            self.conn.executescript("""
            CREATE TABLE IF NOT EXISTS summaries (
                conversation_id TEXT PRIMARY KEY,
                title TEXT,
                step_count INTEGER,
                last_modified TIMESTAMP DEFAULT CURRENT_TIMESTAMP
            );
            CREATE VIRTUAL TABLE IF NOT EXISTS fts_index USING fts5(
                conversation_id UNINDEXED,
                file_path UNINDEXED,
                byte_offset UNINDEXED,
                content,
                tokenize = 'unicode61 remove_diacritics 2'
            );
            """)

    def index_turn(self, conversation_id: str, transcript_path: Path, byte_offset: int, content: str):
        with self.conn:
            self.conn.execute(
                "INSERT INTO fts_index (conversation_id, file_path, byte_offset, content) VALUES (?, ?, ?, ?);",
                (conversation_id, str(transcript_path), byte_offset, content)
            )

    def search(self, query: str, limit: int = 5) -> list[dict]:
        cursor = self.conn.execute(
            """
            SELECT conversation_id, file_path, byte_offset, snippet(fts_index, 3, '<b>', '</b>', '...', 15), rank
            FROM fts_index
            WHERE fts_index MATCH ?
            ORDER BY rank
            LIMIT ?;
            """,
            (query, limit)
        )
        results = []
        for row in cursor.fetchall():
            results.append({
                "conversation_id": row[0],
                "file_path": row[1],
                "byte_offset": int(row[2]),
                "snippet": row[3],
                "rank": row[4]
            })
        return results

    def read_exact_turn(self, file_path: str, byte_offset: int) -> str:
        """Instant O(1) byte-offset seek."""
        with open(file_path, "r", encoding="utf-8") as f:
            f.seek(byte_offset)
            return f.readline().strip()
```
