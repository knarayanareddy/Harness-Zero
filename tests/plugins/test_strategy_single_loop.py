"""
Unit and conformance tests for single_loop strategy plugin (PLG-09).
Absorbs LOOP-01 and scenario S6.
"""
from __future__ import annotations

import asyncio
from pathlib import Path
from typing import Any

import pytest

from harness.kernel.inbox import BackgroundInbox, WakeupEvent
from harness.plugins.strategies.single_loop.strategy import SingleLoopStrategy
from harness.sdk.testing.kits import StrategyKit
from harness.sdk.types import (
    HookOutcome,
    Message,
    ModelParams,
    ModelRequest,
    Role,
    TaskInfo,
    TextBlock,
    ToolCall,
    ToolResult,
    ToolUseBlock,
)


class ScriptedKernelServices:
    def __init__(self, responses: list[Message], inbox: BackgroundInbox | None = None):
        self.responses = list(responses)
        self.recorded: list[Message] = []
        self.tool_calls_executed: list[ToolCall] = []
        self.inbox = inbox or BackgroundInbox()
        self.stop_hook_outcome: HookOutcome | None = None

    async def assemble_context(self, *, tools: Any = None) -> ModelRequest:
        return ModelRequest(
            model="mock",
            system=[],
            messages=self.recorded,
            tools=[],
            params=ModelParams(),
        )

    async def call_model(self, req: ModelRequest) -> Message:
        if not self.responses:
            return Message(role=Role.ASSISTANT, content=[TextBlock(text="Default ending")])
        return self.responses.pop(0)

    async def execute_tools(self, calls: list[ToolCall]) -> list[ToolResult]:
        self.tool_calls_executed.extend(calls)
        results = []
        for c in calls:
            results.append(ToolResult.text(f"Executed {c.name}"))
        return results

    async def execute_tool(self, call: ToolCall) -> ToolResult:
        res = await self.execute_tools([call])
        return res[0]

    async def record(self, msg: Message) -> int:
        self.recorded.append(msg)
        return len(self.recorded)

    async def compact_if_needed(self) -> bool:
        return False

    def pending_tasks(self) -> list[TaskInfo]:
        if self.inbox.has_pending():
            return [TaskInfo(id="task_bg_1", label="sleep", running=True)]
        return []

    async def wait_for_wakeup(self, timeout_s: float = 600.0) -> Any:
        return await self.inbox.wait_for_wakeup(timeout_s)

    async def evaluate_stop_hooks(self, output: str) -> HookOutcome | None:
        return self.stop_hook_outcome


class DummyTurnContext:
    def __init__(self, workspace: Path, options: dict[str, Any] | None = None):
        self.workspace = workspace
        self.options = options or {}
        self.cancel = asyncio.Event()


# -----------------------------------------------------------------------------
# 1. StrategyKit Conformance
# -----------------------------------------------------------------------------
@pytest.mark.asyncio
async def test_strategykit_conformance(tmp_path: Path):
    kit = StrategyKit()
    strategy = SingleLoopStrategy()
    turn_ctx = DummyTurnContext(tmp_path)

    msg = Message(
        role=Role.ASSISTANT,
        content=[TextBlock(text="Conformance test completed.")],
    )
    k = ScriptedKernelServices([msg])
    await kit.test_strategy_completes_turn(strategy, turn_ctx, k)


