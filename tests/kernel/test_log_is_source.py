"""WS-04 §1: the session log is the single source of truth for the conversation.

Two properties, both cheap to state and both false on `main`:

1. *Reconstructibility* — every message list a provider received is exactly `derive_messages(records)`
   over the records that existed at that moment, read back **from disk** in a fresh kernel. If the in-memory
   list and the file ever disagree, this test fails; that is the point, because a reloaded session is
   what the user sees after a crash.
2. *Fail-closed append* — if the log cannot be written, the turn stops. Before this, `store.append`
   failing was a warning and the model kept running, so the conversation continued on a history that
   existed only in memory.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

import pytest

from harness.kernel.bootstrap import bootstrap_kernel
from harness.kernel.context import derive_messages
from harness.kernel.transcript import TranscriptLog
from harness.sdk.testing.scripted import ScriptedProvider
from harness.sdk.types import Done, ModelInfo, TextDelta, ToolCallEnd

MODEL = ModelInfo(
    id="scripted-v1",
    display_name="Scripted",
    context_tokens=128_000,
    input_usd_per_mtok=0.0,
    output_usd_per_mtok=0.0,
)


def _norm_block(b: Any) -> Any:
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
            "role": m.role.value if hasattr(m.role, "value") else str(m.role),
            "blocks": [_norm_block(b) for b in m.content],
        }
        for m in messages
    ]


def _tool(name: str, cid: str, args: dict) -> list:
    return [ToolCallEnd(id=cid, name=name, arguments=args), Done(stop_reason="tool_use")]


@pytest.mark.asyncio
async def test_every_model_request_is_derivable_from_the_log(tmp_path: Path):
    ws = tmp_path / "ws"
    ws.mkdir()
    (ws / "app.py").write_text("print('x')\n", encoding="utf-8")
    conf = tmp_path / "conf"
    conf.mkdir()

    rt = await bootstrap_kernel(
        workspace=ws,
        config_dir=conf,
        overrides={"kernel.security.permission_mode": "allow", "kernel.provider": "scripted", "kernel.model": MODEL.id},
        resume_session=False,
    )
    provider = ScriptedProvider(models=[MODEL])
    provider.add_response(_tool("view_file", "c1", {"file_path": "app.py"}))
    provider.add_response(_tool("write_to_file", "c2", {"file_path": "other.py", "content": "hello\n"}))
    provider.add_response([TextDelta(text="Done."), Done(stop_reason="end_turn")])
    rt.registry.register_provider(provider, plugin_name="test_scripted")
    try:
        session = await rt.session_manager.create_session()
        await rt.runner.execute_turn(
            session=session.info, prompt="read then write", options={"provider": "scripted", "model": MODEL.id}
        )
        session_id = session.info.session_id
        assert len(provider.requests) == 3, "the scripted flow changed"
    finally:
        await rt.registry.unload_all()

    # A fresh kernel over the same workspace/config: no in-memory state is available to it, so anything
    # the requests contain must come out of the file.
    rt2 = await bootstrap_kernel(workspace=ws, config_dir=conf, resume_session=False)
    try:
        log: TranscriptLog = rt2.session_manager.get_session(session_id).log
        records = await log.read(compact=False)
        assert [r["kind"] for r in records] == [
            "user.message",
            "assistant.message",
            # A decision is a record too (WS-04b): the log says *why* the call ran, not only that it did.
            "tool.decision",
            "tool.result",
            "assistant.message",
            "tool.decision",
            "tool.result",
            "assistant.message",
            # The turn ends with a marker record (WS-04 §3.3), so the log alone says how it finished —
            # and `derive_messages` ignores it, so it is not part of any request.
            "turn.completed",
        ], records

        # Request k saw the history as of that moment, which is *every record before the assistant record
        # that request produced*. Naming the boundary in the record stream rather than as arithmetic over
        # records-per-iteration is what let this test keep its meaning when decisions, cards and turn
        # markers joined the log — and it no longer depends on the scripted flow's step size.
        assistant_at = [i for i, r in enumerate(records) if r["kind"] == "assistant.message"]
        assert len(assistant_at) == len(provider.requests), "one assistant record per model request"
        for k, request in enumerate(provider.requests):
            derived = _norm(derive_messages(records[: assistant_at[k]]))
            assert derived == _norm(request.messages), f"request {k} is not derivable from the log:\n{derived}\n{request.messages}"
    finally:
        await rt2.registry.unload_all()


@pytest.mark.asyncio
async def test_sessions_get_returns_upgraded_records(tmp_path: Path):
    ws = tmp_path / "ws"
    ws.mkdir()
    conf = tmp_path / "conf"
    conf.mkdir()
    rt = await bootstrap_kernel(
        workspace=ws,
        config_dir=conf,
        overrides={"kernel.provider": "mock", "kernel.model": "mock"},
        resume_session=False,
    )
    try:
        session = await rt.session_manager.create_session()
        # write a v1 line the way an older harness would have, then read it back through the command
        path = ws / ".harness" / "sessions" / session.info.session_id / "transcript_full.jsonl"
        line = {"role": "user", "content": "legacy prompt", "timestamp": 1.0, "full_offset": 0}
        with open(path, "a", encoding="utf-8") as f:
            import json

            f.write(json.dumps({**line, "step_index": 99}) + "\n")

        got = await rt.commands.sessions_get(session.info.session_id)
        legacy = [r for r in got["records"] if r.get("content") == "legacy prompt"]
        assert legacy and legacy[0]["kind"] == "user.message" and legacy[0]["v"] == 2
        assert legacy[0]["full_offset"] == 0, "the read-time upgrade must not drop store annotations"
    finally:
        await rt.registry.unload_all()


@pytest.mark.asyncio
async def test_a_store_that_cannot_append_stops_the_turn(tmp_path: Path, monkeypatch):
    ws = tmp_path / "ws"
    ws.mkdir()
    conf = tmp_path / "conf"
    conf.mkdir()
    rt = await bootstrap_kernel(
        workspace=ws,
        config_dir=conf,
        overrides={"kernel.provider": "scripted", "kernel.model": MODEL.id},
        resume_session=False,
    )
    provider = ScriptedProvider(models=[MODEL])
    provider.add_response([TextDelta(text="should never be asked"), Done(stop_reason="end_turn")])
    rt.registry.register_provider(provider, plugin_name="test_scripted")
    try:
        session = await rt.session_manager.create_session()
        live = rt.bus.subscribe({"turn.error"}, interactive=False)

        store = rt.session_manager.get_session(session.info.session_id).transcript

        async def broken_append(record: dict[str, Any]) -> int:
            raise OSError(f"disk full (test): refusing to write {record.get('kind')}")

        monkeypatch.setattr(store, "append", broken_append)

        result = await rt.runner.execute_turn(
            session=session.info, prompt="go", options={"provider": "scripted", "model": MODEL.id}
        )

        assert provider.requests == [], "an unlogged prompt must not reach the model"
        assert result.error and "disk full" in result.error
        assert result.stop_reason == "transcript_append_failed"
        errors = []
        while not live.empty():
            errors.append(live.get_nowait())
        kinds = [e.data.get("kind") for e in errors if e.type == "turn.error"]
        assert kinds == ["transcript_append_failed"], kinds
    finally:
        await rt.registry.unload_all()


@pytest.mark.asyncio
async def test_record_is_reloaded_equal_after_a_restart(tmp_path: Path):
    """Live view == reloaded view, at the record level (the UI half of this is WS-04c's scenario)."""
    ws = tmp_path / "ws"
    ws.mkdir()
    (ws / "app.py").write_text("print('x')\n", encoding="utf-8")
    conf = tmp_path / "conf"
    conf.mkdir()
    rt = await bootstrap_kernel(
        workspace=ws,
        config_dir=conf,
        overrides={"kernel.security.permission_mode": "allow", "kernel.provider": "scripted", "kernel.model": MODEL.id},
        resume_session=False,
    )
    provider = ScriptedProvider(models=[MODEL])
    provider.add_response(_tool("view_file", "c1", {"file_path": "app.py"}))
    provider.add_response([TextDelta(text="Read it."), Done(stop_reason="end_turn")])
    rt.registry.register_provider(provider, plugin_name="test_scripted")
    try:
        session = await rt.session_manager.create_session()
        await rt.runner.execute_turn(
            session=session.info, prompt="read app.py", options={"provider": "scripted", "model": MODEL.id}
        )
        live_records = list(rt.session_manager.get_session(session.info.session_id).log.records)
        session_id = session.info.session_id
    finally:
        await rt.registry.unload_all()

    rt2 = await bootstrap_kernel(workspace=ws, config_dir=conf, resume_session=False)
    try:
        reloaded = await rt2.session_manager.get_session(session_id).log.read(compact=False)
        assert reloaded == live_records
        assert _norm(derive_messages(reloaded)) == _norm(derive_messages(live_records))
    finally:
        await rt2.registry.unload_all()


@pytest.mark.asyncio
async def test_the_search_index_is_rebuildable_from_the_log(tmp_path: Path):
    """The FTS5 table is a *derived* index: rebuilding it from the transcript has to reproduce it (WS-04 §7).

    Two bugs this pins. The row projection read v1 field names (`type`, `source`), so every row a live
    session indexed said `UNKNOWN`/`UNKNOWN` and a search hit carried no kind; and the only rebuild path ran
    when the table was *empty*, so a workspace whose index had gone stale could not be repaired at all. Now
    one projection serves both paths, and `harness sessions reindex` rebuilds unconditionally.
    """
    from harness.plugins.stores.fts5_search.index import FTS5SearchIndex
    from harness.sdk.paths import sessions_dir

    ws = tmp_path / "ws"
    ws.mkdir()
    (ws / "app.py").write_text("print('x')\n", encoding="utf-8")
    conf = tmp_path / "conf"
    conf.mkdir()
    rt = await bootstrap_kernel(
        workspace=ws,
        config_dir=conf,
        overrides={"kernel.security.permission_mode": "allow", "kernel.provider": "scripted", "kernel.model": MODEL.id},
        resume_session=False,
    )
    provider = ScriptedProvider(models=[MODEL])
    provider.add_response(_tool("view_file", "c1", {"file_path": "app.py"}))
    provider.add_response([TextDelta(text="Read it."), Done(stop_reason="end_turn")])
    rt.registry.register_provider(provider, plugin_name="test_scripted")
    try:
        session = await rt.session_manager.create_session()
        await rt.runner.execute_turn(
            session=session.info, prompt="read app.py", options={"provider": "scripted", "model": MODEL.id}
        )
        session_id = session.info.session_id
        live_records = list(rt.session_manager.get_session(session_id).log.records)
    finally:
        await rt.registry.unload_all()

    searchable = [r for r in live_records if str(r.get("content") or "").strip()]
    assert searchable, "a turn that ran left nothing searchable in the log"

    # The live path and the rebuild walk must agree row for row.
    live_db, rebuilt_db = tmp_path / "live.db", tmp_path / "rebuilt.db"
    live = FTS5SearchIndex(live_db)
    try:
        for rec in live_records:
            await live.index(session_id, rec)
        rows_live = sorted(live.conn.execute("SELECT step_index, source, step_type, content FROM turns_fts").fetchall())
    finally:
        live.close()

    rebuilt = FTS5SearchIndex(rebuilt_db)
    try:
        found, rows = rebuilt.rebuild_from_sessions(sessions_dir(ws))
        assert found >= 1 and rows == len(searchable), f"indexed {rows} of {len(searchable)} searchable records"
        rows_back = sorted(
            rebuilt.conn.execute("SELECT step_index, source, step_type, content FROM turns_fts").fetchall()
        )
        assert rows_back == rows_live, "a reindexed workspace differs from a session indexed while it ran"
        kinds = {r[2] for r in rows_back}
        assert "UNKNOWN" not in kinds, kinds
        assert {"user.message", "assistant.message"} <= kinds, kinds

        # A rebuild repairs staleness rather than only filling an empty table.
        rebuilt.conn.execute(
            "INSERT INTO turns_fts (conversation_id, step_index, source, step_type, content)"
            " VALUES ('ghost', 0, 'user', 'user.message', 'a row the log does not contain')"
        )
        rebuilt.conn.commit()
        _, rows_after = rebuilt.rebuild_from_sessions(sessions_dir(ws))
        assert rebuilt.conn.execute("SELECT count(*) FROM turns_fts WHERE conversation_id='ghost'").fetchone()[0] == 0
        assert rows_after == rows

        from click.testing import CliRunner

        from harness.cli import main

        result = CliRunner().invoke(main, ["sessions", "reindex", "--workspace", str(ws), "--dry-run"])
        assert result.exit_code == 0, result.output
        assert "would re-index" in result.output and str(len(searchable)) in result.output
    finally:
        rebuilt.close()
