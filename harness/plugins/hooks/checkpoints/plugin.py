"""Checkpoint plugin entry point: hooks, tools, workspace commands, and owned panel contribution."""
from __future__ import annotations

import logging
from typing import Any

from harness.sdk.context import PluginCommandContext, PluginContext
from harness.sdk.events import CheckpointCreated, CheckpointRestored, SystemNotice
from harness.sdk.manifest import PluginManifest

from .changes import changed_file_count, diff_file, validate_checkpoint_sha
from .hook import DEFAULT_MAX_DIFF_BYTES, DEFAULT_MAX_WORKSPACE_BYTES, CheckpointHook
from .shadow_repo import ShadowRepo
from .tools import CHECKPOINT_RESTORE_SPEC, handle_checkpoint_restore

logger = logging.getLogger(__name__)

_SESSION_SCHEMA = {
    "type": "object",
    "properties": {"session_id": {"type": "string"}},
    "required": ["session_id"],
    "additionalProperties": False,
}
_DIFF_SCHEMA = {
    "type": "object",
    "properties": {
        "path": {"type": "string"},
        "base": {"type": "string", "pattern": "^[0-9a-f]{40}$"},
        "head": {"type": "string", "pattern": "^[0-9a-f]{40}$"},
    },
    "required": ["path", "base", "head"],
    "additionalProperties": False,
}
_RESTORE_SCHEMA = {
    "type": "object",
    "properties": {
        "session_id": {"type": "string"},
        "sha": {"type": "string", "pattern": "^[0-9a-f]{40}$"},
    },
    "required": ["session_id", "sha"],
    "additionalProperties": False,
}


def _positive_setting(view: Any, name: str, default: int) -> int:
    try:
        value = view.get(name, default)
    except Exception:  # noqa: BLE001 - a malformed setting cannot widen a diff or disable the size guard
        return default
    return value if isinstance(value, int) and not isinstance(value, bool) and value > 0 else default


