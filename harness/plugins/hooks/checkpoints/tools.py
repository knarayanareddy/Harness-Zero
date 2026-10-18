"""Checkpoint restore tool implementation (destructive; still policy-gated)."""
from __future__ import annotations

import inspect
import logging
from typing import Any

from harness.sdk.cards import Card
from harness.sdk.context import ToolContext
from harness.sdk.events import CheckpointRestored
from harness.sdk.types import RiskClass, TextBlock, ToolResult, ToolSpec

from .changes import changed_file_count, validate_checkpoint_sha
from .hook import TURN_START_SHA_KEY
from .shadow_repo import ShadowRepo

logger = logging.getLogger(__name__)

CHECKPOINT_RESTORE_SPEC = ToolSpec(
    name="checkpoint_restore",
    description=(
        "Restores the workspace to a previous shadow checkpoint. This is destructive and requires user approval. "
        "Use checkpoint_id 'turn-start' to restore to the beginning of the current turn."
    ),
    input_schema={
        "type": "object",
        "properties": {
            "checkpoint_id": {
                "type": "string",
                "description": "Full shadow-repo commit SHA, or 'turn-start' for this turn's start snapshot.",
            },
        },
        "required": ["checkpoint_id"],
        "additionalProperties": False,
    },
    risk_class=RiskClass.DESTRUCTIVE,
    capabilities=frozenset(["fs:write"]),
)


async def handle_checkpoint_restore(args: dict[str, Any], ctx: ToolContext) -> ToolResult:
    """Restore a verified shadow checkpoint after the PolicyGate has authorized the destructive tool."""
    checkpoint_id = str(args.get("checkpoint_id", "")).strip()
    if not checkpoint_id:
        return ToolResult.error("Parameter 'checkpoint_id' is required.")

    label = checkpoint_id
    if checkpoint_id in {"turn-start", "turn_start"}:
        state = getattr(ctx, "turn_state", {})
        target = state.get(TURN_START_SHA_KEY) if isinstance(state, dict) else None
        if not target:
            return ToolResult.error("The current turn has no start checkpoint to restore.")
        label = "turn-start"
    else:
        try:
            target = validate_checkpoint_sha(checkpoint_id)
        except ValueError as err:
            return ToolResult.error(str(err))

    repo = ShadowRepo(ctx.workspace)
    try:
        if not await repo.has_commit(target):
            return ToolResult.error("Checkpoint does not exist in this workspace's shadow repository.")
        before = await repo.head()
        files_touched = await changed_file_count(repo, before, target) if before else 0
        await repo.restore(target)

        checkpoints = await repo.list_checkpoints(limit=1000)
        label = next((row["label"] for row in checkpoints if row["sha"] == target), label)
        payload = CheckpointRestored(sha=target, label=label, files_touched=files_touched)
        metadata = {"sha": target, "label": label, "files_touched": files_touched}
        emitter = getattr(ctx, "emit_record", None)
        if callable(emitter):
            try:
                emitted = emitter(
                    payload,
                    content=f"Workspace restored to checkpoint '{label}' ({files_touched} file(s) changed).",
                    metadata=metadata,
                )
                if inspect.isawaitable(emitted):
                    await emitted
            except Exception as err:  # noqa: BLE001 - a completed restore must not be reported as failed due to logging
                logger.warning("Could not persist checkpoint.restored: %s", err)

        card = Card.notice(
            body=f"Workspace restored to checkpoint '{label}' ({files_touched} file(s) changed).",
            level="warn",
            title="Workspace restored",
        )
        return ToolResult(
            content=[TextBlock(text=f"Restored workspace to checkpoint '{label}'.")],
            cards=[card],
            metadata={"checkpoint_id": target, "label": label, "files_touched": files_touched},
        )
    except Exception as err:  # noqa: BLE001 - return a user-visible tool failure, not an unhandled kernel error
        return ToolResult.error(f"Failed to restore checkpoint '{target}': {err}")
