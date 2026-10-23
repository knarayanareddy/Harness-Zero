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

import asyncio
import json
from pathlib import Path
from typing import Any

import pytest

from harness.kernel.bootstrap import bootstrap_kernel
from harness.kernel.transcript import TranscriptLog
from harness.sdk.testing.scripted import ScriptedProvider
from harness.sdk.types import (
    RECORD_KINDS,
    Done,
    ModelInfo,
    TextDelta,
    ToolAnnotations,
    ToolCallEnd,
    ToolResult,
    ToolSpec,
)

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


async def _boot(tmp_path: Path, responses: list[list[Any]], *, mode: str = "allow"):
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
            "kernel.security.permission_mode": mode,
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
        "mcp.server.status",
        "model.started",
        "model.text.delta",
        "model.thinking.delta",
        "model.tool_call",
        "tool.call",
        "tool.output.delta",
        "tool.repeat_detected",
        "tool.result.pruned",
    ]
    for t in transient:
        assert not table[t]["durable"] and table[t]["record_kind"] is None, t


def test_events_that_carry_a_record_are_the_durable_ones(tmp_path: Path) -> None:
    """`carries_record` is not decoration: exactly the events that write a record attach one.

    The exception named in the schema (`tool.result`) pairs by id instead —
    `tool.result` because a strategy batches a step's results into one record, which is the shape a
    provider requires. That pairing is asserted in `test_tool_result_events_pair_with_the_log`.
    """
    table = _durability()
    carrying = {t for t, s in table.items() if s["durable"] and s["carries_record"]}
    assert "turn.started" in carrying and "turn.completed" in carrying
    assert {t for t, s in table.items() if not s["durable"]} >= {"plugin.loaded", "session.started", "settings.changed"}


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


@pytest.mark.asyncio
async def test_every_dangling_call_is_closed_durably_and_returned(tmp_path: Path) -> None:
    rt, _provider, sub, _ws = await _boot(tmp_path, [[Done(stop_reason="end_turn")]])
    try:
        session = await rt.session_manager.create_session()
        log = session.log
        await log.append({"v": 2, "kind": "assistant.message", "role": "assistant", "tool_calls": [
            {"id": "c1", "name": "view_file", "arguments": {"file_path": "app.py"}},
            {"id": "c2", "name": "view_file", "arguments": {"file_path": "app.py"}},
        ]})
        repaired = await log.repair_dangling_tool_calls()
        assert [r["metadata"]["call_id"] for r in repaired] == ["c1", "c2"]
        assert all(r["metadata"]["synthetic"] and r["tool_results"][0]["is_error"] for r in repaired)
        assert log.dangling_tool_calls() == []
        assert await log.repair_dangling_tool_calls() == []
        stored = await log.read(compact=False)
        assert [r for r in stored if r.get("metadata", {}).get("synthetic")] == repaired
    finally:
        if sub is not None:
            rt.bus.unsubscribe(sub)
        await rt.registry.unload_all()


# --------------------------------------------------------------------------- the pairings and the new facts


@pytest.mark.asyncio
async def test_repair_restart_between_individual_results_is_idempotent(tmp_path: Path) -> None:
    from harness.kernel.transcript import TranscriptAppendError, TranscriptLog
    from harness.plugins.stores.jsonl_transcript.store import JsonlTranscriptStore

    store = JsonlTranscriptStore(tmp_path)
    log = TranscriptLog(store, session_id="restart")
    await log.append({"role": "assistant", "tool_calls": [
        {"id": "first", "name": "never_execute"}, {"id": "second", "name": "never_execute"},
    ]})

    class InterruptedStore:
        async def append(self, data):
            if data.get("metadata", {}).get("call_id") == "second":
                raise OSError("synthetic crash before second repair")
            return await store.append(data)

    log._store = InterruptedStore()
    with pytest.raises(TranscriptAppendError):
        await log.repair_dangling_tool_calls()
    persisted = await store.read(compact=False)
    assert [r["metadata"]["call_id"] for r in persisted if r.get("metadata", {}).get("synthetic")] == ["first"]
    restarted = TranscriptLog(JsonlTranscriptStore(tmp_path), session_id="restart")
    await restarted.load()
    repairs = await restarted.repair_dangling_tool_calls()
    assert [r["metadata"]["call_id"] for r in repairs] == ["second"]
    assert await restarted.repair_dangling_tool_calls() == []
    for compact in (False, True):
        rows = await restarted.read(compact=compact)
        synthetic = [r for r in rows if r.get("metadata", {}).get("synthetic")]
        assert [r["metadata"]["call_id"] for r in synthetic] == ["first", "second"]
        assert all(r["tool_results"][0]["is_error"] for r in synthetic)


