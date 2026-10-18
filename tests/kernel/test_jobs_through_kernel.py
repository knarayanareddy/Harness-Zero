"""WS-10a: jobs started through the gated tool surface persist output and spill safely."""
from __future__ import annotations

import asyncio
import json
import os
from pathlib import Path
from types import SimpleNamespace

import pytest

from harness.kernel.bootstrap import bootstrap_kernel
from harness.plugins.tools.jobs.registry import JobRegistry, _open_regular_file
from harness.sdk.testing.scripted import ScriptedProvider
from harness.sdk.types import (
    Done,
    ExecRequest,
    ExecResult,
    ModelInfo,
    TextDelta,
    ToolCall,
    ToolCallEnd,
)

MODEL = ModelInfo(
    id="ws10a-jobs-test",
    display_name="WS-10a Jobs Test",
    context_tokens=128_000,
    input_usd_per_mtok=0,
    output_usd_per_mtok=0,
)


class _ControlledProcess:
    def __init__(self) -> None:
        self.id = "controlled-job"
        self.pid = 1234
        self.exit_code: int | None = None
        self._output: asyncio.Queue[tuple[str, str] | None] = asyncio.Queue()
        self._finished = asyncio.Event()

    async def output(self):
        while True:
            chunk = await self._output.get()
            if chunk is None:
                return
            yield chunk

    async def wait(self) -> ExecResult:
        await self._finished.wait()
        return ExecResult(
            exit_code=self.exit_code,
            stdout="durable job completed\n",
            stderr="",
            truncated=False,
            duration_ms=25,
        )

    async def kill(self, grace_s: float = 2.0) -> None:
        if not self._finished.is_set():
            self.exit_code = -15
            self._output.put_nowait(None)
            self._finished.set()

    async def finish(self) -> None:
        self.exit_code = 0
        self._output.put_nowait(("stdout", "durable job completed\n"))
        self._output.put_nowait(None)
        self._finished.set()


class _ControlledExecutor:
    name = "local"
    isolation = "test"

    def __init__(self) -> None:
        self.process: _ControlledProcess | None = None

    async def start(self, _request: ExecRequest) -> _ControlledProcess:
        self.process = _ControlledProcess()
        return self.process


@pytest.mark.skipif(not hasattr(os, "mkfifo"), reason="FIFO safety checks require Unix named pipes")
@pytest.mark.parametrize("special_file", ["index", "spill"])
def test_jobs_snapshot_loader_rejects_special_files_without_blocking(
    tmp_path: Path, special_file: str
) -> None:
    workspace = tmp_path / "workspace"
    jobs_dir = workspace / ".harness" / "sessions" / "session_123456789abc" / "jobs"
    jobs_dir.mkdir(parents=True)
    index = jobs_dir / "index.jsonl"
    spill = jobs_dir / "job_123456789abc.log"

    if special_file == "index":
        os.mkfifo(index)
        target = index
    else:
        snapshot = {
            "job_id": "job_123456789abc",
            "state": "running",
            "label": "fifo-test",
            "command": "never-run",
            "cwd": str(workspace),
            "started_at": 1.0,
            "output_bytes": 0,
            "spill_base_offset": 0,
        }
        index.write_text(json.dumps(snapshot) + "\n", encoding="utf-8")
        os.mkfifo(spill)
        target = spill

    with pytest.raises(ValueError, match="non-regular job data file"):
        _open_regular_file(target, os.O_RDONLY)

    registry = JobRegistry(SimpleNamespace(workspace=workspace, settings={}))
    assert registry.list("session_123456789abc") == []


@pytest.mark.skipif(not hasattr(os, "link"), reason="Hard-link safety checks require hard-link support")
def test_jobs_data_files_reject_hardlinks(tmp_path: Path) -> None:
    workspace = tmp_path / "workspace"
    jobs_dir = workspace / ".harness" / "sessions" / "session_123456789abc" / "jobs"
    jobs_dir.mkdir(parents=True)
    outside_file = tmp_path / "sensitive.txt"
    outside_file.write_text("do not append job data here", encoding="utf-8")
    linked_index = jobs_dir / "index.jsonl"
    os.link(outside_file, linked_index)

    with pytest.raises(ValueError, match="multiply-linked job data file"):
        _open_regular_file(linked_index, os.O_WRONLY | os.O_APPEND)

    registry = JobRegistry(SimpleNamespace(workspace=workspace, settings={}))
    assert registry.list("session_123456789abc") == []


