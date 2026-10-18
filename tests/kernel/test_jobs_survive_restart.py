"""WS-10a: a job left running by a crashed daemon is closed and announced next turn."""
from __future__ import annotations

import json
import time
from pathlib import Path

import pytest

from harness.kernel.bootstrap import bootstrap_kernel
from harness.sdk.testing.scripted import ScriptedProvider
from harness.sdk.types import Done, ModelInfo, TextDelta, TranscriptRecord

SESSION_ID = "session_bbbbbbbbbbbb"
JOB_ID = "job_deadbeef1234"
MODEL = ModelInfo(
    id="ws10a-scripted",
    display_name="WS-10a Scripted",
    context_tokens=128_000,
    input_usd_per_mtok=0,
    output_usd_per_mtok=0,
)


def _write_crashed_session_snapshot(workspace: Path) -> None:
    session_dir = workspace / ".harness" / "sessions" / SESSION_ID
    jobs_dir = session_dir / "jobs"
    jobs_dir.mkdir(parents=True)
    (session_dir / "session.json").write_text(
        json.dumps({"session_id": SESSION_ID, "workspace": str(workspace), "metadata": {}}),
        encoding="utf-8",
    )
    spill = jobs_dir / f"{JOB_ID}.log"
    spill.write_text("pytest collection started\n", encoding="utf-8")

    started_at = time.time() - 42
    snapshot = {
        "job_id": JOB_ID,
        "session_id": SESSION_ID,
        "state": "running",
        "label": "pytest",
        "command": "pytest -q",
        "cwd": str(workspace),
        "pid": 987654,
        "started_at": started_at,
        "output_bytes": spill.stat().st_size,
        "spill_base_offset": 0,
        "reported": False,
        "sandbox": {"mode": "off", "enforcement": "none"},
    }
    (jobs_dir / "index.jsonl").write_text(json.dumps(snapshot) + "\n", encoding="utf-8")

    started_record = TranscriptRecord(
        role="system",
        kind="job.started",
        content="Job pytest started.",
        timestamp=started_at,
        metadata={
            "job_id": JOB_ID,
            "label": "pytest",
            "command": "pytest -q",
            "cwd": str(workspace),
            "pid": 987654,
        },
        step_index=1,
    )
    (session_dir / "transcript.jsonl").write_text(
        json.dumps(started_record.to_dict()) + "\n", encoding="utf-8"
    )


@pytest.mark.asyncio
async def test_lost_job_is_closed_and_reported_next_turn(tmp_path: Path) -> None:
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    config = tmp_path / "config"
    config.mkdir()
    _write_crashed_session_snapshot(workspace)

    runtime = await bootstrap_kernel(
        workspace=workspace,
        config_dir=config,
        overrides={
            "kernel.security.permission_mode": "bypass",
            "kernel.provider": "scripted",
            "kernel.model": MODEL.id,
        },
        resume_session=False,
    )
    try:
        jobs = runtime.registry.get_service("jobs")
        assert jobs is not None
        job = jobs.get(SESSION_ID, JOB_ID)
        assert job is not None
        assert job["state"] == "exited"
        assert job["reason"] == "lost"

        provider = ScriptedProvider(models=[MODEL])
        provider.add_response([TextDelta(text="The interrupted pytest job was reported."), Done(stop_reason="end_turn")])
        runtime.registry.register_provider(provider, plugin_name="test_ws10a")
        session = await runtime.session_manager.switch_session(SESSION_ID)
        result = await runtime.runner.execute_turn(
            session=session.info,
            prompt="Continue after the daemon restarted.",
            options={"provider": "scripted", "model": MODEL.id},
        )
        assert result.stop_reason == "end_turn"

        records = await session.get_history()
        exited = [record for record in records if record["kind"] == "job.exited" and record["metadata"].get("job_id") == JOB_ID]
        assert len(exited) == 1
        assert exited[0]["metadata"]["reason"] == "lost"
        notices = [record for record in records if record["kind"] == "system.notice" and record["metadata"].get("code") == "jobs.finished_since_last_turn"]
        assert len(notices) == 1
        assert notices[0]["metadata"]["model_visible"] is True
        assert "pytest" in notices[0]["content"]
        assert any("pytest" in message.text() and "finished since your last turn" in message.text().lower() for message in provider.requests[0].messages)
    finally:
        await runtime.registry.unload_all()
