"""Repeat-call reminder: tell the model when it is burning a step on the same call again.

The failure this guards (DeepSeek Harness called it out in their post-mortem, and our own replay
scenarios show the same shape) is a loop that re-runs a command, gets the same output, and keeps
going — because nothing in the transcript says "you have already seen this". Counting identical
(name, arguments) pairs inside one turn and appending a note is the cheapest possible loop brake;
`block_after` turns it into a hard stop for the pathological case.

State lives in `hook_ctx.turn_state`, not module globals: two sessions must not share counters and a
restart must not resurrect a turn that no longer exists.
"""

from __future__ import annotations

import hashlib
import json
import logging
from dataclasses import dataclass
from typing import Any

from harness.sdk.events import ToolRepeatDetected
from harness.sdk.types import HookEvent, HookOutcome

logger = logging.getLogger(__name__)

#: The exact string the model reads (WS-03 §1).
REPEAT_NOTE = (
    "Note: this is the {ordinal} identical call to {tool} with the same arguments in this turn. "
    "The result has not changed. Consider a different approach or explain why repeating is needed."
)


@dataclass(frozen=True)
class RepeatSettings:
    enabled: bool = True
    repeat_after: int = 3
    block_after: int = 0  # 0 = never block, only remind
    exempt_tools: tuple[str, ...] = ("manage_task", "view_file")


def ordinal(n: int) -> str:
    if 10 <= n % 100 <= 13:
        return f"{n}th"
    return f"{n}{ {1: 'st', 2: 'nd', 3: 'rd'}.get(n % 10, 'th') }"


class RepeatCallReminderHook:
    name = "repeat_call_reminder"
    events = frozenset({HookEvent.POST_TOOL_USE})
    matcher: str | None = None
    timeout_s = 5.0
    order = 500

    def __init__(self, settings: RepeatSettings) -> None:
        self.settings = settings

    async def on(self, event: HookEvent, payload: dict[str, Any], ctx: Any) -> HookOutcome:
        if not self.settings.enabled:
            return HookOutcome(action="continue")
        call = payload.get("call")
        result = payload.get("result")
        if call is None or result is None:
            return HookOutcome(action="continue")  # HookKit calls us with an empty payload
        state = getattr(ctx, "turn_state", None)
        if not isinstance(state, dict):
            # Without per-turn state there is no memory of the previous calls, and guessing from
            # process-global state is exactly the bug this plugin exists to avoid.
            return HookOutcome(action="continue")
        name = getattr(call, "name", "") or ""
        if name in self.settings.exempt_tools:
            return HookOutcome(action="continue")

        signature = json.dumps(
            {"name": name, "arguments": getattr(call, "arguments", {})},
            sort_keys=True,
            default=str,
        )
        key = hashlib.sha256(signature.encode("utf-8")).hexdigest()[:24]
        # An identical *call* is only a spin if the *result* is identical too — a `pytest` re-run
        # after an edit is productive even though the command text never changes.
        result_hash = hashlib.sha256(result.output.encode("utf-8")).hexdigest()[:16]

        ledger: dict[str, Any] = state.setdefault("repeat_calls", {})
        prior = ledger.get(key)
        count = (prior["count"] + 1) if prior and prior["hash"] == result_hash else 1
        ledger[key] = {"count": count, "hash": result_hash, "signature": signature}

        if count < self.settings.repeat_after:
            return HookOutcome(action="continue")

        note = REPEAT_NOTE.format(ordinal=ordinal(count), tool=name)
        blocked = self.settings.block_after > 0 and count >= self.settings.block_after
        if ctx is not None and hasattr(ctx, "emit"):
            ctx.emit(
                ToolRepeatDetected.EVENT_TYPE,
                ToolRepeatDetected(
                    call_id=str(getattr(call, "id", "")),
                    name=name,
                    count=count,
                    blocked=blocked,
                ),
            )
        if blocked:
            return HookOutcome(action="block", reason=note)
        return HookOutcome(action="continue", additional_context=f"\n\n{note}")
