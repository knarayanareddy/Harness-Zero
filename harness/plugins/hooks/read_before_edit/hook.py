"""Read-before-edit: refuse a surgical edit of a file the model has not read (or has since lost sync with).

`edit_file` matches an exact SEARCH block. Without a read, the model is guessing at the file's
current bytes, and the guess fails — or worse, succeeds against content it never intended to touch.
An external change (another agent, `git checkout`, a `sed` the model itself ran) has the same effect:
the copy in the transcript is no longer the file on disk.

The ledger is *derived from the transcript*, not kept in plugin memory: a restart resumes the session
with its history, and the guard must still know that `app.py` was read at step 4. Deriving it here is
also what keeps the guard honest about failed edits — a `write_to_file` that errored did not teach the
model anything, so it does not count.
"""

from __future__ import annotations

import fnmatch
import logging
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from harness.sdk.types import HookEvent, HookOutcome

logger = logging.getLogger(__name__)

#: The exact strings the model reads (WS-03 §1). `detail` is the whole middle of the sentence, so
#: the two cases (never read / read then changed) stay one template instead of string surgery.
BLOCK_MESSAGE = "{tool} blocked: {detail} Read it first."
DETAIL_UNREAD = "{path} has not been read in this session."
DETAIL_STALE = (
    "{path} has not been read in this session (or changed since it was read at step {step})."
)
WARN_MESSAGE = (
    "Warning: {tool} was applied to {path}, which has not been read in this session (or changed "
    "since it was read at step {step}). Read it with view_file before editing next time."
)

#: mtime is compared with a slack, because a filesystem that stamps whole seconds would otherwise
#: report a read as stale when the edit happens in the same second.
MTIME_SLACK_S = 1.0


@dataclass(frozen=True)
class ReadBeforeEditSettings:
    mode: str = "block"  # block | warn | off ('off' is the switch: see the note in plugin.py)
    exempt_globs: tuple[str, ...] = ()
    guarded_tools: tuple[str, ...] = ("edit_file", "write_to_file")
    read_tools: tuple[str, ...] = ("view_file",)


class ReadBeforeEditHook:
    name = "read_before_edit"
    events = frozenset({HookEvent.PRE_TOOL_USE})
    matcher: str | None = None
    timeout_s = 5.0
    order = 500

    def __init__(self, settings: ReadBeforeEditSettings, workspace: Path) -> None:
        self.settings = settings
        self.workspace = Path(workspace)
        # Reads satisfy the check; successful writes/edits refresh it (the model just produced the
        # content, so it does not need to read what it wrote).
        self._counted = frozenset(settings.read_tools) | frozenset(settings.guarded_tools)

    async def on(self, event: HookEvent, payload: dict[str, Any], ctx: Any) -> HookOutcome:
        cfg = self.settings
        if cfg.mode == "off":
            return HookOutcome(action="continue")
        call = payload.get("call")
        if call is None:
            return HookOutcome(action="continue")  # HookKit calls us with an empty payload
        if getattr(call, "name", "") not in cfg.guarded_tools:
            return HookOutcome(action="continue")

        raw_path = str((getattr(call, "arguments", None) or {}).get("file_path") or "")
        if not raw_path:
            return HookOutcome(action="continue")
        rel = self._relative(raw_path)
        if any(fnmatch.fnmatch(rel, pat) for pat in cfg.exempt_globs):
            return HookOutcome(action="continue")

        target = (self.workspace / rel).resolve() if not Path(raw_path).is_absolute() else Path(raw_path)
        if not target.exists() or target.is_dir():
            # Creating a file needs no prior read; only overwriting one the model should know does.
            return HookOutcome(action="continue")

        seen = self._ledger(ctx).get(str(target.resolve()) if target.is_absolute() else str((self.workspace / rel).resolve()))
        if seen is not None:
            step, ts = seen
            try:
                if target.stat().st_mtime <= ts + MTIME_SLACK_S:
                    return HookOutcome(action="continue")
            except OSError:
                return HookOutcome(action="continue")
        else:
            step = -1

        if seen is None:
            detail = DETAIL_UNREAD.format(path=rel)
        else:
            detail = DETAIL_STALE.format(path=rel, step=max(step, 0))

        if cfg.mode == "warn":
            return HookOutcome(
                action="continue",
                additional_context=WARN_MESSAGE.format(tool=call.name, path=rel, step=max(step, 0)),
            )
        return HookOutcome(
            action="block",
            reason=BLOCK_MESSAGE.format(tool=call.name, detail=detail),
        )

    def _relative(self, raw_path: str) -> str:
        p = Path(raw_path)
        try:
            return p.resolve().relative_to(self.workspace).as_posix() if p.is_absolute() else p.as_posix()
        except ValueError:
            return p.name

    def _key(self, raw_path: str) -> str:
        path = Path(raw_path)
        target = path if path.is_absolute() else self.workspace / path
        try:
            return str(target.resolve())
        except OSError:  # a path that cannot be resolved is not one we can vouch for
            return str(target)

    def _ledger(self, ctx: Any) -> dict[str, tuple[int, float]]:
        """path -> (step index, timestamp) of the last moment the model definitely saw this file.

        Derived from the transcript instead of kept in plugin state, because "what the model has seen"
        *is* the transcript — and a resume must not forget that `app.py` was read at step 4.
        A call counts only once its result exists: the edit being attempted in this very step is
        evidence of an edit, not of a read.
        """
        state = getattr(ctx, "turn_state", None)
        raw = state.get("records") if isinstance(state, dict) else None
        records: list[dict[str, Any]] = list(raw) if isinstance(raw, list) else []
        ledger: dict[str, tuple[int, float]] = {}
        pending: dict[str, str] = {}  # tool_use_id -> path, awaiting its result
        for index, rec in enumerate(records):
            ts = float(rec.get("timestamp") or 0.0)
            # Calls first, then results: the kernel writes the assistant turn and the tool result into
            # separate records (so a result is matched one step later), and handling calls first also
            # copes with a transcript that stores both in one record.
            for call in rec.get("tool_calls", []) or []:
                if call.get("name") not in self._counted:
                    continue
                raw = str((call.get("arguments") or {}).get("file_path") or "")
                if raw:
                    pending[str(call.get("id"))] = self._key(raw)
            for res in rec.get("tool_results", []) or []:
                key = pending.pop(res.get("tool_use_id"), None)
                if key is None or res.get("is_error"):
                    continue  # a failed read or edit taught the model nothing about the content
                ledger[key] = (index, ts)
        return ledger