@pytest.mark.asyncio
async def test_job_start_is_detached_and_noticed_on_the_following_turn(tmp_path: Path) -> None:
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    config = tmp_path / "config"
    config.mkdir()
    runtime = await bootstrap_kernel(
        workspace=workspace,
        config_dir=config,
        overrides={
            "kernel.security.permission_mode": "bypass",
            "kernel.sandbox.mode": "off",
        },
        resume_session=False,
    )
    session = await runtime.session_manager.create_session(session_id="session_999999999999")
    executor = _ControlledExecutor()
    for name in ("default", "local"):
        if name in runtime.registry.executors:
            runtime.registry.executors[name] = (executor, "test_ws10a")
    provider = ScriptedProvider(
        responses=[
            [
                ToolCallEnd(
                    id="call_durable_job_start",
                    name="job_start",
                    arguments={"command": "long-running-test-command", "label": "controlled-job"},
                ),
                Done(stop_reason="tool_use"),
            ],
            [TextDelta(text="The job is running."), Done(stop_reason="end_turn")],
            [TextDelta(text="The job has finished."), Done(stop_reason="end_turn")],
        ],
        models=[MODEL],
        strict_exhaustion=True,
    )
    runtime.registry.register_provider(provider, plugin_name="test_ws10a")
    try:
        first_turn = await asyncio.wait_for(
            runtime.commands.submit_turn(
                session_id=session.session_id,
                prompt="Start a durable background job.",
                options={"provider": "scripted", "model": MODEL.id},
            ),
            timeout=3,
        )
        assert first_turn.stop_reason == "end_turn"
        assert executor.process is not None
        jobs = runtime.registry.get_service("jobs")
        assert jobs is not None
        job = jobs.list(session.session_id)[0]
        job_id = job["job_id"]
        assert job["running"] is True
        assert jobs.has_pending(session.session_id) is False

        active_job = jobs._jobs[(session.session_id, job_id)]
        assert active_job.monitor_task is not None
        await executor.process.finish()
        await asyncio.wait_for(asyncio.shield(active_job.monitor_task), timeout=2)
        assert jobs.get(session.session_id, job_id)["state"] == "exited"
        assert jobs.get(session.session_id, job_id)["reported"] is False

        second_turn = await runtime.commands.submit_turn(
            session_id=session.session_id,
            prompt="What happened to the background job?",
            options={"provider": "scripted", "model": MODEL.id},
        )
        assert second_turn.stop_reason == "end_turn"
        assert provider.remaining == 0
        request_text = "".join(message.text() for message in provider.requests[-1].messages)
        assert "Jobs finished since your last turn" in request_text
        assert "durable job completed" in request_text
        records = await session.get_history()
        notice = next(record for record in records if record["kind"] == "system.notice")
        assert notice["metadata"]["model_visible"] is True
        assert notice["metadata"]["job_ids"] == [job_id]
        assert jobs.get(session.session_id, job_id)["reported"] is True
    finally:
        await runtime.registry.unload_all()


