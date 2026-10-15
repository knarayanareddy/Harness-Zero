"""
Tests for PLG-02: Local Executor plugin (§4, §11, PLG-02).
Uses ExecutorKit and streaming process validation.
"""
from __future__ import annotations

import sys
from pathlib import Path

import pytest

from harness.kernel.bus import EventBus
from harness.kernel.registry import PluginRegistry
from harness.kernel.secrets import Secrets
from harness.kernel.settings import SettingsStore
from harness.plugins.executors.local.executor import LocalExecutor
from harness.sdk.testing import ExecutorKit
from harness.sdk.types import ExecRequest


@pytest.fixture
def executor(tmp_path: Path) -> LocalExecutor:
    return LocalExecutor(default_cwd=tmp_path)


@pytest.mark.asyncio
async def test_executor_kit_conformance(executor: LocalExecutor, tmp_path: Path):
    """Runs all standard ExecutorKit conformance tests."""
    kit = ExecutorKit()
    await kit.test_echo_round_trip(executor, tmp_path)
    await kit.test_cwd_enforced(executor, tmp_path)
    await kit.test_kill_idempotency(executor, tmp_path)


@pytest.mark.asyncio
async def test_streaming_output(executor: LocalExecutor, tmp_path: Path):
    """Verifies that output() yields stdout chunks asynchronously."""
    req = ExecRequest(
        command=[sys.executable, "-c", "import sys, time; sys.stdout.write('chunk1\\n'); sys.stdout.flush(); time.sleep(0.05); sys.stdout.write('chunk2\\n'); sys.stdout.flush()"],
        cwd=tmp_path,
        shell=False,
    )
    proc = await executor.start(req)

    streamed_chunks = []
    async for stream_name, chunk in proc.output():
        streamed_chunks.append((stream_name, chunk.strip()))

    res = await proc.wait()
    assert res.exit_code == 0
    assert ("stdout", "chunk1") in streamed_chunks
    assert ("stdout", "chunk2") in streamed_chunks
    assert "chunk1" in res.stdout
    assert "chunk2" in res.stdout


@pytest.mark.asyncio
async def test_send_input(executor: LocalExecutor, tmp_path: Path):
    """Verifies send_input sends data to child process stdin."""
    req = ExecRequest(
        command=[sys.executable, "-c", "import sys; data = sys.stdin.readline().strip(); print(f'ECHO_{data}')"],
        cwd=tmp_path,
        shell=False,
        interactive=True,
    )
    proc = await executor.start(req)
    await proc.send_input("message_to_stdin\n")

    res = await proc.wait()
    assert res.exit_code == 0
    assert "ECHO_message_to_stdin" in res.stdout


@pytest.mark.asyncio
async def test_plugin_discovery_and_setup(tmp_path: Path):
    """Verifies PluginRegistry discovers and registers local-executor."""
    ws = tmp_path / "ws"
    ws.mkdir()
    conf = tmp_path / "conf"
    conf.mkdir()

    bus = EventBus()
    settings = SettingsStore(path=conf)
    secrets = Secrets(path=conf)
    registry = PluginRegistry(workspace=ws, settings=settings, secrets=secrets, bus=bus)

    builtin_dir = Path(__file__).resolve().parents[2] / "harness" / "plugins"
    registry.discover_all(builtin_dir=builtin_dir)
    await registry.load_all()

    assert "local-executor" in registry.discovered
    assert registry.is_loaded("local-executor")
    assert "default" in registry.executors or "local" in registry.executors
