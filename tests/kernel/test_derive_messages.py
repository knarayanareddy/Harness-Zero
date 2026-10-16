"""`derive_messages` is the only producer of a model request's message list (WS-04 §1.2).

Table-driven on purpose: the mapping from record kinds to messages *is* the contract between the log
and the model. Anything that can be reconstructed from the log must be; anything the log holds that the
model should not see (cards, decisions, approvals, turn markers) must not be.
"""

from __future__ import annotations

from typing import Any

import pytest

from harness.kernel.context import MODEL_VISIBLE_KINDS, derive_messages, records_to_messages
from harness.sdk.types import Role, TranscriptRecord


def _norm_block(b: Any) -> list[Any]:
    kind = getattr(b, "type", "")
    if kind == "text":
        return ["text", b.text]
    if kind == "thinking":
        return ["thinking", b.text]
    if kind == "tool_use":
        return ["tool_use", b.id, b.name, b.input]
    if kind == "tool_result":
        return ["tool_result", b.tool_use_id, bool(b.is_error), [_norm_block(c) for c in b.content]]
    if kind == "image":
        return ["image", getattr(b, "url", None) or "data"]
    return [kind]


def _norm(messages: list[Any]) -> list[Any]:
    return [
        {
            "role": m.role.value if isinstance(m.role, Role) else str(m.role),
            "blocks": [_norm_block(b) for b in m.content],
        }
        for m in messages
    ]


def _rec(**kw: Any) -> dict[str, Any]:
    return TranscriptRecord(**kw).to_dict()


def test_user_and_assistant_text():
    records = [_rec(role="user", content="hi"), _rec(role="assistant", content="hello")]
    assert _norm(derive_messages(records)) == [
        {"role": "user", "blocks": [["text", "hi"]]},
        {"role": "assistant", "blocks": [["text", "hello"]]},
    ]


def test_thinking_stays_with_the_assistant_turn():
    records = [_rec(role="assistant", content="answer", thinking="reasoning")]
    assert _norm(derive_messages(records)) == [
        {"role": "assistant", "blocks": [["thinking", "reasoning"], ["text", "answer"]]}
    ]


def test_tool_call_and_result_round_trip():
    records = [
        _rec(role="user", content="do it"),
        _rec(role="assistant", content="calling", tool_calls=[{"id": "c1", "name": "run_command", "arguments": {"command": "ls"}}]),
        _rec(
            role="user",
            tool_results=[{"tool_use_id": "c1", "content": [{"type": "text", "text": "afile"}], "is_error": False}],
        ),
    ]
    out = _norm(derive_messages(records))
    assert out == [
        {"role": "user", "blocks": [["text", "do it"]]},
        {
            "role": "assistant",
            "blocks": [
                ["text", "calling"],
                ["tool_use", "c1", "run_command", {"command": "ls"}],
            ],
        },
        {"role": "user", "blocks": [["tool_result", "c1", False, [["text", "afile"]]]]},
    ]


def test_media_blocks_survive():
    records = [
        {
            "v": 2,
            "kind": "user.message",
            "role": "user",
            "content": "look",
            "media": [{"type": "image", "media_type": "image/png", "url": "file:///x.png"}],
            "timestamp": 1.0,
        }
    ]
    assert _norm(derive_messages(records)) == [
        {"role": "user", "blocks": [["text", "look"], ["image", "file:///x.png"]]}
    ]


def test_compaction_summary_replaces_the_earlier_history():
    records = [
        _rec(role="user", content="long ago"),
        _rec(role="system", content="summary of the above", metadata={"compaction_summary": True}),
        _rec(role="user", content="now"),
    ]
    out = _norm(derive_messages(records))
    assert [m["blocks"][0][1] for m in out] == [
        "## Previous Conversation Summary\n\nsummary of the above",
        "now",
    ]
    assert out[0]["role"] == "user", "the summary is fed as a user message, as before WS-04"


def test_user_visible_only_kinds_are_not_model_visible():
    """Cards, decisions, approvals and turn markers are in the log for the *user*, not the model."""
    model_visible_before = len(
        derive_messages([_rec(role="user", content="go"), _rec(role="assistant", content="done")])
    )
    for kind in ("ui.card", "tool.decision", "approval.requested", "approval.resolved", "turn.started", "tool.call"):
        records = [
            _rec(role="user", content="go"),
            {"v": 2, "kind": kind, "role": "system", "content": "x", "timestamp": 1.0, "metadata": {}},
            _rec(role="assistant", content="done"),
        ]
        assert len(derive_messages(records)) == model_visible_before, kind
    assert not MODEL_VISIBLE_KINDS & {"ui.card", "tool.decision", "approval.requested", "tool.call"}


