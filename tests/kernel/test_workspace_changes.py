"""WS-07: checkpoint summaries are durable across normal and exceptional turn endings."""
from __future__ import annotations

from pathlib import Path
from typing import Any

import pytest

from harness.kernel.bootstrap import bootstrap_kernel
from harness.plugins.hooks.checkpoints.shadow_repo import ShadowRepo
from harness.sdk.errors import ProviderError
from harness.sdk.testing.scripted import ScriptedProvider
from harness.sdk.types import Done, HookEvent, HookOutcome, ModelInfo, TextDelta, ToolCallEnd

MODEL = ModelInfo(
    id="ws07-scripted",
    display_name="WS-07 Scripted",
    context_tokens=128_000,
    input_usd_per_mtok=0,
    output_usd_per_mtok=0,
)


async def _boot(
    tmp_path: Path,
    responses: list[list[Any] | Exception],
    *,
    provider: ScriptedProvider | None = None,
):
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    (workspace / "initial.txt").write_text("unchanged\n", encoding="utf-8")
    config = tmp_path / "config"
    config.mkdir()
    runtime = await bootstrap_kernel(
        workspace=workspace,
        config_dir=config,
        overrides={
            "kernel.security.permission_mode": "allow",
            "kernel.security.destructive_blocking": False,
            "kernel.provider": "scripted",
            "kernel.model": MODEL.id,
        },
        resume_session=False,
    )
    provider = provider or ScriptedProvider(models=[MODEL])
    for response in responses:
        if isinstance(response, Exception):
            provider.add_error(response)
        else:
            provider.add_response(response)
    runtime.registry.register_provider(provider, plugin_name="test_ws07")
    return runtime, workspace


@pytest.mark.asyncio
async def test_two_edits_emit_one_changes_record_and_survive_reload(tmp_path: Path) -> None:
    runtime, workspace = await _boot(
        tmp_path,
        [
            [ToolCallEnd(id="c1", name="write_to_file", arguments={"file_path": "one.txt", "content": "one\n"}), Done(stop_reason="tool_use")],
            [ToolCallEnd(id="c2", name="write_to_file", arguments={"file_path": "two.txt", "content": "two\n"}), Done(stop_reason="tool_use")],
            [TextDelta(text="Both files are ready."), Done(stop_reason="end_turn")],
        ],
    )
    try:
        session = await runtime.session_manager.create_session()
        result = await runtime.runner.execute_turn(
            session=session.info,
            prompt="Create two small files.",
            options={"provider": "scripted", "model": MODEL.id},
        )
        assert result.stop_reason == "end_turn"
        assert (workspace / "one.txt").read_text(encoding="utf-8") == "one\n"
        assert (workspace / "two.txt").read_text(encoding="utf-8") == "two\n"

        changes = [record for record in session.log.records if record["kind"] == "workspace.changes"]
        assert len(changes) == 1
        record = changes[0]
        assert record["metadata"]["turn_id"].startswith("turn_")
        assert record["metadata"]["base"] and record["metadata"]["head"]
        assert record["metadata"]["added"] == 2
        assert {row["path"] for row in record["metadata"]["files"]} == {"one.txt", "two.txt"}

        reloaded = await runtime.commands.sessions_get(session_id=session.session_id)
        persisted = [row for row in reloaded["records"] if row["kind"] == "workspace.changes"]
        assert persisted == changes
    finally:
        await runtime.registry.unload_all()


@pytest.mark.asyncio
async def test_workspace_changes_are_emitted_when_provider_fails(tmp_path: Path) -> None:
    runtime, workspace = await _boot(
        tmp_path,
        [ProviderError("scripted", MODEL.id, kind="server", message="provider failed")],
    )
    try:
        session = await runtime.session_manager.create_session()
        result = await runtime.runner.execute_turn(
            session=session.info,
            prompt="Please continue.",
            options={"provider": "scripted", "model": MODEL.id},
        )
        assert result.stop_reason == "error"
        assert any(record["kind"] == "workspace.changes" for record in session.log.records)
    finally:
        await runtime.registry.unload_all()


