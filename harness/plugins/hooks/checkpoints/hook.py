"""Turn-boundary and pre-mutation snapshots for the private shadow repository."""
from __future__ import annotations

import logging
from typing import Any

from harness.sdk.context import HookContext
from harness.sdk.events import CheckpointCreated, SystemNotice, WorkspaceChanges
from harness.sdk.types import HookEvent, HookOutcome, RiskClass, ToolCall, ToolSpec

from .changes import compute_changes
from .shadow_repo import ShadowRepo, SnapshotResult

logger = logging.getLogger(__name__)

TURN_START_SHA_KEY = "hook-checkpoints.turn_start_sha"
TURN_START_SKIPPED_KEY = "hook-checkpoints.turn_start_skipped"
DEFAULT_MAX_FILES = 200
DEFAULT_MAX_DIFF_BYTES = 262_144
DEFAULT_MAX_WORKSPACE_BYTES = 64 * 1024 * 1024


def _setting(ctx: HookContext, name: str, default: int) -> int:
    settings = getattr(ctx, "settings", None)
    getter = getattr(settings, "get", None)
    if not callable(getter):
        return default
    try:
        value = getter(f"plugins.hook-checkpoints.{name}", default)
    except Exception:  # noqa: BLE001 - a setting must not make the safety hook fail to run
        return default
    return value if isinstance(value, int) and not isinstance(value, bool) and value > 0 else default