def test_system_notice_reaches_the_model_only_when_marked():
    quiet = _rec(role="system", content="checkpoint created", metadata={"checkpoint": "abc"})
    loud = _rec(role="system", content="permission mode is now plan", metadata={"model_visible": True})
    assert derive_messages([quiet]) == []
    assert _norm(derive_messages([loud])) == [{"role": "user", "blocks": [["text", "permission mode is now plan"]]}]


def test_dangling_tool_call_gets_a_synthetic_result():
    """A crash between `tool.call` and `tool.result` used to wedge the session forever.

    Anthropic-style providers 400 on a `tool_use` with no matching `tool_result`, so every later turn
    in that session failed the same way. Derive now closes the gap explicitly.
    """
    records = [
        _rec(role="user", content="go"),
        _rec(role="assistant", content="", tool_calls=[{"id": "c1", "name": "edit_file", "arguments": {}}]),
    ]
    out = derive_messages(records)
    assert len(out) == 3
    tail = out[-1]
    assert tail.role == Role.USER
    block = tail.content[0]
    assert block.tool_use_id == "c1" and block.is_error
    text = block.content[0].text
    assert "No result was recorded for 'edit_file'" in text
    assert tail.metadata.get("synthetic") is True


def test_dangling_result_is_merged_into_the_existing_tool_result_message():
    """Two calls, one result: the missing one is added to the *same* message, not after the next turn."""
    records = [
        _rec(
            role="assistant",
            content="",
            tool_calls=[
                {"id": "c1", "name": "view_file", "arguments": {}},
                {"id": "c2", "name": "view_file", "arguments": {}},
            ],
        ),
        _rec(role="user", tool_results=[{"tool_use_id": "c1", "content": [{"type": "text", "text": "one"}]}]),
        _rec(role="assistant", content="after"),
    ]
    out = derive_messages(records)
    assert len(out) == 3, "the repair adds no extra message"
    ids = [b.tool_use_id for b in out[1].content]
    assert ids == ["c2", "c1"], "the synthetic result sits with the real one, before the next assistant turn"
    assert out[1].metadata.get("repaired_tool_results") == 1
    assert _norm([out[2]])[0] == {"role": "assistant", "blocks": [["text", "after"]]}


def test_v1_records_without_kind_still_derive():
    v1 = [
        {"role": "user", "content": "hi", "timestamp": 1.0},
        {"role": "assistant", "content": "yo", "timestamp": 2.0},
        {"role": "user", "tool_results": [{"tool_use_id": "c9", "content": "text!"}], "timestamp": 3.0},
    ]
    out = _norm(derive_messages(v1))
    assert out == [
        {"role": "user", "blocks": [["text", "hi"]]},
        {"role": "assistant", "blocks": [["text", "yo"]]},
        {"role": "user", "blocks": [["tool_result", "c9", False, [["text", "text!"]]]]},
    ]


def test_legacy_compaction_type_marker_is_upgraded_on_read():
    records = [
        {"role": "user", "content": "old", "timestamp": 1.0},
        {"role": "system", "content": "the summary", "type": "COMPACTION_SUMMARY", "timestamp": 2.0},
        {"role": "user", "content": "new", "timestamp": 3.0},
    ]
    out = _norm(derive_messages(records))
    assert out[0]["blocks"][0][1].endswith("the summary")
    assert len(out) == 2


def test_empty_and_single_record_inputs():
    assert derive_messages([]) == []
    assert len(derive_messages([_rec(role="user", content="only")])) == 1


def test_alias_is_the_same_function():
    assert records_to_messages is derive_messages


@pytest.mark.parametrize("kind", sorted(MODEL_VISIBLE_KINDS))
def test_model_visible_kinds_are_all_producible(kind: str):
    """Every kind in the model-visible set must be representable, or the set is a lie."""
    if kind == "user.message":
        rec = _rec(role="user", content="x")
    elif kind == "assistant.message":
        rec = _rec(role="assistant", content="x")
    elif kind == "tool.result":
        rec = _rec(role="user", tool_results=[{"tool_use_id": "c1", "content": []}])
    else:
        rec = _rec(role="system", content="x", metadata={"compaction_summary": True})
    assert TranscriptRecord.from_dict(rec).kind == kind
    assert derive_messages([rec]), kind


def test_the_message_counter_and_the_projection_agree_on_what_a_message_is() -> None:
    """`sessions.list` counts messages; `derive_messages` builds them. Same question, one answer.

    If these two sets drift, either the listing counts turn markers as conversation or the model stops
    seeing something a human can see — both are the "two sources of truth" shape WS-04 removed.
    """
    from harness.kernel.transcript import MESSAGE_KINDS

    assert MESSAGE_KINDS == MODEL_VISIBLE_KINDS
