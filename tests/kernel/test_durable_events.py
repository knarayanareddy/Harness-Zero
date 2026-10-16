"""WS-04b: durability is a contract between the session log and the event bus.

The schema says, per event type, whether the event mirrors a transcript record (``durable`` +
``record_kind``) or is streamed noise no replay may depend on (``transient``). That claim is only worth
anything if the code keeps it: a durable event must actually carry the record its publisher wrote
(``Event.record``), and the marker records it points at must stay out of model requests. The two failure
modes pinned here are therefore (1) a schema that promises a record no publisher writes — the client would
render an empty card after a reload — and (2) a log fact that leaks into the prompt.

See ``review/dsh-integration/WS-04_session_log_single_source.md`` §3.4 and §6.5.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import pytest

from harness.kernel.bootstrap import bootstrap_kernel
from harness.kernel.transcript import TranscriptLog
from harness.sdk.testing.scripted import ScriptedProvider
from harness.sdk.types import RECORD_KINDS, Done, ModelInfo, TextDelta, ToolCallEnd

SCHEMA_PATH = Path(__file__).resolve().parents[2] / "docs" / "events.schema.json"

MODEL = ModelInfo(
    id="scripted-v1",
    display_name="Scripted",
    context_tokens=128_000,
    input_usd_per_mtok=0.0,
    output_usd_per_mtok=0.0,
)


def _durability() -> dict[str, dict[str, Any]]:
    return json.loads(SCHEMA_PATH.read_text(encoding="utf-8"))["events"]


def _drain(sub) -> list[dict[str, Any]]:
    out: list[dict[str, Any]] = []
    while not sub.empty():
        ev = sub.get_nowait()
        out.append(ev.to_dict() if hasattr(ev, "to_dict") else dict(ev))
    return out


async def _boot(tmp_path: Path, responses: list[list[Any]]):
    """A booted kernel plus a scripted provider, with the bus tapped before anything runs."""
    ws = tmp_path / "ws"
    ws.mkdir()
    (ws / "app.py").write_text("print('hi')\n", encoding="utf-8")
    conf = tmp_path / "conf"
    conf.mkdir()
    rt = await bootstrap_kernel(
        workspace=ws,
        config_dir=conf,
        overrides={
            "kernel.security.permission_mode": "allow",
            "kernel.provider": "scripted",
            "kernel.model": MODEL.id,
        },
        resume_session=False,
    )
    provider = ScriptedProvider(models=[MODEL])
    for r in responses:
        provider.add_response(r)
    rt.registry.register_provider(provider, plugin_name="test_scripted")
    sub = rt.bus.subscribe(interactive=True) if rt.bus is not None else None
    return rt, provider, sub, ws


# --------------------------------------------------------------------------- schema


def test_every_durable_event_has_record_kind() -> None:
    """The spec's own assertion (§5): `durable` implies a real record kind."""
    for etype, spec in _durability().items():
        if spec["durable"]:
            assert spec["record_kind"] in RECORD_KINDS, etype


def test_transient_events_name_no_record() -> None:
    """`transient` is the streamed set: no record kind, never durable.

    The membership list is the spec's (§3.4), plus WS-03's two hygiene events, whose docstrings already
    said they are live-only — this turns that comment into a checked statement.
    """
    table = _durability()
    transient = sorted(t for t, s in table.items() if s["transient"])
    assert transient == [
        "compaction.started",
        "context.assembled",
        "cost.updated",
        "model.started",
        "model.text.delta",
        "model.thinking.delta",
        "tool.output.delta",
        "tool.repeat_detected",
        "tool.result.pruned",
    ]
    for t in transient:
        assert not table[t]["durable"] and table[t]["record_kind"] is None, t


def test_durable_kinds_are_a_subset_of_the_record_vocabulary() -> None:
    """Nothing claims a kind the SDK does not define, and the mapping is one record kind per event."""
    kinds = {t: s["record_kind"] for t, s in _durability().items() if s["durable"]}
    assert kinds
    assert set(kinds.values()) <= set(RECORD_KINDS)


# --------------------------------------------------------------------------- on the wire


@pytest.mark.asyncio
async def test_durable_events_carry_the_record_the_log_holds(tmp_path: Path) -> None:
    rt, provider, sub, _ws = await _boot(
        tmp_path,
        [
            [ToolCallEnd(id="c1", name="view_file", arguments={"file_path": "app.py"}), Done(stop_reason="tool_use")],
            [TextDelta(text="Here it is."), Done(stop_reason="end_turn")],
        ],
    )
    try:
        session = await rt.session_manager.create_session()
        await rt.runner.execute_turn(
            session=session.info, prompt="what is in app.py", options={"provider": "scripted", "model": MODEL.id}
        )
        events = _drain(sub)
        log: TranscriptLog = session.log
        records = log.records
        by_step = {r.get("step_index"): r for r in records}
        table = _durability()

        carried = [e for e in events if e.get("record") is not None]
        assert {e["type"] for e in carried} >= {"turn.started", "turn.completed"}, sorted(e["type"] for e in carried)
        for e in carried:
            spec = table[e["type"]]
            assert spec["durable"], f"{e['type']} carried a record but is not durable"
            assert e["record"]["kind"] == spec["record_kind"], e["type"]
            logged = by_step.get(e["record"]["step_index"])
            assert logged is not None, f"{e['type']} points at a step the log does not have"
            # Same dict, not a re-description: the event carries what was stored, so there is no second
            # copy of the fact for the client to reconcile.
            assert logged == e["record"] or logged.get("content") == e["record"].get("content")
    finally:
        if sub is not None:
            rt.bus.unsubscribe(sub)
        await rt.registry.unload_all()


