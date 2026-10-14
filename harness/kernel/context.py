"""
ContextAssembler: parallel source collection, tier ordering, cache hints,
and prompt assembly for model requests.
Fulfills KRN-07 (§5.4) and absorbs LOOP-02 and CONF-04.
"""
from __future__ import annotations

import asyncio
import copy
import logging
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from harness.kernel.bus import EventBus
from harness.kernel.compaction import estimate_request_tokens, estimate_tokens
from harness.kernel.registry import PluginRegistry
from harness.kernel.settings import SettingsStore
from harness.sdk.contracts import ContextSource
from harness.sdk.events import Event
from harness.sdk.types import (
    Capability,
    ContentBlock,
    ContextBlock,
    ImageBlock,
    Message,
    ModelParams,
    ModelRequest,
    ProviderCaps,
    Role,
    Stability,
    TextBlock,
    ThinkingBlock,
    ToolResultBlock,
    ToolSpec,
    ToolUseBlock,
)

logger = logging.getLogger(__name__)

STABILITY_ORDER = {
    Stability.STATIC: 1,
    Stability.SESSION: 2,
    Stability.TURN: 3,
}


@dataclass
class HookContextImpl:
    """Concrete implementation satisfying the HookContext protocol."""
    session_id: str
    workspace: Path


@dataclass
class PolicyContextImpl:
    """Concrete implementation satisfying the PolicyContext protocol."""
    workspace: Path
    capabilities: frozenset[Capability]


def records_to_messages(records: list[dict[str, Any]]) -> list[Message]:
    """
    Converts transcript records to SDK Message dataclasses.
    Preserves ToolUseBlock and ToolResultBlock structures.
    Slices history starting from the latest COMPACTION_SUMMARY boundary.
    """
    if not records:
        return []

    # Find latest compaction boundary
    last_compaction_idx = -1
    for idx, r in enumerate(records):
        rec_type = str(r.get("type", "")).upper()
        if rec_type == "COMPACTION_SUMMARY" or r.get("metadata", {}).get("compaction_summary"):
            last_compaction_idx = idx

    if last_compaction_idx >= 0:
        sliced_records = records[last_compaction_idx:]
    else:
        sliced_records = records

    messages: list[Message] = []
    for r in sliced_records:
        rec_type = str(r.get("type", "")).upper()
        rec_source = str(r.get("source", "")).upper()

        if rec_type == "COMPACTION_SUMMARY":
            content_text = r.get("content", "")
            msg = Message(
                role=Role.USER,
                content=[TextBlock(text=f"## Previous Conversation Summary\n\n{content_text}")],
                metadata={"compaction_summary": True},
            )
            messages.append(msg)
            continue

        if rec_source == "USER" or rec_type in ("USER", "USER_INPUT"):
            content_blocks: list[ContentBlock] = [TextBlock(text=r.get("content", ""))]
            if "media" in r and isinstance(r["media"], list):
                for m in r["media"]:
                    if isinstance(m, dict):
                        content_blocks.append(
                            ImageBlock(
                                media_type=m.get("media_type", "image/png"),
                                data_b64=m.get("data_b64"),
                                url=m.get("url"),
                            )
                        )
            messages.append(Message(role=Role.USER, content=content_blocks))
            continue

        if rec_source in ("MODEL", "ASSISTANT") or rec_type in ("MODEL", "ASSISTANT", "PLANNER_RESPONSE"):
            blocks: list[ContentBlock] = []
            if r.get("thinking"):
                blocks.append(ThinkingBlock(text=r["thinking"]))
            if r.get("content"):
                blocks.append(TextBlock(text=r["content"]))
            if r.get("tool_calls"):
                for tc in r["tool_calls"]:
                    tc_id = tc.get("id") or tc.get("call_id", "")
                    tc_name = tc.get("name", "")
                    tc_args = tc.get("arguments") or tc.get("input", {})
                    blocks.append(ToolUseBlock(id=tc_id, name=tc_name, input=tc_args))
            messages.append(Message(role=Role.ASSISTANT, content=blocks))
            continue

        if rec_source == "TOOL" or rec_type in ("TOOL", "TOOL_RESULT"):
            use_id = r.get("tool_use_id") or r.get("call_id") or r.get("id", "")
            text = r.get("content", "")
            is_err = bool(r.get("is_error", False))
            block = ToolResultBlock(
                tool_use_id=use_id,
                content=[TextBlock(text=text)],
                is_error=is_err,
            )
            messages.append(Message(role=Role.TOOL, content=[block]))
            continue

    return messages