@pytest.mark.asyncio
async def test_job_output_tail_and_spill(tmp_path: Path) -> None:
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    config = tmp_path / "config"
    config.mkdir()
    runtime = await bootstrap_kernel(
        workspace=workspace,
        config_dir=config,
        overrides={
            "kernel.security.permission_mode": "bypass",
            "plugins.tools-jobs.ring_bytes": 128,
            "plugins.tools-jobs.spill_max_bytes": 4096,
            "kernel.sandbox.mode": "off",
        },
        resume_session=False,
    )
    try:
        session = await runtime.session_manager.create_session(session_id="session_aaaaaaaaaaaa")
        started = await runtime.gate.execute(
            ToolCall(
                id="call_job_start",
                name="job_start",
                arguments={
                    "command": "python -c \"import sys; sys.stdout.write('A' * 5000 + '\\\\nFINAL_LINE\\\\n')\"",
                    "cwd": str(workspace),
                    "label": "large-output",
                },
            ),
            session_id=session.session_id,
            turn_id="turn_ws10a_test",
            log=session.log,
        )
        assert not started.is_error, started.output
        job_id = started.metadata["job_id"]
        jobs = runtime.registry.get_service("jobs")
        assert jobs is not None
        await jobs.wait_task(session.session_id, job_id, timeout_s=10)
        summary = jobs.get(session.session_id, job_id)
        assert summary is not None
        assert len(summary["tail"].encode("utf-8")) <= 128

        tailed = await runtime.gate.execute(
            ToolCall(
                id="call_job_output_tail",
                name="job_output",
                arguments={"job_id": job_id, "tail_lines": 2},
            ),
            session_id=session.session_id,
            turn_id="turn_ws10a_test",
            log=session.log,
        )
        assert not tailed.is_error, tailed.output
        assert "FINAL_LINE" in tailed.output
        assert tailed.metadata["truncated"] is True
        assert tailed.metadata["total_bytes"] > 4096

        records = [record for record in await session.get_history() if record["kind"].startswith("job.")]
        kinds = {record["kind"] for record in records}
        assert {"job.started", "job.output", "job.exited"} <= kinds
        assert all(record.get("metadata", {}).get("job_id") == job_id for record in records)

        spill = workspace / ".harness" / "sessions" / session.session_id / "jobs" / f"{job_id}.log"
        assert spill.is_file()
        assert spill.stat().st_size <= 4096
        assert b"FINAL_LINE" in spill.read_bytes()
    finally:
        await runtime.registry.unload_all()


@pytest.mark.asyncio
async def test_job_list_kill_and_manage_task_alias(tmp_path: Path) -> None:
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    config = tmp_path / "config"
    config.mkdir()
    runtime = await bootstrap_kernel(
        workspace=workspace,
        config_dir=config,
        overrides={
            "kernel.security.permission_mode": "bypass",
            "kernel.sandbox.mode": "off",
        },
        resume_session=False,
    )
    try:
        session = await runtime.session_manager.create_session(session_id="session_cccccccccccc")
        started = await runtime.gate.execute(
            ToolCall(
                id="call_job_start_long",
                name="job_start",
                arguments={
                    "command": "python -c \"import time; time.sleep(30)\"",
                    "cwd": str(workspace),
                    "label": "long-lived-test",
                },
            ),
            session_id=session.session_id,
            turn_id="turn_ws10a_kill",
            log=session.log,
        )
        assert not started.is_error, started.output
        job_id = started.metadata["job_id"]

        listed = await runtime.gate.execute(
            ToolCall(id="call_job_list", name="job_list", arguments={}),
            session_id=session.session_id,
            turn_id="turn_ws10a_kill",
            log=session.log,
        )
        assert not listed.is_error and job_id in listed.output
        other_session = await runtime.session_manager.create_session(session_id="session_eeeeeeeeeeee")
        other_listed = await runtime.gate.execute(
            ToolCall(id="call_job_list_other_session", name="job_list", arguments={}),
            session_id=other_session.session_id,
            turn_id="turn_ws10a_kill",
            log=other_session.log,
        )
        assert not other_listed.is_error and job_id not in other_listed.output

        alias = await runtime.gate.execute(
            ToolCall(id="call_manage_task_list", name="manage_task", arguments={"action": "list"}),
            session_id=session.session_id,
            turn_id="turn_ws10a_kill",
            log=session.log,
        )
        assert not alias.is_error
        assert alias.metadata["deprecated_alias"] is True
        assert "Deprecated" in alias.output and job_id in alias.output

        killed = await runtime.gate.execute(
            ToolCall(id="call_job_kill", name="job_kill", arguments={"job_id": job_id}),
            session_id=session.session_id,
            turn_id="turn_ws10a_kill",
            log=session.log,
        )
        assert not killed.is_error, killed.output
        jobs = runtime.registry.get_service("jobs")
        assert jobs is not None
        status = jobs.get(session.session_id, job_id)
        assert status is not None
        assert status["state"] == "exited"
        assert status["reason"] == "killed"
    finally:
        await runtime.registry.unload_all()


