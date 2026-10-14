from pathlib import Path

from harness.memory.fts5_index import FTS5MemoryIndex


def test_fts5_indexing_and_seeking(tmp_path: Path):
    db_path = tmp_path / "memory.db"
    index = FTS5MemoryIndex(db_path)

    transcript_file = tmp_path / "transcript_full.jsonl"
    line1 = '{"step_index": 1, "content": "Refactored JWT authentication tokens"}\n'
    line2 = '{"step_index": 2, "content": "Configured SQLite database WAL mode"}\n'

    off1 = 0
    transcript_file.write_bytes(line1.encode("utf-8"))
    off2 = len(line1.encode("utf-8"))
    with open(transcript_file, "ab") as f:
        f.write(line2.encode("utf-8"))

    index.register_conversation("conv_101", title="Auth refactor")
    index.index_turn("conv_101", 1, transcript_file, off1, "USER", "USER_INPUT", "Refactored JWT authentication tokens")
    index.index_turn("conv_101", 2, transcript_file, off2, "MODEL", "PLANNER_RESPONSE", "Configured SQLite database WAL mode")

    # Search for JWT
    results = index.search("JWT")
    assert len(results) == 1
    assert results[0].step_index == 1
    assert results[0].full_content == "Refactored JWT authentication tokens"

    # Search for SQLite
    results2 = index.search("SQLite WAL")
    assert len(results2) == 1
    assert results2[0].step_index == 2
    assert results2[0].full_content == "Configured SQLite database WAL mode"

    index.close()
