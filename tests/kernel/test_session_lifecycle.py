"""Session lifecycle is kernel-owned (consolidation P2, review §4.4).

* A daemon start resumes the most recent session that has recorded turns — never an empty one.
* ``submit_turn`` without a session id creates a session lazily and announces it with ``session.started``.
* Restarting the daemon N times on the same workspace leaves exactly one session.
* ``sessions.list`` rows carry what a picker needs (``SessionRow``): title, turns, updated_at, active.
"""
from __future__ import annotations

import asyncio
from dataclasses import fields
from pathlib import Path

import pytest

from harness.kernel.bootstrap import bootstrap_kernel
from harness.sdk.testing.scripted import ScriptedProvider
from harness.sdk.types import Done, ModelInfo, SessionRow, TextDelta

MODEL = ModelInfo(id="scripted-v1", display_name="Scripted", context_tokens=100_000, input_usd_per_mtok=1.0, output_usd_per_mtok=2.0)


def _provider(*answers: str) -> ScriptedProvider:
    p = ScriptedProvider(models=[MODEL])
    for a in answers:
        p.add_response([TextDelta(text=a), Done(stop_reason="end_turn")])
    return p


async def _collect(bus, types: set[str]):
    sub = bus.subscribe()
    seen = []

    async def pump():
        while True:
            evt = await sub.get()
            if evt.type in types:
                seen.append(evt)

    task = asyncio.create_task(pump())
    return seen, task


@pytest.mark.asyncio
async def test_three_restarts_leave_one_session_and_resume_it(tmp_path: Path):
    ws, conf = tmp_path / "ws", tmp_path / "conf"
    ws.mkdir()
    conf.mkdir()

    # Start 1: fresh workspace -> nothing to resume; the first prompt creates the session lazily.
    rt = await bootstrap_kernel(workspace=ws, config_dir=conf)
    assert rt.session_manager.active_session is None
    rt.registry.register_provider(_provider("Answer one."), plugin_name="t")
    seen, pump = await _collect(rt.bus, {"session.started"})
    res = await rt.commands.submit_turn(prompt="Question one", options={"provider": "scripted", "model": MODEL.id})
    await asyncio.sleep(0.05)
    pump.cancel()
    assert res.output == "Answer one."
    assert len(seen) == 1 and seen[0].data["session_id"] == rt.session_manager.active_session.session_id
    sid = rt.session_manager.active_session.session_id
    await rt.registry.unload_all()

    # Starts 2 and 3: the same session is resumed and no new one appears.
    for _ in range(2):
        rt = await bootstrap_kernel(workspace=ws, config_dir=conf)
        assert rt.session_manager.active_session is not None
        assert rt.session_manager.active_session.session_id == sid
        rows = await rt.commands.sessions_list()
        assert [r["session_id"] for r in rows] == [sid]
        assert rows[0]["active"] is True
        await rt.registry.unload_all()

    # A turn on the resumed session continues the same transcript.
    rt = await bootstrap_kernel(workspace=ws, config_dir=conf)
    rt.registry.register_provider(_provider("Answer two."), plugin_name="t")
    await rt.commands.submit_turn(prompt="Question two", options={"provider": "scripted", "model": MODEL.id})
    rows = await rt.commands.sessions_list()
    assert len(rows) == 1
    assert rows[0]["turns"] == 2 and rows[0]["title"] == "Question one"
    history = (await rt.commands.sessions_get(session_id=sid))["records"]
    assert [r["content"] for r in history if r["role"] == "user"] == ["Question one", "Question two"]
    await rt.registry.unload_all()


@pytest.mark.asyncio
async def test_resume_skips_empty_sessions(tmp_path: Path):
    ws, conf = tmp_path / "ws", tmp_path / "conf"
    ws.mkdir()
    conf.mkdir()
    rt = await bootstrap_kernel(workspace=ws, config_dir=conf)
    rt.registry.register_provider(_provider("Hi."), plugin_name="t")
    used = await rt.session_manager.create_session()
    await rt.commands.submit_turn(session_id=used.session_id, prompt="hello", options={"provider": "scripted", "model": MODEL.id})
    empty = await rt.session_manager.create_session()  # e.g. the "New session" button, never used
    assert rt.session_manager.active_session is empty
    await rt.registry.unload_all()

    rt = await bootstrap_kernel(workspace=ws, config_dir=conf)
    assert rt.session_manager.active_session.session_id == used.session_id
    rows = await rt.commands.sessions_list()
    assert {r["session_id"]: r["turns"] for r in rows} == {used.session_id: 1, empty.session_id: 0}
    assert rows[0]["session_id"] == empty.session_id  # newest activity first (creation counts); resume still skipped it
    await rt.registry.unload_all()

    rt = await bootstrap_kernel(workspace=ws, config_dir=conf, resume_session=False)
    assert rt.session_manager.active_session is None
    await rt.registry.unload_all()


@pytest.mark.asyncio
async def test_sessions_list_rows_are_session_rows(tmp_path: Path):
    ws, conf = tmp_path / "ws", tmp_path / "conf"
    ws.mkdir()
    conf.mkdir()
    rt = await bootstrap_kernel(workspace=ws, config_dir=conf)
    rt.registry.register_provider(_provider("Sure."), plugin_name="t")
    long_prompt = "Please refactor the payment module so that retries are idempotent and logged"
    await rt.commands.submit_turn(prompt=long_prompt, options={"provider": "scripted", "model": MODEL.id})
    rows = await rt.commands.sessions_list()
    assert len(rows) == 1
    row = rows[0]
    assert set(row) == {f.name for f in fields(SessionRow)}
    assert row["title"] == long_prompt[:60] + "…"
    assert row["turns"] == 1 and row["message_count"] == 2
    assert row["updated_at"] >= row["created_at"]
    assert row["active"] is True and row["busy"] is False
    await rt.registry.unload_all()


@pytest.mark.asyncio
async def test_cli_frontend_follows_kernel_session_across_processes(tmp_path: Path):
    """``harness run`` twice on one workspace = one conversation: the CLI no longer mints its own session id.

    Process 1 has no session; the CLI submits without an id, the kernel creates one and the CLI adopts it.
    Process 2 resumes that session at bootstrap; the CLI picks it up and the second prompt sees the first.
    """
    from harness.frontends.cli.app import CliApp

    ws, conf = tmp_path / "ws", tmp_path / "conf"
    ws.mkdir()
    conf.mkdir()
    opts = {"provider": "scripted", "model": MODEL.id}

    rt1 = await bootstrap_kernel(workspace=ws, config_dir=conf)
    rt1.registry.register_provider(_provider("Answer one."), plugin_name="t")
    app1 = CliApp(commands=rt1.commands, bus=rt1.bus, interactive=False)
    assert app1.session_id is None
    await app1.run_turn("Question one", options=opts)
    assert app1.session_id == rt1.session_manager.active_session.session_id
    sid = app1.session_id

    rt2 = await bootstrap_kernel(workspace=ws, config_dir=conf)
    provider2 = _provider("Answer two.")
    rt2.registry.register_provider(provider2, plugin_name="t")
    app2 = CliApp(commands=rt2.commands, bus=rt2.bus, interactive=False)
    await app2.run_turn("Question two", options=opts)
    assert app2.session_id == sid
    # The second turn was built on the resumed transcript: the model saw the first exchange.
    provider2.assert_requested(count=1, contains_text="Question one")
    provider2.assert_requested(contains_text="Answer one.")
    rows = rt2.session_manager.list_sessions()
    assert [r["session_id"] for r in rows] == [sid]
    assert rows[0]["turns"] == 2 and rows[0]["title"] == "Question one"
