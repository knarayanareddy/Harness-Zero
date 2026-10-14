import asyncio
from pathlib import Path

import pytest

from harness.sandboxing.executor import CommandExecutor


@pytest.mark.asyncio
async def test_synchronous_command_execution(tmp_path: Path):
    executor = CommandExecutor()
    res = await executor.run("echo 'Harness-Zero Safe Shell'", tmp_path, wait_ms_before_async=2000)
    assert res["status"] == "completed"
    assert res["exit_code"] == 0
    assert "Harness-Zero Safe Shell" in res["stdout"]
    assert res["is_background"] is False


@pytest.mark.asyncio
async def test_async_background_command_transition(tmp_path: Path):
    completed_events = []

    def on_complete(task):
        completed_events.append(task.task_id)

    executor = CommandExecutor(on_task_completed=on_complete)
    # Sleep 1s with 100ms sync threshold -> should transition to background
    res = await executor.run("sleep 0.5 && echo 'Done'", tmp_path, wait_ms_before_async=100)
    assert res["is_background"] is True
    assert "task_id" in res
    task_id = res["task_id"]

    # Wait for completion
    await asyncio.sleep(0.8)
    task = executor.get_task(task_id)
    assert task is not None
    assert task.status == "completed"
    assert task_id in completed_events
