"""
Unit and conformance tests for shell-tools plugin (PLG-04).
"""
from __future__ import annotations

from pathlib import Path
from typing import Any

import pytest

from harness.kernel.approvals import ApprovalBroker
from harness.kernel.bus import EventBus
from harness.kernel.gate import PolicyGate
from harness.kernel.inbox import BackgroundInbox
from harness.kernel.jail import WorkspaceJail
from harness.kernel.registry import PluginRegistry
from harness.kernel.secrets import Secrets
from harness.kernel.settings import SettingsStore
from harness.plugins.executors.local.executor import LocalExecutor
from harness.plugins.tools.shell.tools import (
    MANAGE_TASK_SPEC,
    RUN_COMMAND_SPEC,
    handle_manage_task,
    handle_run_command,
)
from harness.sdk.context import ToolContext
from harness.sdk.contracts import Process
from harness.sdk.testing.kits import ToolKit
from harness.sdk.types import Capability, ToolCall


class DummyToolContext(ToolContext):
    def __init__(self, workspace: Path, executor: Any, inbox: Any = None):
        self.call_id = "test_call"
        self.workspace = workspace
        self.data_dir = workspace / "data"
        self.executor = executor
        self.capabilities = frozenset([Capability.EXEC])
        self.deltas: list[tuple[str, str]] = []
        self._inbox = inbox or BackgroundInbox()

    def emit_output_delta(self, stream: str, text: str) -> None:
        self.deltas.append((stream, text))

    def background(self, proc: Process, label: str = "") -> str:
        tid = f"task_{label[:8]}"
        self._inbox.register_task(tid, proc, label=label)
        return tid

    def get_task(self, task_id: str) -> dict[str, Any] | None:
        return self._inbox.get_status(task_id)

    def list_tasks(self) -> list[dict[str, Any]]:
        return self._inbox.list_tasks()

    async def kill_task(self, task_id: str) -> bool:
        return await self._inbox.kill(task_id)

    async def send_task_input(self, task_id: str, text: str) -> bool:
        return await self._inbox.send_input(task_id, text)

    async def wait_task(self, task_id: str, timeout_s: float | None = None) -> Any:
        return await self._inbox.wait_task(task_id, timeout_s=timeout_s)


def test_toolkit_conformance():
    kit = ToolKit()
    for spec in (RUN_COMMAND_SPEC, MANAGE_TASK_SPEC):
        kit.test_input_schema_valid_and_closed(spec)
        kit.test_workspace_path_formatting(spec)
        kit.test_declared_risk_class_bound(spec)

    kit.test_no_direct_subprocess_import(handle_run_command)
    kit.test_no_direct_subprocess_import(handle_manage_task)


@pytest.mark.asyncio
async def test_run_command_sync_echo(tmp_path: Path):
    executor = LocalExecutor(default_cwd=tmp_path)
    ctx = DummyToolContext(workspace=tmp_path, executor=executor)

    res = await handle_run_command(
        {"command": "echo 'hello shell tools'"},
        ctx,
    )
    assert not res.is_error
    assert "hello shell tools" in res.output
    assert res.metadata.get("exit_code") == 0


@pytest.mark.asyncio
async def test_run_command_delta_streaming(tmp_path: Path):
    executor = LocalExecutor(default_cwd=tmp_path)
    ctx = DummyToolContext(workspace=tmp_path, executor=executor)

    res = await handle_run_command(
        {"command": "echo line1 && echo line2"},
        ctx,
    )
    assert not res.is_error
    assert len(ctx.deltas) >= 1
    streams = [s for s, _ in ctx.deltas]
    assert "stdout" in streams


@pytest.mark.asyncio
async def test_run_command_background_transition_and_manage(tmp_path: Path):
    executor = LocalExecutor(default_cwd=tmp_path)
    inbox = BackgroundInbox()
    ctx = DummyToolContext(workspace=tmp_path, executor=executor, inbox=inbox)

    # 1. Run command that exceeds wait_ms_before_async
    res = await handle_run_command(
        {"command": "sleep 1 && echo BG_DONE", "wait_ms_before_async": 100},
        ctx,
    )
    assert not res.is_error
    assert res.metadata.get("background") is True
    task_id = res.metadata.get("task_id")
    assert task_id

    # 2. Check task status via manage_task
    st_res = await handle_manage_task(
        {"action": "status", "task_id": task_id},
        ctx,
    )
    assert not st_res.is_error
    assert task_id in st_res.output

    # 3. List tasks
    list_res = await handle_manage_task(
        {"action": "list"},
        ctx,
    )
    assert not list_res.is_error
    assert task_id in list_res.output

    # 4. Wait for completion
    wait_res = await handle_manage_task(
        {"action": "wait", "task_id": task_id, "timeout_ms": 3000},
        ctx,
    )
    assert not wait_res.is_error
    assert wait_res.metadata.get("exit_code") == 0


@pytest.mark.asyncio
async def test_manage_task_kill(tmp_path: Path):
    executor = LocalExecutor(default_cwd=tmp_path)
    inbox = BackgroundInbox()
    ctx = DummyToolContext(workspace=tmp_path, executor=executor, inbox=inbox)

    res = await handle_run_command(
        {"command": "sleep 10", "wait_ms_before_async": 100},
        ctx,
    )
    task_id = res.metadata["task_id"]

    kill_res = await handle_manage_task(
        {"action": "kill", "task_id": task_id},
        ctx,
    )
    assert not kill_res.is_error
    assert "killed successfully" in kill_res.output


@pytest.mark.asyncio
async def test_gate_integration(tmp_path: Path):
    bus = EventBus()
    inbox = BackgroundInbox(bus=bus)
    settings = SettingsStore(path=tmp_path / "settings.json")
    settings.set("kernel.security.permission_mode", "bypass")
    secrets = Secrets(path=tmp_path / "secrets.json")
    jail = WorkspaceJail(root=tmp_path)
    approvals = ApprovalBroker(bus=bus)

    registry = PluginRegistry(
        workspace=tmp_path,
        settings=settings,
        secrets=secrets,
        bus=bus,
    )
    plugin_dir = Path(__file__).resolve().parents[2] / "harness" / "plugins"
    registry.discover_all(builtin_dir=plugin_dir)
    await registry.load_all()

    gate = PolicyGate(
        registry=registry,
        jail=jail,
        settings=settings,
        secrets=secrets,
        approvals=approvals,
        bus=bus,
        inbox=inbox,
    )

    call = ToolCall(
        id="call_cmd_1",
        name="run_command",
        arguments={"command": "echo 'gate integration success'"},
    )
    res = await gate.execute(call)
    assert not res.is_error
    assert "gate integration success" in res.output
