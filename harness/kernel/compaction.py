"""
Compaction service and token estimator.
Fulfills KRN-07 (§5.5) and absorbs LOOP-02 and CONF-04.
"""
from __future__ import annotations

import asyncio
import json
import logging
import time
from collections.abc import Awaitable, Callable
from typing import Any

from harness.kernel.bus import EventBus
from harness.kernel.registry import PluginRegistry
from harness.kernel.settings import SettingsStore
from harness.sdk.errors import HarnessError, SpendCapExceeded
from harness.sdk.events import CompactionCompleted, CompactionStarted, Event
from harness.sdk.types import (
    ContentBlock,
    ContextBlock,
    Done,
    HookEvent,
    ImageBlock,
    Message,
    ModelParams,
    ModelRequest,
    Role,
    Stability,
    TextBlock,
    ThinkingBlock,
    ToolResultBlock,
    ToolUseBlock,
    Usage,
    UsageEvent,
)

logger = logging.getLogger(__name__)


class CompactionError(HarnessError):
    """Compaction failed before a usable summary was produced."""

COMPACTION_SYSTEM_PROMPT = """You are an expert technical assistant. Summarize the conversation and work completed so far into a concise, high-density structured summary.
You must strictly format your response using these four Markdown sections:

## Goal
Summarize the user's primary objectives, requirements, and intended outcomes.

## Decisions
List all key architectural, technical, design, and implementation decisions made.

## State of work
Detail what has been implemented, edited, tested, verified, or created.

## Open items
List all remaining tasks, pending steps, and unresolved questions."""


