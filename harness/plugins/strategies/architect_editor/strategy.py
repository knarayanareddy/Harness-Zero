"""
Architect/Editor Strategy Plugin (§5.2, §5.6, PLG-19).
Two-phase workflow: Architect formulates plan; Editor applies surgical SEARCH/REPLACE diffs.
Automated verification via test command; safe file-scoped staging and commit verification.
Absorbs ORCH-01.
"""
from __future__ import annotations

import logging

from harness.sdk.cards import Card
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


class ArchitectEditorStrategy:
    """
    Two-role architect/editor workflow strategy:
    1. Phase 1 (Architect): Formulates structured implementation plan and diffs.
    2. Phase 2 (Editor): Executes surgical edits via 'edit_file' tool.
    3. Phase 3 (Verification): Runs test_command if provided.
    4. Phase 4 (Safe Commit): On green, verifies clean index and stages ONLY changed files.
    """

    name: str = "architect_editor"

    async def run(self, turn: TurnContext, k: KernelServices) -> TurnResult:
        total_usage = Usage()
        total_tool_calls = 0
        total_cards: list[Card] = []
        changed_files: list[str] = []
        output_parts: list[str] = []
        thinking_parts: list[str] = []

        if turn.cancel.is_set():
            return TurnResult(output="Turn cancelled", stop_reason="cancelled")

        # -------------------------------------------------------------
        # 1. Target Files & Verification Command Resolution
        # -------------------------------------------------------------
        target_files = turn.options.get("files", [])
        test_command = turn.options.get("test_command")
        auto_commit = turn.options.get("auto_commit", False)

        # Context guidance
        guidance = [f"User Request: {turn.prompt}"]
        if target_files:
            guidance.append(f"Target Files: {', '.join(target_files)}")
        if test_command:
            guidance.append(f"Verification Command: {test_command}")
        else:
            guidance.append("Verification Command: None (no verification will run)")

        # -------------------------------------------------------------
        # 2. Phase 1: Architect Planning
        # -------------------------------------------------------------
        await k.compact_if_needed()
        req = await k.assemble_context()

        # Inject role guidance if specified
        architect_model = turn.options.get("architect_model") or (
            turn.settings.get("models.architect_model") if hasattr(turn.settings, "get") else None
        )
        if architect_model:
            req.model = architect_model

        architect_msg = await k.call_model(req)
        await k.record(architect_msg)

        # Accumulate usage and output
        msg_usage = getattr(architect_msg, "usage", None)
        if msg_usage:
            total_usage.input_tokens += msg_usage.input_tokens
            total_usage.output_tokens += msg_usage.output_tokens

        for block in architect_msg.content:
            if isinstance(block, TextBlock):
                output_parts.append(block.text)
            elif isinstance(block, ThinkingBlock):
                thinking_parts.append(block.thinking)

        # -------------------------------------------------------------
        # 3. Phase 2: Editor Execution (Tool Loop)
        # -------------------------------------------------------------
        tool_use_blocks = [b for b in architect_msg.content if isinstance(b, ToolUseBlock)]

        # If architect produced immediate tool calls (e.g. edit_file or view_file)
        if tool_use_blocks:
            total_tool_calls += len(tool_use_blocks)
            calls = [ToolCall(id=b.id, name=b.name, arguments=b.input) for b in tool_use_blocks]
            results = await k.execute_tools(calls)

            result_blocks = []
            for b, res in zip(tool_use_blocks, results, strict=False):
                result_blocks.append(
                    ToolResultBlock(tool_use_id=b.id, content=res.content, is_error=res.is_error)
                )
                if res.cards:
                    total_cards.extend(res.cards)
                if not res.is_error and res.metadata.get("changed_files"):
                    changed_files.extend(res.metadata["changed_files"])

            await k.record(
                Message(role=Role.USER, content=result_blocks, metadata={"tool_results": True})
            )

        # Run editor model if secondary loop needed
        editor_model = turn.options.get("editor_model") or (
            turn.settings.get("models.editor_model") if hasattr(turn.settings, "get") else None
        )
        max_editor_iterations = turn.options.get("max_editor_iterations", 5)

        for _ in range(max_editor_iterations):
            if turn.cancel.is_set():
                return TurnResult(output="Cancelled", stop_reason="cancelled")

            # Check if further tool calls needed
            req_editor = await k.assemble_context()
            if editor_model:
                req_editor.model = editor_model

            editor_resp = await k.call_model(req_editor)
            await k.record(editor_resp)

            resp_usage = getattr(editor_resp, "usage", None)
            if resp_usage:
                total_usage.input_tokens += resp_usage.input_tokens
                total_usage.output_tokens += resp_usage.output_tokens

            more_tools = [b for b in editor_resp.content if isinstance(b, ToolUseBlock)]
            if not more_tools:
                for b in editor_resp.content:
                    if isinstance(b, TextBlock):
                        output_parts.append(b.text)
                break

            total_tool_calls += len(more_tools)
            calls = [ToolCall(id=b.id, name=b.name, arguments=b.input) for b in more_tools]
            results = await k.execute_tools(calls)

            res_blocks = []
            for b, res in zip(more_tools, results, strict=False):
                res_blocks.append(
                    ToolResultBlock(tool_use_id=b.id, content=res.content, is_error=res.is_error)
                )
                if res.cards:
                    total_cards.extend(res.cards)
                if not res.is_error and res.metadata.get("changed_files"):
                    changed_files.extend(res.metadata["changed_files"])

            await k.record(
                Message(role=Role.USER, content=res_blocks, metadata={"tool_results": True})
            )

        # -------------------------------------------------------------
        # 4. Phase 3: Verification (test_command)
        # -------------------------------------------------------------
        tests_passed = True
        if test_command:
            total_tool_calls += 1
            test_call = ToolCall(
                id="call_verify_test",
                name="run_command",
                arguments={"command": test_command},
            )
            test_result = await k.execute_tool(test_call)
            if test_result.is_error:
                tests_passed = False
                output_parts.append(f"\nVerification test failed:\n{test_result.output}")
            else:
                output_parts.append(f"\nVerification test passed:\n{test_result.output}")

        # -------------------------------------------------------------
        # 5. Phase 4: Safe Staging & Optional Commit
        # -------------------------------------------------------------
        unique_changed = list(dict.fromkeys(changed_files))

        if auto_commit and unique_changed and tests_passed:
            # Check git index cleanliness
            status_call = ToolCall(
                id="call_check_index",
                name="run_command",
                arguments={"command": "git status --porcelain"},
            )
            status_res = await k.execute_tool(status_call)
            staged_changes = False
            for line in status_res.output.splitlines():
                if line and len(line) >= 2 and line[0] not in (" ", "?"):
                    staged_changes = True
                    break

            if staged_changes:
                output_parts.append("\nRefused to commit: index not clean.")
            else:
                # Stage ONLY touched files (never git add -A)
                files_arg = " ".join(f"'{f}'" for f in unique_changed)
                stage_call = ToolCall(
                    id="call_stage_files",
                    name="run_command",
                    arguments={"command": f"git add -- {files_arg}"},
                )
                await k.execute_tool(stage_call)

                commit_call = ToolCall(
                    id="call_commit",
                    name="run_command",
                    arguments={"command": "git commit -m 'feat: architect/editor automated changes'"},
                )
                commit_res = await k.execute_tool(commit_call)
                if not commit_res.is_error:
                    output_parts.append(f"\nCommitted {len(unique_changed)} changed file(s).")
                else:
                    output_parts.append(f"\nCommit failed: {commit_res.output}")

        # Emit summary card
        if unique_changed:
            total_cards.append(
                Card.notice(
                    body=f"Architect/Editor touched {len(unique_changed)} file(s): {', '.join(unique_changed)}",
                    level="info",
                )
            )

        return TurnResult(
            output="\n".join(output_parts).strip(),
            thinking="\n".join(thinking_parts).strip(),
            usage=total_usage,
            tool_calls=total_tool_calls,
            stop_reason="end_turn",
            cards=total_cards,
            changed_files=unique_changed,
        )