@pytest.mark.asyncio
async def test_transient_events_never_carry_a_record(tmp_path: Path) -> None:
    rt, provider, sub, _ws = await _boot(tmp_path, [[TextDelta(text="Only an answer."), Done(stop_reason="end_turn")]])
    try:
        session = await rt.session_manager.create_session()
        await rt.runner.execute_turn(
            session=session.info, prompt="hi", options={"provider": "scripted", "model": MODEL.id}
        )
        table = _durability()
        seen = _drain(sub)
        streamed = [e for e in seen if table.get(e["type"], {}).get("transient")]
        assert streamed, "the transient set should be observable in a plain answer turn"
        assert not [e for e in streamed if e.get("record") is not None], [e["type"] for e in streamed if e.get("record")]
    finally:
        if sub is not None:
            rt.bus.unsubscribe(sub)
        await rt.registry.unload_all()


# --------------------------------------------------------------------------- markers stay out of prompts


@pytest.mark.asyncio
async def test_turn_markers_are_logged_but_never_reach_the_model(tmp_path: Path) -> None:
    rt, provider, _sub, _ws = await _boot(
        tmp_path,
        [
            [ToolCallEnd(id="c1", name="view_file", arguments={"file_path": "app.py"}), Done(stop_reason="tool_use")],
            [TextDelta(text="Done."), Done(stop_reason="end_turn")],
        ],
    )
    try:
        session = await rt.session_manager.create_session()
        await rt.runner.execute_turn(
            session=session.info, prompt="read it", options={"provider": "scripted", "model": MODEL.id}
        )
    finally:
        await rt.registry.unload_all()

    kinds = [r["kind"] for r in session.log.records]
    assert kinds.count("turn.completed") == 1, kinds
    marker_text = [r for r in session.log.records if r["kind"] == "turn.completed"][0]["content"]
    assert "Turn completed (end_turn)" in marker_text

    sent = json.dumps(
        [
            {"role": m.role.value, "content": [getattr(b, "text", str(b)) for b in m.content]}
            for req in provider.requests
            for m in req.messages
        ]
    )
    assert marker_text not in sent, "a turn marker leaked into a model request"


# --------------------------------------------------------------------------- dangling calls are closed on disk


@pytest.mark.asyncio
async def test_unanswered_tool_call_is_closed_in_the_log(tmp_path: Path) -> None:
    """A crash between the call and the result leaves a hole; the next turn fills it *in the file*.

    Repairing only in memory is what made each reader invent its own answer: the provider needs a result
    for every `tool_use` or it 400s, and the transcript would go on saying nothing about the gap.
    """
    rt, provider, _sub, _ws = await _boot(tmp_path, [[TextDelta(text="Recovered."), Done(stop_reason="end_turn")]])
    try:
        session = await rt.session_manager.create_session()
        log = session.log
        await log.append(
            {
                "v": 2,
                "kind": "assistant.message",
                "role": "assistant",
                "content": "Let me read that.",
                "tool_calls": [{"id": "deadbeef", "name": "view_file", "arguments": {"file_path": "app.py"}}],
            }
        )
        assert [d["call_id"] for d in log.dangling_tool_calls()] == ["deadbeef"]

        await rt.runner.execute_turn(
            session=session.info, prompt="and now?", options={"provider": "scripted", "model": MODEL.id}
        )

        repairs = [r for r in log.records if r.get("metadata", {}).get("synthetic")]
        assert len(repairs) == 1, log.records
        assert repairs[0]["kind"] == "tool.result"
        assert repairs[0]["tool_results"][0]["tool_use_id"] == "deadbeef"
        assert repairs[0]["tool_results"][0]["is_error"] is True
        assert not log.dangling_tool_calls(), "the repair must be durable, not per-request"

        # The provider saw a result for the id it left hanging, so the conversation is sendable again.
        blocks = [b for m in provider.requests[0].messages for b in m.content]
        assert any(getattr(b, "tool_use_id", None) == "deadbeef" for b in blocks), blocks
    finally:
        await rt.registry.unload_all()


@pytest.mark.asyncio
async def test_a_clean_log_is_left_alone(tmp_path: Path) -> None:
    rt, _provider, _sub, _ws = await _boot(tmp_path, [[TextDelta(text="fine"), Done(stop_reason="end_turn")]])
    try:
        session = await rt.session_manager.create_session()
        before = len(session.log.records)
        assert await session.log.repair_dangling_tool_calls() == []
        assert len(session.log.records) == before, "repair must not fabricate records"
    finally:
        await rt.registry.unload_all()