@pytest.mark.asyncio
async def test_jobs_panel_kill_honors_blocking_and_normal_approval(tmp_path: Path) -> None:
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    config = tmp_path / "config"
    config.mkdir()
    runtime = await bootstrap_kernel(
        workspace=workspace,
        config_dir=config,
        overrides={
            "kernel.security.permission_mode": "ask",
            "kernel.sandbox.mode": "off",
        },
        resume_session=False,
    )
    session = await runtime.session_manager.create_session(session_id="session_ffffffffffff")
    try:
        jobs = runtime.registry.get_service("jobs")
        assert jobs is not None
        assert runtime.settings.get("kernel.security.destructive_blocking") is True
        executor = runtime.registry.executors["local"][0]
        job = await jobs.start(
            session.session_id,
            turn_id=None,
            executor=executor,
            req=ExecRequest(
                command="python -c \"import time; time.sleep(30)\"",
                cwd=workspace,
                timeout_s=None,
                interactive=True,
            ),
            label="panel-kill-test",
        )

        blocked = await runtime.commands.dispatch(
            "plugins.tools-jobs.kill",
            {"session_id": session.session_id, "job_id": job["job_id"]},
        )
        assert blocked["ok"] is False
        assert "destructive action blocked" in blocked["reason"]
        assert jobs.get(session.session_id, job["job_id"])["state"] == "running"

        runtime.settings.set("kernel.security.destructive_blocking", False)
        subscriber = runtime.bus.subscribe(interactive=True, session_id=session.session_id)
        try:
            kill_call = asyncio.create_task(
                runtime.commands.dispatch(
                    "plugins.tools-jobs.kill",
                    {"session_id": session.session_id, "job_id": job["job_id"]},
                )
            )
            requested = await asyncio.wait_for(subscriber.get(), timeout=2)
            while requested.type != "approval.required":
                requested = await asyncio.wait_for(subscriber.get(), timeout=2)
            assert requested.data["kind"] == "job.kill"
            resolution = await runtime.commands.resolve_approval(
                approval_id=requested.data["approval_id"],
                approved=True,
                by="ws10a-test",
                reason="Jobs panel kill approval integration test",
            )
            assert resolution["status"] == "resolved"
            result = await asyncio.wait_for(kill_call, timeout=5)
            assert result["ok"] is True
            assert jobs.get(session.session_id, job["job_id"])["reason"] == "killed"
        finally:
            runtime.bus.unsubscribe(subscriber)
    finally:
        await runtime.registry.unload_all()


@pytest.mark.asyncio
async def test_job_start_waits_for_normal_approval(tmp_path: Path) -> None:
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    config = tmp_path / "config"
    config.mkdir()
    runtime = await bootstrap_kernel(
        workspace=workspace,
        config_dir=config,
        overrides={
            "kernel.security.permission_mode": "ask",
            "kernel.sandbox.mode": "off",
        },
        resume_session=False,
    )
    session = await runtime.session_manager.create_session(session_id="session_dddddddddddd")
    subscriber = runtime.bus.subscribe(interactive=True, session_id=session.session_id)
    try:
        start_call = asyncio.create_task(
            runtime.gate.execute(
                ToolCall(
                    id="call_job_start_approval",
                    name="job_start",
                    arguments={"command": "python -c \"print('approved-job')\"", "cwd": str(workspace)},
                ),
                session_id=session.session_id,
                turn_id="turn_ws10a_approval",
                log=session.log,
            )
        )
        requested = await asyncio.wait_for(subscriber.get(), timeout=2)
        while requested.type != "approval.required":
            requested = await asyncio.wait_for(subscriber.get(), timeout=2)
        jobs = runtime.registry.get_service("jobs")
        assert jobs is not None
        assert jobs.list(session.session_id) == []

        resolution = await runtime.commands.resolve_approval(
            approval_id=requested.data["approval_id"],
            approved=True,
            by="ws10a-test",
            reason="approval gate integration test",
        )
        assert resolution["status"] == "resolved"
        result = await asyncio.wait_for(start_call, timeout=5)
        assert not result.is_error, result.output
        assert await jobs.wait_task(session.session_id, result.metadata["job_id"], timeout_s=5) == 0
        records = await session.get_history()
        assert any(record["kind"] == "approval.requested" for record in records)
        assert any(record["kind"] == "job.started" for record in records)
    finally:
        runtime.bus.unsubscribe(subscriber)
        await runtime.registry.unload_all()