@pytest.mark.asyncio
async def test_workspace_changes_are_emitted_when_turn_is_cancelled(tmp_path: Path) -> None:
    import asyncio

    cancel = asyncio.Event()

    class SelfCancellingProvider(ScriptedProvider):
        async def generate(self, request):
            self.requests.append(request)
            yield TextDelta(text="Working…")
            cancel.set()
            yield Done(stop_reason="end_turn")

    runtime, _workspace = await _boot(tmp_path, [], provider=SelfCancellingProvider(models=[MODEL]))
    try:
        session = await runtime.session_manager.create_session()
        result = await runtime.runner.execute_turn(
            session=session.info,
            prompt="Start and then cancel.",
            options={"provider": "scripted", "model": MODEL.id},
            cancel_event=cancel,
        )
        assert result.stop_reason == "cancelled"
        assert any(record["kind"] == "workspace.changes" for record in session.log.records)
    finally:
        await runtime.registry.unload_all()


@pytest.mark.asyncio
async def test_cancellation_during_prompt_hook_runs_stop_hook_and_records_changes(tmp_path: Path) -> None:
    import asyncio

    entered = asyncio.Event()

    class BlockingPromptHook:
        name = "test_blocking_prompt"
        events = frozenset({HookEvent.USER_PROMPT_SUBMIT})
        timeout_s = 30.0

        async def on(self, event, payload, ctx):
            entered.set()
            await asyncio.Event().wait()
            return HookOutcome(action="continue")

    runtime, _workspace = await _boot(tmp_path, [])
    runtime.registry.register_hook(BlockingPromptHook(), plugin_name="test_ws07")
    try:
        session = await runtime.session_manager.create_session()
        task = asyncio.create_task(
            runtime.runner.execute_turn(
                session=session.info,
                prompt="Cancel while a prompt hook is waiting.",
                options={"provider": "scripted", "model": MODEL.id},
            )
        )
        await asyncio.wait_for(entered.wait(), timeout=5)
        task.cancel()
        result = await asyncio.wait_for(task, timeout=5)

        assert result.stop_reason == "cancelled"
        assert any(record["kind"] == "turn.cancelled" for record in session.log.records)
        changes = [record for record in session.log.records if record["kind"] == "workspace.changes"]
        assert len(changes) == 1
        assert changes[0]["metadata"]["turn_id"].startswith("turn_")
    finally:
        await runtime.registry.unload_all()


@pytest.mark.asyncio
async def test_restore_command_requires_approval_reverts_and_logs(tmp_path: Path) -> None:
    import asyncio

    runtime, workspace = await _boot(tmp_path, [])
    sub = runtime.bus.subscribe(interactive=True)
    target = workspace / "app.txt"
    target.write_text("before\n", encoding="utf-8")
    repo = ShadowRepo(workspace)
    base, _ = await repo.snapshot("before")
    assert base
    target.write_text("after\n", encoding="utf-8")
    try:
        session = await runtime.session_manager.create_session()
        task = asyncio.create_task(
            runtime.commands.dispatch(
                "plugins.hook-checkpoints.restore",
                {"session_id": session.session_id, "sha": base},
            )
        )
        approval = None
        while approval is None:
            event = await asyncio.wait_for(sub.get(), timeout=5)
            if event.type == "approval.required":
                approval = event.data
        approval_id = approval["approval_id"] if isinstance(approval, dict) else approval.approval_id
        await runtime.commands.resolve_approval(approval_id=approval_id, approved=True, by="user")
        result = await asyncio.wait_for(task, timeout=5)

        assert result["status"] == "restored"
        assert target.read_text(encoding="utf-8") == "before\n"
        restored = [record for record in session.log.records if record["kind"] == "checkpoint.restored"]
        assert len(restored) == 1
        assert restored[0]["metadata"]["sha"] == base
        assert restored[0]["metadata"]["files_touched"] == 1
    finally:
        runtime.bus.unsubscribe(sub)
        await runtime.registry.unload_all()


@pytest.mark.asyncio
async def test_diff_and_restore_are_confined_to_shadow_repo(tmp_path: Path) -> None:
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    target = workspace / "app.txt"
    target.write_text("before\n", encoding="utf-8")
    repo = ShadowRepo(workspace)
    base, _ = await repo.snapshot("before")
    assert base
    target.write_text("after\n", encoding="utf-8")
    head, _ = await repo.snapshot("after")
    assert head
    assert await repo.has_commit(base)
    assert await repo.has_commit(head)
    assert await repo.list_checkpoints(limit=10)