# -----------------------------------------------------------------------------
# 2. Scenario S6: Background Wake-up (Zero Polling)
# -----------------------------------------------------------------------------
@pytest.mark.asyncio
async def test_scenario_s6_background_wakeup(tmp_path: Path):
    """
    S6: Model launches a background command, turn suspends reactively,
    and resumes on task exit with the tail output WITHOUT polling manage_task.
    """
    inbox = BackgroundInbox()
    strategy = SingleLoopStrategy()
    turn_ctx = DummyTurnContext(
        tmp_path,
        options={"wait_for_background": True, "background_wait_s": 2.0},
    )

    # Response 1: Model issues a tool call
    resp1 = Message(
        role=Role.ASSISTANT,
        content=[
            ToolUseBlock(
                id="call_bg",
                name="run_command",
                input={"command": "sleep 1 && echo FINISHED", "wait_ms_before_async": 100},
            )
        ],
    )

    # Response 2: Model finishes turn while task is still running
    resp2 = Message(
        role=Role.ASSISTANT,
        content=[TextBlock(text="Task is running in background. Waiting for it.")],
    )

    # Response 3: Model receives wakeup with output and provides final answer
    resp3 = Message(
        role=Role.ASSISTANT,
        content=[TextBlock(text="The command has finished successfully. Output: FINISHED")],
    )

    k = ScriptedKernelServices([resp1, resp2, resp3], inbox=inbox)

    # Simulate background task completion shortly after resp2
    async def _simulate_task():
        # Wait a moment, register pending task in inbox
        await asyncio.sleep(0.05)
        # Enqueue wakeup event into inbox queue
        wakeup_event = WakeupEvent(
            task_id="task_bg_1",
            exit_code=0,
            tail="FINISHED\n",
            label="sleep 1 && echo FINISHED",
        )
        await inbox._wakeup_queue.put(wakeup_event)

    sim_task = asyncio.create_task(_simulate_task())

    # Mock pending tasks during loop check
    k.pending_tasks = lambda: [TaskInfo(id="task_bg_1", label="sleep", running=True)]  # type: ignore

    res = await strategy.run(turn_ctx, k)
    await sim_task

    assert "FINISHED" in res.output
    assert res.tool_calls == 1

    # Verify wakeup message was recorded as user message
    wakeup_records = [
        m for m in k.recorded
        if m.role == Role.USER and m.metadata.get("wakeup") == "task_bg_1"
    ]
    assert len(wakeup_records) == 1
    assert "FINISHED" in wakeup_records[0].content[0].text


# -----------------------------------------------------------------------------
# 3. STOP-Hook Re-entry (Max 2 Iterations)
# -----------------------------------------------------------------------------
@pytest.mark.asyncio
async def test_stop_hook_reentry(tmp_path: Path):
    strategy = SingleLoopStrategy()
    turn_ctx = DummyTurnContext(tmp_path)

    # Turn 1: Model claims done
    resp1 = Message(
        role=Role.ASSISTANT,
        content=[TextBlock(text="Finished task without tests.")],
    )
    # Turn 2: Model fixes task after STOP hook blocks
    resp2 = Message(
        role=Role.ASSISTANT,
        content=[TextBlock(text="Added unit test and verified green.")],
    )

    k = ScriptedKernelServices([resp1, resp2])
    # Hook blocks first time
    k.stop_hook_outcome = HookOutcome(action="block", reason="Unit tests required before completion")

    # Evaluate stop hooks dynamically
    call_count = 0

    async def _evaluate_stop_hooks(output: str) -> HookOutcome:
        nonlocal call_count
        call_count += 1
        if call_count == 1:
            return HookOutcome(action="block", reason="Unit tests required before completion")
        return HookOutcome(action="continue")

    k.evaluate_stop_hooks = _evaluate_stop_hooks  # type: ignore

    res = await strategy.run(turn_ctx, k)
    assert call_count == 2
    assert "Added unit test" in res.output

    # Verify STOP hook user message was injected
    stop_msgs = [m for m in k.recorded if m.metadata.get("stop_hook")]
    assert len(stop_msgs) == 1
    assert "Unit tests required" in stop_msgs[0].content[0].text


# -----------------------------------------------------------------------------
# 4. Turn Cancellation
# -----------------------------------------------------------------------------
@pytest.mark.asyncio
async def test_strategy_turn_cancellation(tmp_path: Path):
    strategy = SingleLoopStrategy()
    turn_ctx = DummyTurnContext(tmp_path)
    turn_ctx.cancel.set()

    k = ScriptedKernelServices([])
    res = await strategy.run(turn_ctx, k)
    assert res.stop_reason == "cancelled"