class CheckpointsPlugin:
    """Owns shadow snapshots, safe diff commands, and explicit restore commands."""

    def __init__(self, manifest: PluginManifest | None = None) -> None:
        self.manifest = manifest
        self.ctx: PluginContext | None = None
        self.hook: CheckpointHook | None = None

    async def setup(self, ctx: PluginContext) -> None:
        self.ctx = ctx
        self.hook = CheckpointHook()
        ctx.register_hook(self.hook)
        ctx.register_tool(CHECKPOINT_RESTORE_SPEC, handle_checkpoint_restore)
        ctx.register_command("changes", self._changes, args_schema=_SESSION_SCHEMA)
        ctx.register_command("diff", self._diff, args_schema=_DIFF_SCHEMA)
        ctx.register_command("restore", self._restore, args_schema=_RESTORE_SCHEMA)

    async def _changes(
        self,
        *,
        session_id: str,
        _context: PluginCommandContext,
    ) -> dict[str, list[dict[str, Any]]]:
        """Read a session's durable workspace-change and restore records."""
        records = await _context.read_records(session_id)
        return {
            "changes": [row for row in records if row.get("kind") == "workspace.changes"],
            "restored": [row for row in records if row.get("kind") == "checkpoint.restored"],
        }

    async def _diff(
        self,
        *,
        path: str,
        base: str,
        head: str,
        _context: PluginCommandContext,
    ) -> dict[str, Any]:
        """Fetch a bounded unified diff for one workspace file between two snapshots."""
        if self.ctx is None:
            raise RuntimeError("Checkpoint plugin is not initialized")
        repo = ShadowRepo(_context.workspace)
        return await diff_file(
            repo,
            base,
            head,
            path,
            max_bytes=_positive_setting(self.ctx.settings, "max_diff_bytes", DEFAULT_MAX_DIFF_BYTES),
        )

    async def _restore(
        self,
        *,
        session_id: str,
        sha: str,
        _context: PluginCommandContext,
    ) -> dict[str, Any]:
        """Restore the session workspace to a snapshot after explicit user approval."""
        if self.ctx is None:
            raise RuntimeError("Checkpoint plugin is not initialized")
        try:
            target = validate_checkpoint_sha(sha)
        except ValueError as err:
            return {"status": "invalid", "reason": str(err)}

        async with _context.session_lock(session_id):
            repo = ShadowRepo(_context.workspace)
            if not await repo.has_commit(target):
                return {"status": "not_found", "reason": "Checkpoint is not in this workspace's shadow repository."}

            # Preserve the current workspace before asking to restore. This snapshot is private to the shadow
            # repo, bounded by the same guard as turn snapshots, and does not approve the destructive reset.
            pre_restore_label = "pre-restore"
            snapshot = await repo.snapshot_with_status(
                pre_restore_label,
                max_bytes=_positive_setting(
                    self.ctx.settings,
                    "max_workspace_bytes",
                    DEFAULT_MAX_WORKSPACE_BYTES,
                ),
            )
            if snapshot.skipped_reason:
                message = f"Restore was not run: {snapshot.skipped_reason}."
                try:
                    await _context.emit_record(
                        session_id=session_id,
                        turn_id=None,
                        payload=SystemNotice(message=message, level="warn", code="checkpoint.snapshot_skipped"),
                        content=message,
                        metadata={"level": "warn", "code": "checkpoint.snapshot_skipped"},
                    )
                except Exception as err:  # noqa: BLE001 - the command still fails closed
                    logger.warning("Could not persist restore size-guard notice: %s", err)
                return {"status": "blocked", "reason": message}

            if snapshot.created and snapshot.sha:
                checkpoint = CheckpointCreated(sha=snapshot.sha, label=pre_restore_label)
                try:
                    await _context.emit_record(
                        session_id=session_id,
                        turn_id=None,
                        payload=checkpoint,
                        content=f"Checkpoint created: {checkpoint.label}.",
                        metadata={
                            "sha": snapshot.sha,
                            "label": checkpoint.label,
                            "workspace_bytes": snapshot.workspace_bytes,
                        },
                    )
                except Exception as err:  # noqa: BLE001 - the snapshot itself remains useful
                    logger.warning("Could not persist pre-restore checkpoint: %s", err)

            current = snapshot.sha or await repo.head()
            files_touched = await changed_file_count(repo, current, target) if current else 0
            if current == target:
                return {"status": "unchanged", "sha": target, "files_touched": 0}

            checkpoints = await repo.list_checkpoints(limit=1000)
            label = next((row["label"] for row in checkpoints if row["sha"] == target), target[:8])
            approved, reason = await _context.request_approval(
                session_id=session_id,
                kind="checkpoint_restore",
                summary=f"Restore the workspace to checkpoint {target[:8]}?",
                details={
                    "checkpoint_id": target,
                    "label": label,
                    "files_touched": files_touched,
                    "operation": "restore workspace files",
                },
                destructive=True,
            )
            if not approved:
                blocked = "destructive action blocked" in reason
                return {
                    "status": "blocked" if blocked else "denied",
                    "reason": reason,
                    "sha": target,
                    "files_touched": files_touched,
                }

            try:
                await repo.restore(target)
            except Exception as err:  # noqa: BLE001 - report restore errors without disguising partial state
                logger.exception("Checkpoint restore failed")
                return {"status": "error", "reason": str(err), "sha": target}

            payload = CheckpointRestored(sha=target, label=label, files_touched=files_touched)
            content = f"Workspace restored to checkpoint '{label}' ({files_touched} file(s) changed)."
            try:
                await _context.emit_record(
                    session_id=session_id,
                    turn_id=None,
                    payload=payload,
                    content=content,
                    metadata={"sha": target, "label": label, "files_touched": files_touched},
                )
            except Exception as err:  # noqa: BLE001 - the restore succeeded; keep its return truthful
                logger.warning("Could not persist checkpoint.restored: %s", err)
            return {
                "status": "restored",
                "sha": target,
                "label": label,
                "files_touched": files_touched,
            }

    async def teardown(self) -> None:
        self.ctx = None
        self.hook = None
