"""
Tool Guidance Context Source (§5.4, PLG-07).
Emits STATIC Tier-1 system prompt block establishing operational rules for tool execution.
"""
from __future__ import annotations

from typing import Any

from harness.sdk.types import ContextBlock, Stability


class ToolGuidanceContextSource:
    """Emits immutable STATIC tool usage guidance rules."""

    name: str = "tool_guidance"

    async def blocks(self, ctx: Any) -> list[ContextBlock]:
        guidance = (
            "Tool Execution Rules:\n"
            "1. Read before writing: inspect files using view_file before editing.\n"
            "2. Precise edits: use edit_file with exact SEARCH/REPLACE blocks.\n"
            "3. Shell commands: run commands via run_command with appropriate cwd.\n"
            "4. Search: use search_codebase to locate definitions, patterns, and usages.\n"
            "5. Task management: for long commands or background tasks, use manage_task.\n"
            "6. Invariant verification: run test suites to ensure zero regressions."
        )
        return [
            ContextBlock(
                id="tool_guidance",
                text=guidance,
                stability=Stability.STATIC,
                priority=20,
                source=self.name,
                token_estimate=len(guidance) // 4,
            )
        ]