class CheckpointHook:
    """Snapshot at turn start/end and immediately before state-mutating tools."""

    name: str = "checkpoints"
    event: HookEvent = HookEvent.PRE_TOOL_USE
    events: frozenset[HookEvent] = frozenset(
        [HookEvent.USER_PROMPT_SUBMIT, HookEvent.PRE_TOOL_USE, HookEvent.STOP]
    )
    matcher: str | None = r"(write_to_file|edit_file|run_command|create_artifact|present)"
    timeout_s: float = 5.0

    async def _snapshot(self, ctx: HookContext, label: str) -> SnapshotResult:
        repo = ShadowRepo(ctx.workspace)
        return await repo.snapshot_with_status(
            label,
            max_bytes=_setting(ctx, "max_workspace_bytes", DEFAULT_MAX_WORKSPACE_BYTES),
        )

    async def _record_checkpoint(self, ctx: HookContext, label: str, result: SnapshotResult) -> None:
        if not result.created or not result.sha:
            return
        payload = CheckpointCreated(sha=result.sha, label=label)
        content = f"Checkpoint created: {label}."
        metadata = {"sha": result.sha, "label": label, "workspace_bytes": result.workspace_bytes}
        try:
            await ctx.emit_record(payload, content=content, metadata=metadata)
        except Exception as err:  # noqa: BLE001 - a checkpoint is useful even if its audit record cannot be written
            logger.warning("Could not persist checkpoint.created: %s", err)

    async def _notice(self, ctx: HookContext, message: str, code: str) -> None:
        payload = SystemNotice(message=message, level="warn", code=code)
        try:
            await ctx.emit_record(payload, content=message, metadata={"level": "warn", "code": code})
        except Exception as err:  # noqa: BLE001 - still surface the warning live if the log is unavailable
            logger.warning("Could not persist system.notice: %s", err)
            try:
                ctx.emit("system.notice", payload)
            except Exception:
                logger.debug("Could not publish system.notice", exc_info=True)

    async def _safe_snapshot(self, ctx: HookContext, label: str) -> SnapshotResult | None:
        try:
            result = await self._snapshot(ctx, label)
            if result.skipped_reason:
                await self._notice(
                    ctx,
                    f"Checkpoint snapshot skipped: {result.skipped_reason}.",
                    "checkpoint.snapshot_skipped",
                )
                return result
            await self._record_checkpoint(ctx, label, result)
            return result
        except Exception as err:  # noqa: BLE001 - checkpoint failures never prevent the requested tool/turn
            logger.warning("Failed to snapshot workspace (%s): %s", label, err)
            await self._notice(
                ctx,
                f"Checkpoint snapshot failed ({label}); the turn will continue without a workspace diff.",
                "checkpoint.snapshot_failed",
            )
            return None

    async def execute(
        self, call: ToolCall | None, spec: ToolSpec | None, ctx: HookContext
    ) -> HookOutcome:
        """Snapshot before state-mutating tools; this does not change tool authorization."""
        if call is None or spec is None:
            return HookOutcome(action="continue")
        if spec.risk_class in (RiskClass.WRITE, RiskClass.EXEC, RiskClass.DESTRUCTIVE):
            call_id = getattr(call, "id", getattr(call, "call_id", "call"))
            await self._safe_snapshot(ctx, f"pre_{call.name}_{call_id}")
        return HookOutcome(action="continue")

    async def _finish_turn(self, ctx: HookContext) -> HookOutcome:
        turn_id = str(ctx.turn_id or "unknown")
        label = f"turn-end:{turn_id}"
        end = await self._safe_snapshot(ctx, label)
        state = ctx.turn_state if isinstance(ctx.turn_state, dict) else {}
        base = state.get(TURN_START_SHA_KEY)
        skipped = bool(state.get(TURN_START_SKIPPED_KEY)) or end is None or bool(end and end.skipped_reason)
        head = end.sha if end is not None else None
        if not base or not head:
            skipped = True
            summary = {
                "base": base,
                "head": head,
                "files": [],
                "added": 0,
                "removed": 0,
                "truncated": True,
                "snapshot_skipped": True,
                "total_files": 0,
            }
        else:
            try:
                repo = ShadowRepo(ctx.workspace)
                summary = await compute_changes(
                    repo,
                    base,
                    head,
                    max_files=_setting(ctx, "max_files", DEFAULT_MAX_FILES),
                )
                summary["snapshot_skipped"] = skipped
            except Exception as err:  # noqa: BLE001 - summarize failure is visible but must not fail the turn
                logger.warning("Could not compute workspace changes: %s", err)
                skipped = True
                summary = {
                    "base": base,
                    "head": head,
                    "files": [],
                    "added": 0,
                    "removed": 0,
                    "truncated": True,
                    "snapshot_skipped": True,
                    "total_files": 0,
                }
                await self._notice(
                    ctx,
                    "Workspace changes could not be summarized; checkpoint history remains available in Diffs.",
                    "workspace.diff_failed",
                )

        files_count = int(summary.get("total_files", len(summary.get("files", []))))
        if skipped:
            content = "Workspace changes could not be summarized because a checkpoint snapshot was skipped."
        elif files_count == 0:
            content = "No workspace changes this turn."
        else:
            count_text = f"{files_count}+" if summary.get("truncated") else str(files_count)
            content = f"{count_text} files changed · +{summary['added']} −{summary['removed']}"
        payload = WorkspaceChanges(
            turn_id=turn_id,
            base=summary.get("base"),
            head=summary.get("head"),
            files=summary.get("files", []),
            added=int(summary.get("added", 0)),
            removed=int(summary.get("removed", 0)),
            truncated=bool(summary.get("truncated")),
            snapshot_skipped=bool(summary.get("snapshot_skipped")),
        )
        metadata = {**summary, "turn_id": turn_id}
        try:
            await ctx.emit_record(payload, content=content, metadata=metadata)
        except Exception as err:  # noqa: BLE001 - never turn a terminal hook into a new turn failure
            logger.warning("Could not persist workspace.changes: %s", err)
        return HookOutcome(action="continue")

    async def on(self, event: HookEvent, payload: dict[str, Any], ctx: Any) -> HookOutcome:
        """Lifecycle hook entry point."""
        if event == HookEvent.PRE_TOOL_USE:
            return await self.execute(payload.get("call"), payload.get("spec"), ctx)

        turn_id = str(getattr(ctx, "turn_id", None) or "unknown")
        if event == HookEvent.USER_PROMPT_SUBMIT:
            label = f"turn-start:{turn_id}"
            result = await self._safe_snapshot(ctx, label)
            state = ctx.turn_state if isinstance(ctx.turn_state, dict) else None
            if state is not None:
                state[TURN_START_SHA_KEY] = result.sha if result is not None else None
                state[TURN_START_SKIPPED_KEY] = bool(
                    result is None or result.skipped_reason
                )
            return HookOutcome(action="continue")
        if event == HookEvent.STOP:
            return await self._finish_turn(ctx)
        return HookOutcome(action="continue")
