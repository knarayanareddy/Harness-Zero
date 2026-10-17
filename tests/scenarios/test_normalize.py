"""The golden stabiliser (`_lib/normalize.py`).

Goldens are only worth having if they are *reproducible*: the same flow on another machine, in another temp
directory, at another wall-clock time has to produce the same bytes. Everything that varies is either dropped,
turned into a placeholder, or replaced by an ordinal in first-seen order — the last so that a session id that
leaks into prose still shows up as a stable token and an id-swap between two runs is still a diff.
"""
from __future__ import annotations

from pathlib import Path

from _lib.normalize import LIVE_NOISE, Normalizer, collapse_deltas
from _lib.replay_provider import golden_view


def _norm(tmp_path: Path, **kw) -> Normalizer:
    return Normalizer(workspace=tmp_path / "ws", session_id="session_0f2b1c9d", **kw)


def test_bwrap_unavailability_reason_is_host_independent(tmp_path: Path):
    n = _norm(tmp_path)
    missing = {
        "backend": "bwrap",
        "available": False,
        "reason": "bubblewrap (bwrap) not found on PATH; install bubblewrap to enable process confinement",
    }
    blocked = {
        "backend": "bwrap",
        "available": False,
        "reason": "bwrap is present but the required user/PID/network namespaces are unavailable: permission denied",
    }

    assert n.value({"sandbox": missing})["sandbox"]["reason"] == "<bwrap unavailable on this host>"
    assert n.value({"sandbox": blocked})["sandbox"]["reason"] == "<bwrap unavailable on this host>"
    assert n.value({"reason": missing["reason"]})["reason"] == missing["reason"]


def test_request_goldens_ignore_host_only_tools_without_mutating_requests():
    request = {"system_text": "volatile", "tools": ["run_command", "take_screenshot", "view_file"]}

    assert golden_view([request]) == [{"tools": ["run_command", "view_file"]}]
    assert request["tools"] == ["run_command", "take_screenshot", "view_file"]


def test_paths_and_the_session_id_become_placeholders(tmp_path: Path):
    n = _norm(tmp_path)
    ws = str((tmp_path / "ws").resolve())
    assert n.text(f"wrote {ws}/notes.txt") == "wrote <ws>/notes.txt"
    assert n.text(f"see {ws}") == "see <ws>", "the bare path, not just paths under it"
    # Anything just outside the jail lands in the run's temp dir, which gets its own token so a leak out of
    # the workspace stays visible in a golden instead of looking like a path that merely differs.
    assert n.text(f"outside the jail: {tmp_path}/secret.txt") == "outside the jail: <ws-parent>/secret.txt"
    assert n.text("session_0f2b1c9d resumed") == "<session> resumed"
    assert n.text("/tmp/out.log") == "<tmp>/out.log"


def test_a_second_session_in_the_same_run_gets_its_own_label(tmp_path: Path):
    n = _norm(tmp_path)
    assert n.value("session_0f2b1c9d", "session_id") == "<session>"
    # Another id under the same key must not be flattened onto the first one, or a session-crossing bug
    # (an event published to the wrong session) would normalise into invisibility.
    assert n.value("session_aaaaaa11", "session_id") == "session#1"


def test_ids_become_ordinals_in_first_seen_order(tmp_path: Path):
    n = _norm(tmp_path)
    events = [
        {"type": "tool.call", "data": {"call_id": "call_111111aa"}},
        {"type": "tool.result", "data": {"call_id": "call_222222bb", "tool_use_id": "call_111111aa"}},
        {"type": "turn.started", "id": "evt_333333cc", "data": {"turn_id": "turn_444444dd"}},
    ]
    out = n.events(events)
    assert out[0]["data"]["call_id"] == "call#1"
    # `tool_use_id` and `call_id` are the same identifier in two spellings: one table, or a pair that
    # belongs together would look unrelated in the golden.
    assert out[1]["data"] == {"call_id": "call#2", "tool_use_id": "call#1"}
    assert out[2]["id"] == "evt#1" and out[2]["data"]["turn_id"] == "turn#1"
    assert n.events(events) == out, "normalisation is deterministic: same input, same ordinals"


def test_shadow_checkpoint_endpoints_share_stable_sha_ordinals(tmp_path: Path):
    n = _norm(tmp_path)
    base = "1" * 40
    head = "2" * 40
    normalized = n.value({"base": base, "head": head, "checkpoint_id": base, "sha": head})
    assert normalized == {
        "base": "sha#1",
        "checkpoint_id": "sha#1",
        "head": "sha#2",
        "sha": "sha#2",
    }


def test_an_id_in_prose_is_relabelled_exactly_like_the_key_version(tmp_path: Path):
    # `_HEX_ID` in `text()` exists because ids show up inside messages, tool output and notices, not just
    # under `call_id` keys. Both routes must consult the same ordinal table, or a golden reads as two
    # unrelated calls when it is one.
    n = _norm(tmp_path)
    assert n.text("call_111111aa failed for turn_444444dd") == "call#1 failed for turn#1"
    out = n.events([{"type": "tool.result", "data": {"call_id": "call_111111aa", "text": "call_111111aa failed"}}])
    assert out[0]["data"] == {"call_id": "call#1", "text": "call#1 failed"}


def test_noise_keys_vanish_and_floats_lose_precision(tmp_path: Path):
    n = _norm(tmp_path)
    row = {
        "ts": "2026-10-16T09:12:33.482115+02:00",
        "duration_ms": 41,
        "cost_usd": 0.0031,
        "input_tokens": 812,
        "workspace": str(Path(tmp_path / "ws").resolve()),
        "text": "took 12.3456s at 2026-10-16T09:12:33+02:00",
        "usd": 0.0031,
        "ratio": 1.23456,
    }
    out = n.value(row)
    assert set(out) == {"text", "ratio"}, "costs and token counts are noise; other floats only lose precision"
    assert out["text"] == "took <n>s at <ts>"
    assert out["ratio"] == 1.235


def test_collapse_deltas_fuses_a_stream_but_not_two_calls():
    def delta(text, **data):
        return {"type": "tool.output.delta", "data": {"text": text, **data}}

    events = [
        {"type": "model.thinking.delta", "data": {"text": "th"}},
        {"type": "model.thinking.delta", "data": {"text": "ink"}},
        {"type": "model.text.delta", "data": {"text": "Hel"}},
        {"type": "model.text.delta", "data": {"text": "lo"}},
        delta("a1", call_id="call_1", stream="stdout"),
        delta("a2", call_id="call_2", stream="stdout"),
        delta("b1", call_id="call_1", stream="stdout"),
        delta("x1", call_id="call_1", stream="stderr"),
        {"type": "turn.completed", "data": {}},
    ]
    out = collapse_deltas(events)
    assert [(e["type"], e.get("data", {}).get("text")) for e in out] == [
        ("model.thinking.delta", "think"),
        ("model.text.delta", "Hello"),
        # Interleaved calls keep their own lines: fusing them would hide an ordering bug, which is the
        # whole reason tool output is collapsed per (call, stream) rather than per type.
        ("tool.output.delta", "a1"),
        ("tool.output.delta", "a2"),
        ("tool.output.delta", "b1"),
        ("tool.output.delta", "x1"),
        ("turn.completed", None),
    ]
    assert {"model.text.delta", "tool.output.delta"} <= LIVE_NOISE, "counts that chunking decides are not evidence"
