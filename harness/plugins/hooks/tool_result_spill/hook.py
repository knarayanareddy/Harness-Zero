"""Spill guard: copy an oversized tool result into the session directory before anything truncates it.

Order 800 — deliberately *before* the pruner (900), so the pruner can point at the file instead of
throwing content away. Truncation in the kernel is destructive by design; this hook is what makes it
recoverable: the model keeps being told "the full thing is here, go read it".
"""

from __future__ import annotations

import logging
import re
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from harness.sdk.types import HookEvent, HookOutcome, TextBlock, ToolResult

logger = logging.getLogger(__name__)

#: The exact string the model reads (WS-03 §1). Relative to the workspace so `view_file` accepts it.
SPILL_NOTE = (
    "Full output saved to {relpath} ({n} chars). "
    "Use view_file with offset/limit to read more."
)

_SAFE = re.compile(r"[^A-Za-z0-9._-]")


@dataclass(frozen=True)
class SpillSettings:
    enabled: bool = True
    threshold_chars: int = 8192
    max_files_per_session: int = 200
    exempt_tools: tuple[str, ...] = ("view_file",)


class ToolResultSpillHook:
    """Writes oversized results to ``<workspace>/.harness/sessions/<sid>/tool-output/<call_id>.txt``."""

    name = "tool_result_spill"
    events = frozenset({HookEvent.TOOL_RESULT_FINALIZE})
    matcher: str | None = None
    timeout_s = 5.0
    order = 800

    def __init__(self, settings: SpillSettings, workspace: Path) -> None:
        self.settings = settings
        self.workspace = Path(workspace)

    async def on(self, event: HookEvent, payload: dict[str, Any], ctx: Any) -> HookOutcome:
        if not self.settings.enabled:
            return HookOutcome(action="continue")
        result = payload.get("result")
        call = payload.get("call")
        if not isinstance(result, ToolResult) or call is None:
            return HookOutcome(action="continue")  # nothing to guard (also what HookKit passes)
        text = result.output
        tool_name = getattr(call, "name", "") or ""
        if tool_name in self.settings.exempt_tools or len(text) <= self.settings.threshold_chars:
            return HookOutcome(action="continue")

        out_dir = self._session_dir(ctx.session_id)
        rel_id = _SAFE.sub("_", str(getattr(call, "id", "call"))) or "call"
        path = out_dir / f"{rel_id}.txt"
        try:
            out_dir.mkdir(parents=True, exist_ok=True)
            path.write_text(text, encoding="utf-8")
            self._enforce_cap(out_dir, keep=path)
        except OSError as err:  # a spool that cannot be written must not fail the tool call
            logger.warning("tool-result spill failed for %s: %s", tool_name, err)
            return HookOutcome(action="continue")

        rel = path.relative_to(self.workspace).as_posix()
        note = SPILL_NOTE.format(relpath=rel, n=len(text))
        # Appended, not substituted: the pruner that runs after us decides what gets shortened, and
        # it reuses `spill_path` so the model sees the path exactly once.
        return HookOutcome(
            action="mutate",
            result=ToolResult(
                content=[*list(result.content), TextBlock(text=f"\n\n{note}")],
                is_error=result.is_error,
                metadata={
                    **dict(result.metadata),
                    "spill_path": str(path),
                    "spill_relpath": rel,
                    "spill_chars": len(text),
                },
            ),
        )

    def _session_dir(self, session_id: str) -> Path:
        return self.workspace / ".harness" / "sessions" / (session_id or "default") / "tool-output"

    def _enforce_cap(self, out_dir: Path, keep: Path) -> None:
        """Keeps the spool bounded, oldest first, and never evicts the file just written.

        "Oldest" is mtime with the name as tie-break: several spills in the same millisecond are the
        normal case, and without the tie-break the wrong file can be judged oldest (and, written
        first, the current one can be deleted again).
        """
        cap = int(self.settings.max_files_per_session or 0)
        if cap <= 0:
            return

        def stamp(p: Path) -> tuple[float, str]:
            try:
                return (p.stat().st_mtime, p.name)
            except OSError:
                return (0.0, p.name)

        others = [p for p in out_dir.glob("*.txt") if p != keep]
        for stale in sorted(others, key=stamp)[: max(0, len(others) - cap + 1)]:
            try:
                stale.unlink()
            except OSError:
                pass
