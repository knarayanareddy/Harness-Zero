"""
Checkpoint restore tool implementation (§PLG-22).
Reverts workspace to a previous shadow git checkpoint with DESTRUCTIVE risk classification.
"""
from __future__ import annotations

from typing import Any

from harness.sdk.cards import Card
from harness.sdk.context import ToolContext
from harness.sdk.types import RiskClass, TextBlock, ToolResult, ToolSpec

from .shadow_repo import ShadowRepo

CHECKPOINT_RESTORE_SPEC = ToolSpec(
    name="checkpoint_restore",
    description="Restores the workspace to a previous checkpoint snapshot.",
    input_schema={
        "type": "object",
        "properties": {
            "checkpoint_id": {
                "type": "string",
                "description": "Commit SHA or identifier of the checkpoint to restore.",
            },
        },
        "required": ["checkpoint_id"],
        "additionalProperties": False,
    },
    risk_class=RiskClass.DESTRUCTIVE,
    capabilities=frozenset(["fs:write"]),
)


async def handle_checkpoint_restore(args: dict[str, Any], ctx: ToolContext) -> ToolResult:
    """Restores workspace to the specified checkpoint."""
    checkpoint_id = str(args.get("checkpoint_id", "")).strip()
    if not checkpoint_id:
        return ToolResult.error("Parameter 'checkpoint_id' is required.")

    repo = ShadowRepo(ctx.workspace)
    try:
        msg = await repo.restore(checkpoint_id)
        card = Card.notice(
            body=f"Workspace restored to checkpoint {checkpoint_id[:8]}",
            level="warn",
        )
        return ToolResult(
            content=[TextBlock(text=msg)],
            cards=[card],
            metadata={"checkpoint_id": checkpoint_id},
        )
    except Exception as err:
        return ToolResult.error(f"Failed to restore checkpoint '{checkpoint_id}': {err}")
