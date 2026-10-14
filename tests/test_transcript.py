from pathlib import Path

from harness.core.transcript import TranscriptEngine
from harness.core.types import StepSource, StepType


def test_transcript_truncation_and_offset_seeking(tmp_path: Path):
    session_dir = tmp_path / "session_01"
    engine = TranscriptEngine(session_dir, truncation_threshold=50)

    short_text = "Hello world"
    idx1, off1 = engine.append_step(StepSource.USER, StepType.USER_INPUT, content=short_text)
    assert idx1 == 1

    # Large text should trigger truncation in compact log
    long_text = "A" * 200
    idx2, off2 = engine.append_step(StepSource.MODEL, StepType.PLANNER_RESPONSE, content=long_text)
    assert idx2 == 2

    # Verify compact log has truncation
    compact_records = engine.read_compact_history()
    assert len(compact_records) == 2
    assert compact_records[0]["content"] == short_text
    assert "truncated_fields" not in compact_records[0]

    assert compact_records[1]["content"].endswith("... [TRUNCATED]")
    assert "content" in compact_records[1]["truncated_fields"]

    # Verify O(1) seek directly returns full unshortened payload
    full_step2 = engine.get_step(2, full=True)
    assert full_step2 is not None
    assert full_step2["content"] == long_text


def test_trajectory_compaction(tmp_path: Path):
    session_dir = tmp_path / "session_02"
    engine = TranscriptEngine(session_dir)

    summary = engine.compact_trajectory(
        task_overview="Build auth system",
        completed_steps=["Created user schema", "Wrote unit tests"],
        active_context=["Testing password hash"],
        open_commitments=["Add rate limiter"],
    )

    assert "<CONTEXT_SUMMARY>" in summary
    assert "# Task Overview" in summary
    assert "Build auth system" in summary

    compact_records = engine.read_compact_history()
    assert len(compact_records) == 1
    assert compact_records[0]["type"] == StepType.COMPACTION_SUMMARY.value
