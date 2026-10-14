"""
Anthropic Messages API Provider implementation (§4, PLG-10).
Absorbs MDL-03 and MDL-04.
"""
from __future__ import annotations

import json
import logging
import time
from collections.abc import AsyncIterator
from typing import Any

import httpx

from harness.sdk.errors import ProviderError, ProviderErrorKind
from harness.sdk.types import (
    Done,
    ImageBlock,
    ModelEvent,
    ModelInfo,
    ModelRequest,
    ProviderCaps,
    Role,
    Stability,
    TextBlock,
    TextDelta,
    ThinkingBlock,
    ThinkingDelta,
    ToolCallEnd,
    ToolResultBlock,
    ToolUseBlock,
    Usage,
    UsageEvent,
)

logger = logging.getLogger(__name__)

DEFAULT_ANTHROPIC_MODELS: list[ModelInfo] = [
    ModelInfo(
        id="claude-3-7-sonnet",
        display_name="Claude 3.7 Sonnet (Hybrid Reasoning)",
        context_tokens=200000,
        input_usd_per_mtok=3.0,
        output_usd_per_mtok=15.0,
    ),
    ModelInfo(
        id="claude-3-5-sonnet-latest",
        display_name="Claude 3.5 Sonnet",
        context_tokens=200000,
        input_usd_per_mtok=3.0,
        output_usd_per_mtok=15.0,
    ),
    ModelInfo(
        id="claude-3-5-haiku-latest",
        display_name="Claude 3.5 Haiku",
        context_tokens=200000,
        input_usd_per_mtok=0.80,
        output_usd_per_mtok=4.0,
    ),
]


