"""
Compaction service and token estimator.
Fulfills KRN-07 (§5.5) and absorbs LOOP-02 and CONF-04.
"""
from __future__ import annotations

import json
import logging
import time
from collections.abc import Awaitable, Callable
from typing import Any

from harness.kernel.bus import EventBus
from harness.kernel.registry import PluginRegistry
from harness.kernel.settings import SettingsStore
from harness.sdk.events import Event
from harness.sdk.types import (
    ContentBlock,
    HookEvent,
    ImageBlock,
    Message,
    ModelRequest,
    Role,
    TextBlock,
    ThinkingBlock,
    ToolResultBlock,
    ToolUseBlock,
)

logger = logging.getLogger(__name__)

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

    if isinstance(item, TextBlock):
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
                    data={"estimated_tokens": total_tokens},
                )
            )

        # Run PRE_COMPACT hooks
        if self.registry:
            from harness.kernel.context import HookContextImpl

            hook_ctx = HookContextImpl(
                session_id=session_id,
                workspace=self.registry.workspace,
            )
            for hook, _ in self.registry.hooks:
                if hook.event == HookEvent.PRE_COMPACT:
                    try:
                        await hook.execute(None, None, hook_ctx)
                    except Exception as err:
                        logger.warning(f"PRE_COMPACT hook '{hook.name}' failed: {err}")

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

        summary = await summarize_fn(COMPACTION_SYSTEM_PROMPT, conversation_str)

        # Enforce that all 4 required sections exist
        required_headers = ("## Goal", "## Decisions", "## State of work", "## Open items")
        for header in required_headers:
            if header not in summary:
                # If model missed a header, append an empty section so invariants always hold
                summary += f"\n\n{header}\nNone recorded."

        duration_ms = (time.time() - start_time) * 1000

        if self.bus:
            self.bus.publish(
                Event(
                    type="compaction.completed",
                    session_id=session_id,
                    data={
                        "summary_tokens": estimate_tokens(summary),
                        "duration_ms": duration_ms,
                    },
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
                system=[TextBlock(text=system_prompt)],
                messages=[Message(role=Role.USER, content=[TextBlock(text=user_prompt)])],
                tools=[],
            )
            chunks = []
            async for ev in provider.generate(mreq):
                if isinstance(ev, TextDelta):
                    chunks.append(ev.text)
            return "".join(chunks)

        msgs: list[Message] = []
        for b in blocks:
            if isinstance(b, Message):
                msgs.append(b)
            elif isinstance(b, TextBlock):
                msgs.append(Message(role=Role.USER, content=[b]))

        await self.compact(session_id, msgs, _summarize)
        return True

