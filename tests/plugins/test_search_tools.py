"""
Unit and conformance tests for search-tools plugin (PLG-05).
"""
from __future__ import annotations

from pathlib import Path
from typing import Any

import pytest

from harness.kernel.approvals import ApprovalBroker
from harness.kernel.bus import EventBus
from harness.kernel.gate import PolicyGate
from harness.kernel.jail import WorkspaceJail
from harness.kernel.registry import PluginRegistry
from harness.kernel.secrets import Secrets
from harness.kernel.settings import SettingsStore
from harness.plugins.executors.local.executor import LocalExecutor
from harness.plugins.tools.search.tools import (
    SEARCH_CODEBASE_SPEC,
    handle_search_codebase,
)
from harness.sdk.context import ToolContext
from harness.sdk.testing.kits import ToolKit
from harness.sdk.types import Capability, ToolCall


class DummySearchContext(ToolContext):
    def __init__(self, workspace: Path, executor: Any = None):
        self.call_id = "test_search"
        self.workspace = workspace
        self.data_dir = workspace / "data"
        self._executor = executor
        self.capabilities = (
            frozenset([Capability.FS_READ, Capability.EXEC])
            if executor
            else frozenset([Capability.FS_READ])
        )

    @property
    def executor(self) -> Any:
        return self._executor

    def emit_output_delta(self, stream: str, text: str) -> None:
        pass

    def background(self, proc: Any, label: str = "") -> str:
        return "dummy_task"

    def get_task(self, task_id: str) -> dict[str, Any] | None:
        return None

    def list_tasks(self) -> list[dict[str, Any]]:
        return []

    async def kill_task(self, task_id: str) -> bool:
        return False

    async def send_task_input(self, task_id: str, text: str) -> bool:
        return False

    async def wait_task(self, task_id: str, timeout_s: float | None = None) -> Any:
        return None


def test_toolkit_conformance():
    kit = ToolKit()
    kit.test_input_schema_valid_and_closed(SEARCH_CODEBASE_SPEC)
    kit.test_workspace_path_formatting(SEARCH_CODEBASE_SPEC)
    kit.test_declared_risk_class_bound(SEARCH_CODEBASE_SPEC)
    kit.test_no_direct_subprocess_import(handle_search_codebase)


@pytest.mark.asyncio
async def test_search_python_fallback(tmp_path: Path):
    f1 = tmp_path / "hello.py"
    f1.write_text("def hello_world():\n    return 'greeting'\n", encoding="utf-8")
    f2 = tmp_path / "other.txt"
    f2.write_text("Nothing to see here\n", encoding="utf-8")
    f3 = tmp_path / "deep" / "nested.py"
    f3.parent.mkdir()
    f3.write_text("class HelloUniverse:\n    pass\n", encoding="utf-8")

    ctx = DummySearchContext(workspace=tmp_path, executor=None)

    # 1. Basic search
    res = await handle_search_codebase({"query": r"hello_\w+"}, ctx)
    assert not res.is_error
    assert "hello.py:1: def hello_world():" in res.output
    assert res.metadata.get("count") == 1

    # 2. Case sensitivity
    res_case = await handle_search_codebase(
        {"query": "Hello", "case_sensitive": True},
        ctx,
    )
    assert not res_case.is_error
    assert "HelloUniverse" in res_case.output
    assert "hello_world" not in res_case.output

    # 3. Glob filter
    res_glob = await handle_search_codebase(
        {"query": "Nothing", "glob_filter": "*.py"},
        ctx,
    )
    assert not res_glob.is_error
    assert "No matches found" in res_glob.output

    # 4. Pagination
    res_multi = await handle_search_codebase(
        {"query": "def|class", "max_results": 1, "offset": 0},
        ctx,
    )
    assert not res_multi.is_error
    assert res_multi.metadata.get("count") == 1
    assert res_multi.metadata.get("has_more") is True

    # 5. Invalid regex
    res_invalid = await handle_search_codebase({"query": "[unclosed("}, ctx)
    assert res_invalid.is_error
    assert "Invalid regex pattern" in res_invalid.output


@pytest.mark.asyncio
async def test_search_with_executor_ripgrep(tmp_path: Path):
    f = tmp_path / "sample.py"
    f.write_text("import os\nimport sys\n# TODO: implement logic\n", encoding="utf-8")

    executor = LocalExecutor(default_cwd=tmp_path)
    ctx = DummySearchContext(workspace=tmp_path, executor=executor)

    res = await handle_search_codebase({"query": "TODO.*logic"}, ctx)
    assert not res.is_error
    assert "TODO: implement logic" in res.output


@pytest.mark.asyncio
async def test_gate_integration(tmp_path: Path):
    f = tmp_path / "test_file.py"
    f.write_text("CONST_TOKEN = 12345\n", encoding="utf-8")

    bus = EventBus()
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
    )

    call = ToolCall(
        id="call_search_1",
        name="search_codebase",
        arguments={"query": "CONST_TOKEN"},
    )
    res = await gate.execute(call)
    assert not res.is_error
    assert "CONST_TOKEN = 12345" in res.output