@pytest.mark.asyncio
async def test_repair_returns_own_receipts_with_interleaved_notice(tmp_path: Path, monkeypatch) -> None:
    from harness.kernel.transcript import TranscriptLog
    from harness.plugins.stores.jsonl_transcript.store import JsonlTranscriptStore

    log = TranscriptLog(JsonlTranscriptStore(tmp_path))
    await log.append({"role": "assistant", "tool_calls": [{"id": "c1", "name": "never_execute"}]})
    append = log.append

    async def interleaved(record):
        step = await append(record)
        await append({"role": "system", "kind": "system.notice", "content": "later notice"})
        return step

    monkeypatch.setattr(log, "append", interleaved)
    repairs = await log.repair_dangling_tool_calls()
    assert len(repairs) == 1
    assert repairs[0]["step_index"] == 2
    assert repairs[0]["metadata"]["call_id"] == "c1"
    assert repairs[0]["kind"] == "tool.result"


@pytest.mark.asyncio
async def test_tool_result_events_pair_with_the_log(tmp_path: Path) -> None:
    """`tool.result` names a record kind but carries no record — so the ids have to join up.

    The schema says `carries_record: false` for this event because a strategy writes one batched record per
    step (the shape a provider requires) while the gate publishes one preview per call. Pairing by
    `call_id` / `tool_use_id` is therefore the contract, and this is where it is checked: a client that
    re-renders a reloaded session must be able to attach every preview to its record.
    """
    rt, _provider, sub, _ws = await _boot(
        tmp_path,
        [
            [ToolCallEnd(id="c1", name="view_file", arguments={"file_path": "app.py"}), Done(stop_reason="tool_use")],
            [TextDelta(text="done"), Done(stop_reason="end_turn")],
        ],
    )
    try:
        session = await rt.session_manager.create_session()
        await rt.runner.execute_turn(
            session=session.info, prompt="read it", options={"provider": "scripted", "model": MODEL.id}
        )
        events = _drain(sub)
        results = [e for e in events if e["type"] == "tool.result"]
        assert results, "the scripted flow should have produced a tool result"
        recorded_ids = {
            r["tool_results"][0]["tool_use_id"]
            for r in session.log.records
            if r["kind"] == "tool.result" and r.get("tool_results")
        }
        for e in results:
            assert e["data"]["call_id"] in recorded_ids, e["data"]
            assert "record" not in e or e.get("record") is None, "this event pairs by id, not by pointer"
    finally:
        if sub is not None:
            rt.bus.unsubscribe(sub)
        await rt.registry.unload_all()


