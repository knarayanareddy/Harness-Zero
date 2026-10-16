"""
ContextAssembler: parallel source collection, tier ordering, cache hints,
and prompt assembly for model requests.
Fulfills KRN-07 (§5.4) and absorbs LOOP-02 and CONF-04.
"""
from __future__ import annotations

import asyncio
import copy
import logging
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from harness.kernel.bus import EventBus
from harness.kernel.compaction import estimate_request_tokens, estimate_tokens
from harness.kernel.registry import PluginRegistry
from harness.kernel.settings import SettingsStore
from harness.sdk.contracts import ContextSource
from harness.sdk.events import ContextAssembled, Event
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
    SessionInfo,
    Stability,
    TextBlock,
    ThinkingBlock,
    ToolResultBlock,
    ToolSpec,
    ToolUseBlock,
    TranscriptRecord,
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
    turn_state: dict[str, Any] = field(default_factory=dict)
    bus: Any = None

    def emit(self, event_type: str, data: Any) -> None:
        """Publishes a live, non-durable event for the UI (badges, notices).

        Deliberately narrow: no session/turn routing, no replay, no `tool.*` channel the client
        validates field-by-field. A hook that needs a durable fact writes a record, not an event.
        """
        if self.bus is None:
            return
        self.bus.publish(Event(type=event_type, session_id=self.session_id, data=data))


@dataclass
class PolicyContextImpl:
    """Concrete implementation satisfying the PolicyContext protocol."""
    workspace: Path
    capabilities: frozenset[Capability]


@dataclass
class TurnContextImpl:
    """Concrete implementation satisfying the TurnContext protocol."""
    session: SessionInfo
    turn_id: str
    prompt: str
    media: list[ImageBlock] = field(default_factory=list)
    options: dict[str, Any] = field(default_factory=dict)
    settings: Any = None
    cancel: asyncio.Event = field(default_factory=asyncio.Event)
    history: list[Message] = field(default_factory=list)

    @property
    def session_id(self) -> str:
        return self.session.session_id

    @property
    def workspace(self) -> Path:
        return self.session.workspace

    @property
    def user_prompt(self) -> str:
        return self.prompt



def records_to_messages(records: list[dict[str, Any] | TranscriptRecord]) -> list[Message]:
    """
    Converts transcript records to SDK Message dataclasses.
    Preserves ToolUseBlock and ToolResultBlock structures.
    Standardizes tool results into USER role messages with ToolResultBlocks.
    Slices history starting from the latest COMPACTION_SUMMARY boundary.
    """
    if not records:
        return []

    # Find latest compaction boundary
    last_compaction_idx = -1
    for idx, r in enumerate(records):
        r_dict = r.to_dict() if hasattr(r, "to_dict") else r
        rec_type = str(r_dict.get("type", "")).upper()
        if rec_type == "COMPACTION_SUMMARY" or r_dict.get("metadata", {}).get("compaction_summary"):
            last_compaction_idx = idx

    if last_compaction_idx >= 0:
        sliced_records = records[last_compaction_idx:]
    else:
        sliced_records = records

    messages: list[Message] = []
    for r in sliced_records:
        rec = r if isinstance(r, TranscriptRecord) else TranscriptRecord.from_dict(r)

        if rec.metadata.get("compaction_summary"):
            msg = Message(
                role=Role.USER,
                content=[TextBlock(text=f"## Previous Conversation Summary\n\n{rec.content}")],
                metadata={"compaction_summary": True},
            )
            messages.append(msg)
            continue

        if rec.role == "assistant":
            blocks: list[ContentBlock] = []
            if rec.thinking:
                blocks.append(ThinkingBlock(text=rec.thinking))
            if rec.content:
                blocks.append(TextBlock(text=rec.content))
            if rec.tool_calls:
                for tc in rec.tool_calls:
                    tc_id = tc.get("id") or tc.get("call_id", "")
                    tc_name = tc.get("name", "")
                    tc_args = tc.get("arguments") or tc.get("input", {})
                    blocks.append(ToolUseBlock(id=tc_id, name=tc_name, input=tc_args))
            messages.append(Message(role=Role.ASSISTANT, content=blocks, metadata=rec.metadata))
            continue

        if rec.role in ("user", "tool"):
            blocks: list[ContentBlock] = []
            # Tool results standardized as USER role blocks per §4.1 #4
            if rec.tool_results:
                for tr in rec.tool_results:
                    use_id = tr.get("tool_use_id") or tr.get("call_id") or tr.get("id", "")
                    tr_content = tr.get("content", [])
                    parsed_content: list[TextBlock | ImageBlock] = []
                    if isinstance(tr_content, str):
                        parsed_content.append(TextBlock(text=tr_content))
                    elif isinstance(tr_content, list):
                        for c in tr_content:
                            if isinstance(c, dict):
                                if c.get("type") == "image":
                                    parsed_content.append(ImageBlock.from_dict(c))
                                else:
                                    parsed_content.append(TextBlock.from_dict(c))
                            elif isinstance(c, str):
                                parsed_content.append(TextBlock(text=c))
                    blocks.append(
                        ToolResultBlock(
                            tool_use_id=use_id,
                            content=parsed_content,
                            is_error=bool(tr.get("is_error", False)),
                        )
                    )

            if rec.content:
                blocks.append(TextBlock(text=rec.content))

            if rec.media:
                for m in rec.media:
                    if isinstance(m, dict):
                        blocks.append(
                            ImageBlock(
                                media_type=m.get("media_type", "image/png"),
                                data_b64=m.get("data_b64"),
                                url=m.get("url"),
                            )
                        )
                    elif isinstance(m, ImageBlock):
                        blocks.append(m)

            if blocks:
                messages.append(Message(role=Role.USER, content=blocks, metadata=rec.metadata))
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
        user_prompt: str = "",
        model: str = "",
        history_records: list[dict[str, Any]] | None = None,
        media: list[ImageBlock] | None = None,
        current_turn_blocks: list[ContextBlock] | None = None,
        tools: list[ToolSpec] | None = None,
        provider_caps: ProviderCaps | None = None,
        params: ModelParams | None = None,
        prompt: str = "",
        options: dict[str, Any] | None = None,
        **kwargs: Any,
    ) -> ModelRequest:
        actual_prompt = prompt or user_prompt
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

        # 7. Latest user prompt message (only if not already in history)
        has_prompt_in_history = False
        if history_messages:
            last_msg = history_messages[-1]
            if last_msg.role == Role.USER and last_msg.text() == actual_prompt:
                has_prompt_in_history = True
            elif any(isinstance(b, ToolResultBlock) for b in last_msg.content):
                has_prompt_in_history = True
            else:
                for m in reversed(history_messages):
                    if m.role == Role.USER and m.text() == actual_prompt:
                        has_prompt_in_history = True
                        break
                    if m.role == Role.ASSISTANT:
                        break

        if not has_prompt_in_history and (actual_prompt or media):
            user_content: list[ContentBlock] = []
            if actual_prompt:
                user_content.append(TextBlock(text=actual_prompt))
            if media:
                user_content.extend(media)
            assembled_messages.append(Message(role=Role.USER, content=user_content))

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
                    data=ContextAssembled(
                        blocks=[
                            {
                                "id": b.id,
                                "stability": b.stability.value,
                                "source": b.source,
                                "tokens": b.token_estimate,
                            }
                            for b in collected_blocks
                        ],
                        total_tokens=total_tokens,
                        history_messages=len(history_messages),
                    ),
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
