"""v1 transcripts (written before WS-04) must read as v2 records — without losing anything.

The upgrade is a read-time view, never a rewrite: a log line is immutable once appended, and "upgrading"
a session by rewriting it would make the audit trail describe what the *current* code thinks happened.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import pytest

from harness.kernel.transcript import RECORD_VERSION, TranscriptLog, is_v2, upgrade_record_v1
from harness.sdk.types import RECORD_KINDS, TranscriptRecord


def test_plain_user_line_becomes_a_user_message_record():
    out = upgrade_record_v1({"role": "user", "content": "hi", "timestamp": 1.0})
    assert out["kind"] == "user.message"
    assert out["v"] == RECORD_VERSION
    assert is_v2(out)


def test_assistant_line_with_tool_calls_is_an_assistant_record():
    out = upgrade_record_v1(
        {"role": "assistant", "content": "calling", "tool_calls": [{"id": "c1", "name": "view_file"}], "timestamp": 1.0}
    )
    assert out["kind"] == "assistant.message"


def test_user_line_carrying_tool_results_is_a_tool_result_record():
    out = upgrade_record_v1(
        {"role": "user", "tool_results": [{"tool_use_id": "c1", "content": [], "is_error": False}], "timestamp": 1.0}
    )
    assert out["kind"] == "tool.result"


def test_legacy_single_result_shape_is_normalised_into_tool_results():
    out = upgrade_record_v1(
        {"role": "user", "tool_use_id": "c7", "content": "the output", "is_error": True, "timestamp": 1.0}
    )
    assert out["tool_results"] == [
        {"tool_use_id": "c7", "content": [{"type": "text", "text": "the output"}], "is_error": True}
    ]
    assert out["kind"] == "tool.result"


def test_legacy_compaction_type_becomes_a_compaction_summary_record():
    out = upgrade_record_v1({"role": "system", "content": "summary", "type": "COMPACTION_SUMMARY", "timestamp": 1.0})
    assert out["kind"] == "compaction.summary"
    assert out["metadata"]["compaction_summary"] is True


def test_store_annotations_are_preserved():
    """The store stamps `call_id`/`tool_name`/`full_offset`/`truncated_fields` on lines the UI reads.

    A round-trip through the dataclass would drop them, so the upgrader edits the dict in place — this
    test is the reason it must not be "simplified" into one.
    """
    v1 = {
        "role": "user",
        "content": "",
        "tool_results": [{"tool_use_id": "c1", "content": [{"type": "text", "text": "x" * 900}]}],
        "call_id": "c1",
        "tool_name": "run_command",
        "is_error": False,
        "full_offset": 4812,
        "truncated_fields": ["content"],
        "timestamp": 1.0,
    }
    out = upgrade_record_v1(v1)
    for key in ("call_id", "tool_name", "is_error", "full_offset", "truncated_fields"):
        assert out[key] == v1[key], key


def test_upgrade_is_idempotent_and_lossless_for_v2():
    v2 = TranscriptRecord(role="user", content="hi", step_index=3).to_dict()
    once = upgrade_record_v1(v2)
    twice = upgrade_record_v1(once)
    assert once == twice == v2


def test_an_unknown_kind_from_the_future_is_not_fatal_on_read():
    """A file written by a newer harness must still be replayable, not raise on load."""
    out = upgrade_record_v1({"role": "user", "content": "hi", "kind": "quantum.message", "timestamp": 1.0})
    assert out["kind"] == "user.message"  # falls back to inference
    rec = TranscriptRecord.from_dict({"role": "user", "content": "hi", "kind": "quantum.message"})
    assert rec.kind in RECORD_KINDS


def test_constructing_a_record_with_an_unknown_kind_is_an_error():
    with pytest.raises(ValueError, match="unknown transcript record kind"):
        TranscriptRecord(role="user", kind="nonsense")


# ------------------------------------------------------------------ through a store


class _FakeStore:
    def __init__(self, path: Path) -> None:
        self.path = path
        self.appended: list[dict[str, Any]] = []

    async def read(self, since_step: int = 0, compact: bool = True) -> list[dict[str, Any]]:
        out = []
        for line in self.path.read_text(encoding="utf-8").splitlines():
            if line.strip():
                rec = json.loads(line)
                if int(rec.get("step_index", 0) or 0) >= since_step:
                    out.append(rec)
        return out

    async def append(self, record: dict[str, Any]) -> int:
        self.appended.append(record)
        step = len(self.appended)
        with open(self.path, "a", encoding="utf-8") as f:
            f.write(json.dumps({**record, "step_index": step}) + "\n")
        return step


@pytest.mark.asyncio
async def test_log_reads_an_old_session_file_as_typed_records(tmp_path: Path):
    old = tmp_path / "transcript.jsonl"
    old.write_text(
        json.dumps({"role": "user", "content": "before the upgrade", "timestamp": 1.0, "step_index": 1}) + "\n"
        + json.dumps(
            {
                "role": "assistant",
                "content": "answer",
                "timestamp": 2.0,
                "step_index": 2,
                "call_id": "",
                "full_offset": 120,
            }
        )
        + "\n",
        encoding="utf-8",
    )
    log = TranscriptLog(_FakeStore(old), session_id="s1")
    records = await log.load()
    assert [r["kind"] for r in records] == ["user.message", "assistant.message"]
    assert all(is_v2(r) for r in records)
    assert records[1]["full_offset"] == 120, "the upgrade must not drop store annotations"
    assert log.loaded is True


@pytest.mark.asyncio
async def test_appending_through_the_log_writes_a_v2_line_and_caches_it(tmp_path: Path):
    target = tmp_path / "transcript.jsonl"
    target.write_text("", encoding="utf-8")
    store = _FakeStore(target)
    log = TranscriptLog(store, session_id="s1")
    await log.load()
    step = await log.append(TranscriptRecord(role="user", content="go"))
    assert step == 1
    assert json.loads(target.read_text(encoding="utf-8").splitlines()[0])["kind"] == "user.message"
    assert log.records[-1]["kind"] == "user.message", "the cache is the log, not a copy of it"
    assert store.appended[0]["role"] == "user"


@pytest.mark.asyncio
async def test_log_without_a_store_is_explicitly_ephemeral(tmp_path: Path):
    log = TranscriptLog(None, session_id="s1")
    step = await log.append_notice("session resumed")
    assert step == 1
    assert log.records[0]["kind"] == "system.notice"
    assert await log.read() == log.records