def estimate_tokens(item: str | ContentBlock | Message | list[Any]) -> int:
    """
    Estimates tokens using the standard len // 4 heuristic.
    Guarantees at least 1 token for non-empty content.
    """
    if isinstance(item, str):
        return max(1, len(item) // 4) if item else 0

    if isinstance(item, (TextBlock, ContextBlock)):
        return max(1, len(item.text) // 4) if item.text else 0

    if isinstance(item, ThinkingBlock):
        return max(1, len(item.text) // 4) if item.text else 0

    if isinstance(item, ImageBlock):
        # Image token estimate placeholder (standard fixed token budget per image)
        return 1600

    if isinstance(item, ToolUseBlock):
        raw = item.name + json.dumps(item.input)
        return max(1, len(raw) // 4)

    if isinstance(item, ToolResultBlock):
        return sum(estimate_tokens(c) for c in item.content)

    if isinstance(item, Message):
        return sum(estimate_tokens(c) for c in item.content)

    if isinstance(item, list):
        return sum(estimate_tokens(x) for x in item)

    return 0


def estimate_request_tokens(
    system: list[Any] | ModelRequest, messages: list[Message] | None = None
) -> int:
    """Estimates total tokens across system prompt and message history or ModelRequest."""
    if hasattr(system, "system") and hasattr(system, "messages"):
        return estimate_tokens(system.system) + estimate_tokens(system.messages)
    return estimate_tokens(system) + estimate_tokens(messages or [])


class Compactor:
    """
    Checks token thresholds against budget and performs LLM summarization
    under PRE_COMPACT hooks and fixed structure requirements.
    """

    def __init__(
        self,
        settings: SettingsStore,
        bus: EventBus | None = None,
        registry: PluginRegistry | None = None,
    ):
        self.settings = settings
        self.bus = bus
        self.registry = registry

    def needs_compaction(
        self, system: list[TextBlock], messages: list[Message]
    ) -> bool:
        """
        Returns True if total estimated tokens exceed 80% of context budget.
        """
        budget = self.settings.get("kernel.context.budget_tokens", 60000)
        threshold = budget * 0.8
        tokens = estimate_request_tokens(system, messages)
        return tokens > threshold

    async def compact(
        self,
        session_id: str,
        messages: list[Message],
        summarize_fn: Callable[[str, str], Awaitable[str]],
        *,
        on_summary: Callable[[str], Awaitable[dict[str, Any]]] | None = None,
        hook_ctx: Any = None,
    ) -> str:
        """
        Performs compaction:
        1. Emits compaction.started event.
        2. Executes PRE_COMPACT hooks.
        3. Calls summarize_fn with fixed prompt and serialized conversation.
        4. Validates structured sections.
        5. Emits compaction.completed event.
        """
        total_tokens = estimate_tokens(messages)
        start_time = time.time()

        if self.bus:
            self.bus.publish(
                Event(
                    type="compaction.started",
                    session_id=session_id,
                    data=CompactionStarted(estimated_tokens=total_tokens),
                )
            )

        # Run PRE_COMPACT hooks
        if self.registry:
            from harness.kernel.context import HookContextImpl

            hook_ctx = hook_ctx or HookContextImpl(
                session_id=session_id,
                workspace=self.registry.workspace,
                settings=self.settings,
                bus=self.bus,
            )
            for hook in self.registry.get_hooks_for_event(HookEvent.PRE_COMPACT):
                try:
                    outcome = await asyncio.wait_for(
                        hook.on(HookEvent.PRE_COMPACT, {"estimated_tokens": total_tokens}, hook_ctx),
                        timeout=hook.timeout_s,
                    )
                    if outcome.action == "block":
                        raise CompactionError(f"PRE_COMPACT hook '{hook.name}' blocked: {outcome.reason}")
                except CompactionError:
                    raise
                except Exception as err:
                    raise CompactionError(f"PRE_COMPACT hook '{hook.name}' failed: {err or type(err).__name__}") from err

        # Serialize conversation for summarization prompt
        conv_lines: list[str] = []
        for msg in messages:
            role_str = msg.role.value if hasattr(msg.role, "value") else str(msg.role)
            for block in msg.content:
                if isinstance(block, TextBlock):
                    conv_lines.append(f"[{role_str.upper()}]: {block.text}")
                elif isinstance(block, ToolUseBlock):
                    conv_lines.append(f"[TOOL_CALL]: {block.name}({json.dumps(block.input)})")
                elif isinstance(block, ToolResultBlock):
                    text = "".join(c.text for c in block.content if isinstance(c, TextBlock))
                    conv_lines.append(f"[TOOL_RESULT]: {text}")

        conversation_str = "\n".join(conv_lines)

        try:
            async with asyncio.timeout(self.settings.get("kernel.request_timeout_s", 60.0)):
                summary = await summarize_fn(COMPACTION_SYSTEM_PROMPT, conversation_str)
            if not isinstance(summary, str) or not summary.strip():
                raise CompactionError("Summarizer returned no usable text")
        except (CompactionError, SpendCapExceeded):
            raise
        except Exception as err:
            raise CompactionError(f"Summarization failed: {err or type(err).__name__}") from err

        # Missing sections are a failed summary, not facts we may fabricate.
        required_headers = ("## Goal", "## Decisions", "## State of work", "## Open items")
        for header in required_headers:
            if header not in summary.splitlines():
                raise CompactionError(f"Summarizer omitted required section: {header}")

        record = await on_summary(summary) if on_summary is not None else None
        duration_ms = (time.time() - start_time) * 1000

        if self.bus and record is not None:
            self.bus.publish(
                Event(
                    type="compaction.completed",
                    session_id=session_id,
                    data=CompactionCompleted(
                        summary_tokens=estimate_tokens(summary),
                        duration_ms=duration_ms,
                    ),
                    record=record,
                )
            )

        return summary

    async def maybe_compact(
        self,
        session_id: str,
        blocks: list[Any],
        records: list[Any] | None = None,
        provider: Any = None,
        model: str = "",
        bus: EventBus | None = None,
        log: Any = None,
        hook_ctx: Any = None,
        on_usage: Callable[[Usage], Awaitable[None]] | None = None,
    ) -> bool:
        """
        Checks if estimated tokens exceed threshold and performs compaction if needed.
        Returns True if compaction was performed, False otherwise.
        """
        budget = self.settings.get("kernel.context.budget_tokens", 60000)
        threshold = budget * 0.8
        tokens = estimate_tokens(blocks)
        if tokens <= threshold:
            return False

        if not provider:
            return False

        async def _summarize(system_prompt: str, user_prompt: str) -> str:
            from harness.sdk.types import ModelRequest, Role, TextBlock, TextDelta
            mreq = ModelRequest(
                model=model,
                system=[ContextBlock(id="compaction_instructions", text=system_prompt, stability=Stability.STATIC)],
                messages=[Message(role=Role.USER, content=[TextBlock(text=user_prompt)])],
                tools=[],
                params=ModelParams(max_tokens=max(1, budget // 4)),
            )
            chunks = []
            completed = False
            usage = Usage()
            async for ev in provider.generate(mreq):
                if isinstance(ev, TextDelta):
                    chunks.append(ev.text)
                elif isinstance(ev, Done):
                    completed = ev.stop_reason == "end_turn"
                elif isinstance(ev, UsageEvent):
                    usage = usage + ev.usage
            if on_usage is not None:
                await on_usage(usage)
            if not completed:
                raise CompactionError("Summarizer stream ended without successful completion")
            return "".join(chunks)

        msgs: list[Message] = []
        for b in blocks:
            if isinstance(b, Message):
                msgs.append(b)
            elif isinstance(b, TextBlock):
                msgs.append(Message(role=Role.USER, content=[b]))

        on_summary = None
        if log is not None:
            from harness.kernel.context import derive_messages
            from harness.kernel.transcript import record_at_step

            snapshot = list(log.records)
            keep_from = len(snapshot)
            prompt_record = None
            for idx, record in enumerate(snapshot):
                if record.get("kind") == "user.message":
                    keep_from = idx
                    prompt_record = record
            system = [block for block in blocks if isinstance(block, (TextBlock, ContextBlock))]
            summarized_prompt_step = None
            # A first/oversized text prompt has no older prefix that can free
            # enough room. Summarize it explicitly, retain its raw log record,
            # and tell assembly not to reinsert those already-covered bytes.
            dangling_calls = log.dangling_tool_calls()
            retained_tokens = estimate_request_tokens(system, derive_messages(snapshot[keep_from:]))
            if (not dangling_calls and prompt_record is not None and not prompt_record.get("media")
                    and (not keep_from or retained_tokens > budget * 0.75)):
                keep_from = len(snapshot)
                summarized_prompt_step = prompt_record["step_index"]
            # Never hide an unresolved call on the summarized side of the boundary.
            for dangling in dangling_calls:
                for idx, record in enumerate(snapshot):
                    if record.get("step_index") == dangling["record_index"]:
                        keep_from = min(keep_from, idx)
            if not keep_from:
                raise CompactionError("No closed history prefix can be compacted within this budget")
            covered = snapshot[keep_from - 1]["step_index"]
            msgs = derive_messages(snapshot[:keep_from])

            async def persist(summary: str) -> dict[str, Any]:
                record = {"kind": "compaction.summary", "role": "system", "content": summary,
                          "metadata": {"compaction_summary": True, "covers_through_step": covered}}
                if summarized_prompt_step is not None:
                    record["metadata"]["summarized_prompt_step"] = summarized_prompt_step
                projected = derive_messages(list(log.records) + [record])
                if estimate_request_tokens(system, projected) > budget:
                    raise CompactionError("Summary and retained conversation exceed the context budget")
                step = await log.append(record)
                return record_at_step(log.records, step)

            on_summary = persist

        await self.compact(session_id, msgs, _summarize, on_summary=on_summary, hook_ctx=hook_ctx)
        return True