class ContextAssembler:
    """
    Collects context blocks in parallel across registered ContextSources,
    sorts by stability tier and priority, and formats ModelRequests.
    """

    def __init__(
        self,
        registry: PluginRegistry,
        settings: SettingsStore,
        bus: EventBus | None = None,
        timeout_s: float = 5.0,
    ):
        self.registry = registry
        self.settings = settings
        self.bus = bus
        self.timeout_s = timeout_s

    async def _collect_source_blocks(
        self, source: ContextSource, session_id: str
    ) -> list[ContextBlock]:
        """Collects blocks from a single ContextSource with strict timeout."""
        try:
            async with asyncio.timeout(self.timeout_s):
                hook_ctx = HookContextImpl(
                    session_id=session_id, workspace=self.registry.workspace
                )
                blocks = await source.blocks(hook_ctx)
                # Ensure source attribute is tagged on blocks
                for b in blocks:
                    if not b.source:
                        b.source = getattr(source, "name", "unknown")
                    if not b.token_estimate:
                        b.token_estimate = estimate_tokens(b.text)
                return blocks
        except Exception as err:
            logger.warning(
                f"ContextSource '{getattr(source, 'name', type(source).__name__)}' failed: {err}"
            )
            return []

    async def assemble(
        self,
        session_id: str,
        user_prompt: str,
        model: str = "",
        history_records: list[dict[str, Any]] | None = None,
        media: list[ImageBlock] | None = None,
        current_turn_blocks: list[ContextBlock] | None = None,
        tools: list[ToolSpec] | None = None,
        provider_caps: ProviderCaps | None = None,
        params: ModelParams | None = None,
    ) -> ModelRequest:
        """
        Assembles complete ModelRequest:
        - Parallel source collection with 5s timeout.
        - Tier sorting: STATIC -> SESSION -> TURN.
        - system = STATIC + SESSION.
        - messages = history + [ephemeral TURN context] + latest user message.
        """
        # 1. Parallel collection from all registered ContextSources
        tasks = [
            self._collect_source_blocks(src, session_id)
            for src, _ in self.registry.context_sources
        ]
        results = await asyncio.gather(*tasks, return_exceptions=True)

        collected_blocks: list[ContextBlock] = []
        for r in results:
            if isinstance(r, list):
                collected_blocks.extend(r)

        # 2. Add current turn blocks (e.g. hook context, pending approvals)
        if current_turn_blocks:
            for tb in current_turn_blocks:
                tb_copy = copy.deepcopy(tb)
                tb_copy.stability = Stability.TURN
                if not tb_copy.token_estimate:
                    tb_copy.token_estimate = estimate_tokens(tb_copy.text)
                collected_blocks.append(tb_copy)

        # 3. Sort by tier stability order (STATIC -> SESSION -> TURN) and priority
        collected_blocks.sort(
            key=lambda b: (STABILITY_ORDER.get(b.stability, 99), b.priority)
        )

        # 4. Partition blocks
        static_blocks = [b for b in collected_blocks if b.stability == Stability.STATIC]
        session_blocks = [b for b in collected_blocks if b.stability == Stability.SESSION]
        turn_blocks = [b for b in collected_blocks if b.stability == Stability.TURN]

        # System prompt blocks: STATIC + SESSION
        system_blocks = static_blocks + session_blocks
        system_text_blocks = [
            TextBlock(text=b.text) for b in system_blocks
        ]

        # 5. History messages from transcript records
        history_messages = records_to_messages(history_records or [])

        # 6. Ephemeral TURN context wrapped in <context> tags
        assembled_messages: list[Message] = list(history_messages)
        if turn_blocks:
            turn_lines = [b.text for b in turn_blocks if b.text]
            if turn_lines:
                turn_text = "<context>\n" + "\n\n".join(turn_lines) + "\n</context>"
                ephemeral_msg = Message(
                    role=Role.USER,
                    content=[TextBlock(text=turn_text)],
                    metadata={"ephemeral": True},
                )
                assembled_messages.append(ephemeral_msg)

        # 7. Latest user prompt message
        user_content: list[ContentBlock] = [TextBlock(text=user_prompt)]
        if media:
            user_content.extend(media)
        latest_user_msg = Message(role=Role.USER, content=user_content)
        assembled_messages.append(latest_user_msg)

        # 8. Available tools
        if tools is not None:
            active_tools = list(tools)
        else:
            active_tools = [spec for spec, _, _ in self.registry.tools.values()]

        # 9. Emit context.assembled event
        total_tokens = estimate_request_tokens(system_text_blocks, assembled_messages)
        if self.bus:
            self.bus.publish(
                Event(
                    type="context.assembled",
                    session_id=session_id,
                    data={
                        "blocks": [
                            {
                                "id": b.id,
                                "stability": b.stability.value,
                                "source": b.source,
                                "tokens": b.token_estimate,
                            }
                            for b in collected_blocks
                        ],
                        "total_tokens": total_tokens,
                        "history_messages": len(history_messages),
                    },
                )
            )

        model_name = model or self.settings.get("kernel.model", "default-model") or "default-model"

        return ModelRequest(
            model=model_name,
            system=system_blocks,
            messages=assembled_messages,
            tools=active_tools,
            params=params or ModelParams(),
        )
