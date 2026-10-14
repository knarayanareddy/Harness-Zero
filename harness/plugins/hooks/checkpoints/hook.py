"""
Cline-style Checkpoints Hook (§PLG-22).
Automatically snapshots workspace state to a shadow git repository before mutating tools.
"""
from __future__ import annotations

import logging
from typing import Any

from harness.sdk.context import HookContext
from harness.sdk.types import HookEvent, HookOutcome, RiskClass, ToolCall, ToolSpec

from .shadow_repo import ShadowRepo

logger = logging.getLogger(__name__)


class CheckpointHook:
    """Pre-tool hook taking shadow git snapshots before WRITE/EXEC/DESTRUCTIVE tool invocations."""

    name: str = "checkpoints"
    event: HookEvent = HookEvent.PRE_TOOL_USE
    events: frozenset[HookEvent] = frozenset([HookEvent.PRE_TOOL_USE])
    matcher: str | None = r"(write_to_file|edit_file|run_command|create_artifact)"
    timeout_s: float = 5.0

    async def execute(
        self, call: ToolCall | None, spec: ToolSpec | None, ctx: HookContext
    ) -> HookOutcome:
        """Executes before tool use and snapshots workspace if tool is mutating."""
        if call is None or spec is None:
            return HookOutcome(action="continue")

        # Snapshot on state-mutating actions
        if spec.risk_class in (RiskClass.WRITE, RiskClass.EXEC, RiskClass.DESTRUCTIVE):
            try:
                repo = ShadowRepo(ctx.workspace)
                call_id = getattr(call, "id", getattr(call, "call_id", "call"))
                await repo.snapshot(f"pre_{call.name}_{call_id}")
            except Exception as err:
                logger.warning(f"Failed to record checkpoint for {call.name}: {err}")

        return HookOutcome(action="continue")

    async def on(
        self, event: HookEvent, payload: dict[str, Any], ctx: Any
    ) -> HookOutcome:
        """Lifecycle event handler satisfying Hook contract."""
        if event == HookEvent.PRE_TOOL_USE:
            call = payload.get("call")
            spec = payload.get("spec")
            workspace = getattr(ctx, "workspace", None)
            if workspace and call and spec:
                return await self.execute(call, spec, ctx)
        return HookOutcome(action="continue")
