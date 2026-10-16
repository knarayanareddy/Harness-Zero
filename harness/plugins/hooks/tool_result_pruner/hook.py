"""Pruner: keep the head *and the tail* of an oversized tool result, instead of letting the kernel
truncate to head-only.

This exists because of a specific failure mode: `pytest` (and most build/test tooling) prints its
diagnosis last. The kernel's backstop keeps the first `kernel.limits.tool_result_bytes` and appends
`[TRUNCATED DUE TO SIZE LIMIT]`, so on a big run the model reads 400 lines of warnings and never
sees `FAILED tests/... :: assertion`. A guard that runs at `TOOL_RESULT_FINALIZE` — after the handler,
before truncation — is the only place that can decide what the model reads (WS-03 §1).

Lives in a plugin, not the kernel, on purpose: the kernel owns the *hard* cap (a tool must not be able
to blow the context window), while "which half is worth reading" is a judgement call that different
harnesses answer differently — and it must be switchable off without a kernel change.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass

from harness.sdk.events import ToolResultPruned
from harness.sdk.types import HookEvent, HookOutcome, TextBlock, ToolResult

logger = logging.getLogger(__name__)

#: The exact shape the model sees when content was removed (WS-03 §1). `suffix` carries the spill path.
PRUNE_NOTICE = "\n\n[... {omitted} chars omitted ({lines} lines){suffix} ...]\n\n"


@dataclass(frozen=True)
class PrunerSettings:
    enabled: bool = True
    threshold_chars: int = 8192
    head_chars: int = 4096
    tail_chars: int = 1024
    exempt_tools: tuple[str, ...] = ("view_file",)


class ToolResultPrunerHook:
    name = "tool_result_pruner"
    events = frozenset({HookEvent.TOOL_RESULT_FINALIZE})
    matcher: str | None = None
    timeout_s = 5.0
    order = 900  # after spill (800): the notice can then advertise the file the spill guard wrote

    def __init__(self, settings: PrunerSettings) -> None:
        self.settings = settings

    async def on(self, event: HookEvent, payload: dict, ctx) -> HookOutcome:
        cfg = self.settings
        result = payload.get("result")
        call = payload.get("call")
        if not cfg.enabled or not isinstance(result, ToolResult) or call is None:
            return HookOutcome(action="continue")  # HookKit calls us with an empty payload
        if getattr(call, "name", "") in cfg.exempt_tools:
            return HookOutcome(action="continue")

        text = result.output
        # The spill guard (order 800) appends its note after the body, so `spill_chars` is exactly
        # where the original output ends. Split it back off, or the note occupies the tail window and
        # the interesting last lines are pushed out of it.
        spill_chars = int(result.metadata.get("spill_chars") or 0)
        note = ""
        if 0 < spill_chars <= len(text):
            text, note = text[:spill_chars], text[spill_chars:]
        if len(text) <= cfg.threshold_chars:
            return HookOutcome(action="continue")
        if cfg.head_chars + cfg.tail_chars >= len(text):
            return HookOutcome(action="continue")

        head = self._snap_to_line_start(text, cfg.head_chars, tail=False)
        tail = self._snap_to_line_start(text, len(text) - cfg.tail_chars, tail=True)
        # Everything between the two kept pieces is what the model will not read; say so exactly.
        middle = text[len(head) : len(text) - len(tail)]
        omitted = len(middle)
        if omitted <= 0:
            return HookOutcome(action="continue")
        lines = middle.count("\n")

        spill = result.metadata.get("spill_relpath") or result.metadata.get("spill_path") or ""
        suffix = f"; full output: {spill}" if spill else ""
        notice = PRUNE_NOTICE.format(omitted=omitted, lines=lines, suffix=suffix)

        if ctx is not None and hasattr(ctx, "emit"):
            ctx.emit(
                ToolResultPruned.EVENT_TYPE,
                ToolResultPruned(
                    call_id=str(getattr(call, "id", "")),
                    name=str(getattr(call, "name", "")),
                    original_chars=len(text),
                    shown_chars=len(head) + len(tail),
                    spill_path=str(spill),
                ),
            )

        return HookOutcome(
            action="mutate",
            result=ToolResult(
                content=[TextBlock(text=head + notice + tail + note)],
                is_error=result.is_error,
                metadata={
                    **dict(result.metadata),
                    "pruned_chars": omitted,
                    "pruned_lines": lines,
                    "pruned": True,
                },
            ),
        )

    @staticmethod
    def _snap_to_line_start(text: str, index: int, *, tail: bool) -> str:
        """Cut on a line boundary: a half-line of a stack frame is noise, the whole line is cheap.

        Only snaps when the snapped version is still smaller than the budget for the head (and, for
        the tail, starts at a line boundary at or after `index`) so the budget stays a budget.
        """
        if index <= 0:
            return text if tail else ""
        if index >= len(text):
            return "" if tail else text
        if tail:
            nl = text.find("\n", index)
            return text[nl + 1 :] if nl != -1 and nl + 1 < len(text) else text[index:]
        nl = text.rfind("\n", 0, index)
        return text[: nl + 1] if nl != -1 else text[:index]