class AnthropicProvider:
    """
    Anthropic Messages API provider supporting prompt caching (ephemeral cache_control),
    thinking blocks, native tool calling, and full SSE delta streaming.
    """

    caps = ProviderCaps(
        native_tools=True,
        streaming=True,
        vision=True,
        cache_hints=True,
        thinking=True,
        json_output=True,
        max_context_tokens=200000,
    )

    def __init__(
        self,
        name: str = "anthropic",
        base_url: str = "https://api.anthropic.com/v1",
        api_key: str = "",
        timeout_s: float = 60.0,
        extra_headers: dict[str, str] | None = None,
        client: httpx.AsyncClient | None = None,
    ) -> None:
        self.name = name
        self.base_url = base_url.rstrip("/")
        self.api_key = api_key
        self.timeout_s = timeout_s
        self.extra_headers = extra_headers or {}
        self._external_client = client is not None
        self._client: httpx.AsyncClient = client or httpx.AsyncClient(
            timeout=httpx.Timeout(
                connect=10.0,
                read=timeout_s,
                write=30.0,
                pool=10.0,
            )
        )
        self._cached_models: list[ModelInfo] | None = None
        self._cached_models_ts: float = 0.0

    async def aclose(self) -> None:
        if not self._external_client and not self._client.is_closed:
            await self._client.aclose()

    async def list_models(self) -> list[ModelInfo]:
        """Returns supported models with 24h caching."""
        now = time.time()
        if self._cached_models and (now - self._cached_models_ts) < 86400:
            return self._cached_models

        # Check API key if not provided and not external client
        if not self.api_key and not self._external_client:
            raise ProviderError(
                provider=self.name,
                model="claude-3-7-sonnet",
                kind="auth",
                message="No Anthropic API key configured (set ANTHROPIC_API_KEY in ~/.harness/secrets.json)",
                retryable=False,
            )

        self._cached_models = list(DEFAULT_ANTHROPIC_MODELS)
        self._cached_models_ts = now
        return self._cached_models

    def _build_headers(self) -> dict[str, str]:
        headers = {
            "x-api-key": self.api_key,
            "anthropic-version": "2023-06-01",
            "anthropic-beta": "prompt-caching-2024-07-31,thinking-2025-01-20",
            "content-type": "application/json",
        }
        headers.update(self.extra_headers)
        return headers

    def _build_system(self, system_blocks: list[Any]) -> list[dict[str, Any]]:
        """
        Builds Anthropic system content blocks with cache_control at tier boundaries.
        Limits breakpoints to <= 4.
        """
        out: list[dict[str, Any]] = []
        static_indices = []

        for idx, block in enumerate(system_blocks):
            entry: dict[str, Any] = {"type": "text", "text": getattr(block, "text", str(block))}
            if getattr(block, "stability", None) == Stability.STATIC:
                static_indices.append(idx)
            out.append(entry)

        # Apply ephemeral cache_control to the last STATIC block
        if static_indices:
            last_static_idx = static_indices[-1]
            out[last_static_idx]["cache_control"] = {"type": "ephemeral"}

        return out

    def _build_messages(self, messages: list[Any]) -> list[dict[str, Any]]:
        """Converts universal Message objects into Anthropic message schemas."""
        out: list[dict[str, Any]] = []

        for msg in messages:
            role = "user" if msg.role == Role.USER else "assistant"
            content_blocks: list[dict[str, Any]] = []

            for block in msg.content:
                if isinstance(block, TextBlock):
                    content_blocks.append({"type": "text", "text": block.text})
                elif isinstance(block, ImageBlock):
                    import base64
                    b64 = base64.b64encode(block.data).decode("utf-8")
                    content_blocks.append({
                        "type": "image",
                        "source": {
                            "type": "base64",
                            "media_type": block.media_type,
                            "data": b64,
                        },
                    })
                elif isinstance(block, ToolUseBlock):
                    content_blocks.append({
                        "type": "tool_use",
                        "id": block.id,
                        "name": block.name,
                        "input": block.input,
                    })
                elif isinstance(block, ToolResultBlock):
                    # In Anthropic API, tool results are returned in user role content blocks
                    res_parts: list[Any] = []
                    for c in block.content:
                        if isinstance(c, TextBlock):
                            res_parts.append({"type": "text", "text": c.text})
                    content_blocks.append({
                        "type": "tool_result",
                        "tool_use_id": block.tool_use_id,
                        "content": res_parts if len(res_parts) > 1 else (res_parts[0]["text"] if res_parts else ""),
                        "is_error": block.is_error,
                    })
                elif isinstance(block, ThinkingBlock):
                    content_blocks.append({
                        "type": "thinking",
                        "thinking": block.thinking,
                    })

            if content_blocks:
                out.append({"role": role, "content": content_blocks})

        return out

    def _build_tools(self, tools: list[Any]) -> list[dict[str, Any]]:
        """Formats tools for Anthropic Messages API."""
        out: list[dict[str, Any]] = []
        for idx, t in enumerate(tools):
            entry: dict[str, Any] = {
                "name": t.name,
                "description": t.description,
                "input_schema": t.input_schema,
            }
            # Put cache_control on the last tool
            if idx == len(tools) - 1:
                entry["cache_control"] = {"type": "ephemeral"}
            out.append(entry)
        return out

    async def generate(self, req: ModelRequest) -> AsyncIterator[ModelEvent]:
        """Streams model events for request using Anthropic SSE streaming."""
        headers = self._build_headers()
        system_payload = self._build_system(req.system)
        messages_payload = self._build_messages(req.messages)

        is_thinking = bool(
            req.params.extra.get("thinking")
            or req.params.reasoning_effort
            or "3-7" in req.model
        )
        thinking_budget = int(req.params.extra.get("thinking_budget", 2048) or 2048)

        payload: dict[str, Any] = {
            "model": req.model,
            "max_tokens": max(8192 if is_thinking else 4096, req.params.max_tokens or 4096),
            "stream": True,
            "messages": messages_payload,
        }

        if system_payload:
            payload["system"] = system_payload

        if req.tools:
            payload["tools"] = self._build_tools(req.tools)

        if is_thinking or "3-7" in req.model:
            payload["thinking"] = {
                "type": "enabled",
                "budget_tokens": thinking_budget,
            }
        else:
            if req.params.temperature is not None:
                payload["temperature"] = req.params.temperature

        url = f"{self.base_url}/messages"

        try:
            async with self._client.stream("POST", url, headers=headers, json=payload) as resp:
                if resp.status_code != 200:
                    err_body = await resp.aread()
                    err_text = err_body.decode("utf-8", errors="replace")
                    self._handle_http_error(resp.status_code, err_text, req.model)

                # SSE Event Streaming state
                current_tool_id: str = ""
                current_tool_name: str = ""
                tool_json_chunks: list[str] = []
                stop_reason: str = "end_turn"

                # Usage tracking
                input_tokens = 0
                output_tokens = 0
                cache_read_tokens = 0
                cache_write_tokens = 0

                async for line in resp.aiter_lines():
                    if not line:
                        continue
                    if not line.startswith("data: "):
                        continue

                    data_str = line[6:].strip()
                    if not data_str or data_str == "[DONE]":
                        continue

                    try:
                        event = json.loads(data_str)
                    except json.JSONDecodeError:
                        continue

                    event_type = event.get("type")

                    if event_type == "message_start":
                        msg_data = event.get("message", {})
                        usage_data = msg_data.get("usage", {})
                        input_tokens = usage_data.get("input_tokens", 0)
                        cache_read_tokens = usage_data.get("cache_read_input_tokens", 0)
                        cache_write_tokens = usage_data.get("cache_creation_input_tokens", 0)

                    elif event_type == "content_block_start":
                        block = event.get("content_block", {})
                        block_type = block.get("type")
                        if block_type == "tool_use":
                            current_tool_id = block.get("id", "")
                            current_tool_name = block.get("name", "")
                            tool_json_chunks = []

                    elif event_type == "content_block_delta":
                        delta = event.get("delta", {})
                        delta_type = delta.get("type")

                        if delta_type == "text_delta":
                            text = delta.get("text", "")
                            if text:
                                yield TextDelta(text=text)

                        elif delta_type == "thinking_delta":
                            thinking = delta.get("thinking", "")
                            if thinking:
                                yield ThinkingDelta(text=thinking)

                        elif delta_type == "input_json_delta":
                            partial = delta.get("partial_json", "")
                            if partial:
                                tool_json_chunks.append(partial)

                    elif event_type == "content_block_stop":
                        if current_tool_id:
                            full_json = "".join(tool_json_chunks)
                            try:
                                args = json.loads(full_json) if full_json.strip() else {}
                            except json.JSONDecodeError:
                                args = {}

                            yield ToolCallEnd(
                                id=current_tool_id,
                                name=current_tool_name,
                                arguments=args,
                            )
                            current_tool_id = ""
                            current_tool_name = ""
                            tool_json_chunks = []

                    elif event_type == "message_delta":
                        delta = event.get("delta", {})
                        sr = delta.get("stop_reason")
                        if sr:
                            if sr == "tool_use":
                                stop_reason = "tool_use"
                            elif sr == "max_tokens":
                                stop_reason = "max_tokens"
                            else:
                                stop_reason = "end_turn"

                        usage_delta = event.get("usage", {})
                        output_tokens += usage_delta.get("output_tokens", 0)

                    elif event_type == "message_stop":
                        total_usage = Usage(
                            input_tokens=input_tokens,
                            output_tokens=output_tokens,
                            cache_read_tokens=cache_read_tokens,
                            cache_write_tokens=cache_write_tokens,
                        )
                        yield UsageEvent(usage=total_usage)
                        yield Done(stop_reason=stop_reason)
                        return

                # If loop ends without explicit message_stop
                total_usage = Usage(
                    input_tokens=input_tokens,
                    output_tokens=output_tokens,
                    cache_read_tokens=cache_read_tokens,
                    cache_write_tokens=cache_write_tokens,
                )
                yield UsageEvent(usage=total_usage)
                yield Done(stop_reason=stop_reason)

        except httpx.TimeoutException as err:
            raise ProviderError(
                provider=self.name,
                model=req.model,
                kind="timeout",
                message=f"Anthropic request timed out after {self.timeout_s}s: {err}",
                retryable=True,
            ) from err
        except httpx.RequestError as err:
            raise ProviderError(
                provider=self.name,
                model=req.model,
                kind="unavailable",
                message=f"Network error connecting to Anthropic API: {err}",
                retryable=True,
            ) from err

    def _handle_http_error(self, status_code: int, err_text: str, model: str) -> None:
        """Maps HTTP status codes into normalized ProviderError hierarchies."""
        kind: ProviderErrorKind = "server"
        retryable = False

        if status_code in (401, 403):
            kind = "auth"
            retryable = False
        elif status_code == 429:
            kind = "rate_limit"
            retryable = True
        elif status_code in (500, 502, 503, 504):
            kind = "server"
            retryable = True
        elif status_code == 400:
            kind = "bad_request"
            retryable = False

        raise ProviderError(
            provider=self.name,
            model=model,
            status_code=status_code,
            retryable=retryable,
            kind=kind,
            message=f"Anthropic API returned status {status_code}: {err_text}",
        )