@pytest.mark.asyncio
async def test_the_gate_records_what_it_decided_and_what_it_rendered(tmp_path: Path) -> None:
    """Decisions and cards used to be events only — the two facts a reloaded session could not show.

    A tool that returns a card, driven through the real gate with the session log attached: the log gains a
    `tool.decision` and a `ui.card` record, the live events carry those stored records, and neither reaches
    a model request (so adding history did not change what the model reads — `requests.json` in the
    scenario goldens is unchanged for exactly this reason).
    """
    rt, provider, sub, ws = await _boot(
        tmp_path,
        [
            [ToolCallEnd(id="c9", name="make_card", arguments={}), Done(stop_reason="tool_use")],
            [TextDelta(text="shown"), Done(stop_reason="end_turn")],
        ],
    )

    async def handler(args: Any, ctx: Any) -> ToolResult:
        from harness.sdk.types import TextBlock

        return ToolResult(
            content=[TextBlock(text="card made")],
            cards=[{"kind": "checklist", "title": "Two items", "fields": [{"label": "a", "value": "1"}]}],
        )

    rt.registry._register_tool(
        ToolSpec(name="make_card", description="Emits a card", input_schema={"type": "object"},
                 annotations=ToolAnnotations()),
        handler,
        "test_cards",
    )
    try:
        session = await rt.session_manager.create_session()
        await rt.runner.execute_turn(
            session=session.info, prompt="make a card", options={"provider": "scripted", "model": MODEL.id}
        )
        kinds = [r["kind"] for r in session.log.records]
        assert "tool.decision" in kinds, kinds
        assert "ui.card" in kinds, kinds
        card = [r for r in session.log.records if r["kind"] == "ui.card"][0]
        assert card["metadata"]["card"]["title"] == "Two items"
        assert card["metadata"]["call_id"] == "c9"

        events = _drain(sub)
        card_events = [e for e in events if e["type"] == "ui.card"]
        assert card_events and card_events[0].get("record") == card
        decision = [e for e in events if e["type"] == "tool.decision"]
        assert decision and decision[0]["record"]["kind"] == "tool.decision"

        sent = json.dumps([[getattr(b, "text", str(b)) for b in m.content] for req in provider.requests for m in req.messages])
        assert "Two items" not in sent and "allowed" not in sent, "a recorded fact leaked into the prompt"

        # ... and a reload through the public command sees the same records, not a thinner copy.
        fetched = await rt.commands.dispatch("sessions.get", {"session_id": session.info.session_id})
        assert [r["kind"] for r in fetched["records"]] == kinds
    finally:
        if sub is not None:
            rt.bus.unsubscribe(sub)
        await rt.registry.unload_all()


@pytest.mark.asyncio
async def test_approval_facts_are_written_to_the_session_log(tmp_path: Path) -> None:
    """`approval.required` / `approval.resolved` become records, including the timeout path.

    The broker is kernel-wide and logs are per session, so it resolves the log per call — this test drives
    it directly, with a real `TranscriptLog` behind it, to pin both halves of the record.
    """
    from harness.kernel.approvals import ApprovalBroker
    from harness.kernel.bus import EventBus
    from harness.kernel.transcript import TranscriptLog

    log = TranscriptLog(None, session_id="s1")  # no store: the in-memory log is enough for this claim
    bus = EventBus()
    bus.subscribe(interactive=True)  # an interactive subscriber, or `ask` denies without waiting
    broker = ApprovalBroker(bus=bus, timeout_s=5.0, log_for=lambda sid: log if sid == "s1" else None)

    async def approve() -> None:
        while not broker.pending():
            await asyncio.sleep(0.01)
        await broker.resolve_recorded(broker.pending()[0]["approval_id"], True, by="tester", session_id="s1")

    task = asyncio.create_task(broker.ask("tool_execution", "Run `rm -rf build`?", {"risk": "destructive"}, call_id="c1", session_id="s1"))
    answer = asyncio.create_task(approve())
    approved, reason = await task
    await answer

    assert approved, reason
    kinds = [r["kind"] for r in log.records]
    assert kinds == ["approval.requested", "approval.resolved"], kinds
    assert log.records[0]["metadata"]["call_id"] == "c1"
    assert log.records[1]["metadata"]["approved"] is True and log.records[1]["metadata"]["by"] == "tester"
    assert "rm -rf build" in log.records[0]["content"]


@pytest.mark.asyncio
async def test_a_denied_call_is_recorded_even_without_an_approver(tmp_path: Path) -> None:
    """The denial a user never saw is still a fact: `permission_mode: plan` writes the decision record."""
    # The mode has to be in the boot overrides: `settings.set` afterwards cannot win against them, which
    # is exactly why this test boots in plan mode rather than switching into it.
    rt, provider, _sub, _ws = await _boot(
        tmp_path,
        [
            [ToolCallEnd(id="c1", name="write_to_file", arguments={"file_path": "x.txt", "content": "x"}), Done(stop_reason="tool_use")],
            [TextDelta(text="ok then"), Done(stop_reason="end_turn")],
        ],
        mode="plan",
    )
    try:
        session = await rt.session_manager.create_session()
        await rt.runner.execute_turn(
            session=session.info, prompt="write it", options={"provider": "scripted", "model": MODEL.id}
        )
        decisions = [r for r in session.log.records if r["kind"] == "tool.decision"]
        assert decisions and decisions[0]["metadata"]["action"] == "deny", decisions
        assert "denied" in decisions[0]["content"].lower()
    finally:
        await rt.registry.unload_all()
