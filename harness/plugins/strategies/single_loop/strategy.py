"""
Single Loop Strategy Plugin (§5.2, §5.6, PLG-09).
Default agent turn loop executing via KernelServices only.
Absorbs LOOP-01 and LOOP-04.
"""
from __future__ import annotations

import logging

from harness.sdk.context import KernelServices, TurnContext
from harness.sdk.types import (
    Message,
    Role,
    TextBlock,
    ThinkingBlock,
    ToolCall,
    ToolResultBlock,
    ToolUseBlock,
    TurnResult,
    Usage,
)

logger = logging.getLogger(__name__)


class SingleLoopStrategy:
    """
    Standard single-turn execution loop:
    1. Loop up to max_tool_iterations.
    2. Check turn cancellation.
    3. Assembles context, runs budget-triggered compaction, calls model.
    4. Evaluates tool calls via KernelServices (enforcing PolicyGate).
    5. Reactively suspends and wakes on background tasks (zero polling).
    6. Allows up to 2 STOP-hook re-entry iterations if completion is blocked.
    """

    name: str = "single_loop"

    async def run(self, turn: TurnContext, k: KernelServices) -> TurnResult:
        max_iterations = turn.options.get("max_iterations", 20)
        total_usage = Usage()
        total_tool_calls = 0
        final_output = ""
        final_thinking = ""
        stop_reason = "end_turn"
        stop_reentries = 0

        for _iteration in range(max_iterations):
            if turn.cancel.is_set():
                stop_reason = "cancelled"
                break

            # 1. Budget-triggered compaction and context assembly
            await k.compact_if_needed()
            req = await k.assemble_context()

            # 2. Call model through mediated kernel service
            assistant_msg = await k.call_model(req)
            await k.record(assistant_msg)

            # Accumulate text and thinking
            for block in assistant_msg.content:
                if isinstance(block, TextBlock):
                    final_output += block.text
                elif isinstance(block, ThinkingBlock):
                    final_thinking += block.thinking

            if "usage" in assistant_msg.metadata:
                total_usage = total_usage + Usage.from_dict(assistant_msg.metadata["usage"])

            # 3. Extract tool use requests
            tool_use_blocks = [b for b in assistant_msg.content if isinstance(b, ToolUseBlock)]

            if not tool_use_blocks:
                # No tool calls: check for pending background tasks
                pending = k.pending_tasks()
                if pending and turn.options.get("wait_for_background", True):
                    wait_s = turn.options.get("background_wait_s", 600.0)
                    wakeup = await k.wait_for_wakeup(timeout_s=wait_s)
                    if wakeup:
                        wakeup_msg = Message(
                            role=Role.USER,
                            content=[
                                TextBlock(
                                    text=(
                                        f"Background task {wakeup.task_id} ({wakeup.label}) "
                                        f"finished with exit {wakeup.exit_code}:\n{wakeup.tail}"
                                    )
                                )
                            ],
                            metadata={"wakeup": wakeup.task_id},
                        )
                        await k.record(wakeup_msg)
                        continue

                # 4. STOP-hook evaluation and re-entry (max 2 times)
                if stop_reentries < 2 and hasattr(k, "evaluate_stop_hooks"):
                    outcome = await k.evaluate_stop_hooks(final_output)
                    if outcome and getattr(outcome, "action", "") == "block":
                        stop_reentries += 1
                        reason = getattr(outcome, "reason", "STOP hook requested additional verification.")
                        stop_msg = Message(
                            role=Role.USER,
                            content=[TextBlock(text=f"STOP hook blocked completion: {reason}")],
                            metadata={"stop_hook": True},
                        )
                        await k.record(stop_msg)
                        continue

                # Normal turn completion
                break

            # 5. Execute tool calls through PolicyGate
            total_tool_calls += len(tool_use_blocks)
            tool_calls = [
                ToolCall(id=b.id, name=b.name, arguments=b.input)
                for b in tool_use_blocks
            ]
            results = await k.execute_tools(tool_calls)

            # 6. Record tool results into transcript
            tool_result_blocks = [
                ToolResultBlock(
                    tool_use_id=b.id,
                    content=res.content,
                    is_error=res.is_error,
                )
                for b, res in zip(tool_use_blocks, results, strict=False)
            ]
            result_msg = Message(
                role=Role.USER,
                content=tool_result_blocks,
                metadata={"tool_results": True},
            )
            await k.record(result_msg)

        return TurnResult(
            output=final_output,
            thinking=final_thinking,
            usage=total_usage,
            tool_calls=total_tool_calls,
            stop_reason=stop_reason,
        )
