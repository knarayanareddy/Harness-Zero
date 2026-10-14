# FTS5 Search Store Plugin (PLG-14)

SQLite FTS5 full-text search index and memory database:
- Stores indexed session turns in workspace-scoped `<workspace>/.harness/memory.db`.
- Supports session-scoped and workspace-wide BM25 full-text queries.
- Automatically re-indexes existing transcripts on initialization if database is empty.
- Tracks persistent remembered facts for cross-session recall.
