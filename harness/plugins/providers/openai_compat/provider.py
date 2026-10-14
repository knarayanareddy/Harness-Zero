"""
Universal OpenAI-compatible LLM Provider implementation (§4, PLG-01).
Connects to OpenAI, OpenRouter, DeepSeek, Ollama, and local vLLM endpoints.
"""
from __future__ import annotations

import json
import logging
import time
from collections.abc import AsyncIterator
from typing import Any

import httpx

from harness.sdk.errors import ProviderError
from harness.sdk.types import (
    Done,
    ImageBlock,
    ModelEvent,
    ModelInfo,
    ModelRequest,
    ProviderCaps,
    Role,
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


class OpenAICompatProvider:
    """
    OpenAI-compatible inference provider supporting native tool calling,
    SSE streaming deltas, reasoning blocks, prompt cache statistics, and 24h model caching.
    """

    caps = ProviderCaps(
        native_tools=True,
        streaming=True,
        vision=True,
        cache_hints=True,
        thinking=True,
        json_output=True,
        max_context_tokens=128000,
    )

    def __init__(
        self,
        name: str = "openai",
        base_url: str = "https://api.openai.com/v1",
        api_key: str = "",
        timeout_s: float = 60.0,
        extra_headers: dict[str, str] | None = None,
        default_models: list[ModelInfo] | None = None,
        client: httpx.AsyncClient | None = None,
    ) -> None:
        self.name = name
        self.base_url = base_url.rstrip("/")
        self.api_key = api_key
        self.timeout_s = timeout_s
        self.extra_headers = dict(extra_headers or {})
        self._default_models = list(default_models or self._builtin_model_list())
        self._client = client
        self._cached_models: list[ModelInfo] | None = None
        self._cached_models_ts: float = 0.0

    def _builtin_model_list(self) -> list[ModelInfo]:
        """Provides fallback models when list_models endpoint is not reachable."""
        if "deepseek" in self.name or "deepseek" in self.base_url:
            return [
                ModelInfo(id="deepseek-chat", display_name="DeepSeek V3", context_tokens=64000, input_usd_per_mtok=0.14, output_usd_per_mtok=0.28),
                ModelInfo(id="deepseek-reasoner", display_name="DeepSeek R1", context_tokens=64000, input_usd_per_mtok=0.55, output_usd_per_mtok=2.19),
            ]
        elif "openrouter" in self.name or "openrouter" in self.base_url:
            return [
                ModelInfo(id="anthropic/claude-3.5-sonnet", display_name="Claude 3.5 Sonnet (OpenRouter)", context_tokens=200000, input_usd_per_mtok=3.0, output_usd_per_mtok=15.0),
                ModelInfo(id="openai/gpt-4o", display_name="GPT-4o (OpenRouter)", context_tokens=128000, input_usd_per_mtok=2.5, output_usd_per_mtok=10.0),
            ]
        return [
            ModelInfo(id="gpt-4o", display_name="GPT-4o", context_tokens=128000, input_usd_per_mtok=2.5, output_usd_per_mtok=10.0),
            ModelInfo(id="gpt-4o-mini", display_name="GPT-4o Mini", context_tokens=128000, input_usd_per_mtok=0.15, output_usd_per_mtok=0.6),
            ModelInfo(id="o1", display_name="OpenAI o1", context_tokens=200000, input_usd_per_mtok=15.0, output_usd_per_mtok=60.0),
        ]

    def _get_client(self) -> httpx.AsyncClient:
        if self._client is None or self._client.is_closed:
            self._client = httpx.AsyncClient(
                timeout=httpx.Timeout(connect=10.0, read=self.timeout_s, write=30.0, pool=10.0),
                headers=self._build_headers(),
            )
        return self._client

    def _build_headers(self) -> dict[str, str]:
        headers = {
            "Content-Type": "application/json",
            **self.extra_headers,
        }
        if self.api_key:
            headers["Authorization"] = f"Bearer {self.api_key}"
        return headers

    async def aclose(self) -> None:
        """Closes the underlying HTTP client."""
        if self._client is not None and not self._client.is_closed:
            await self._client.aclose()

    async def list_models(self) -> list[ModelInfo]:
        """
        Lists supported models with a 24-hour cache.
        Queries /models endpoint or falls back to built-in models.
        """
        now = time.time()
        if self._cached_models and (now - self._cached_models_ts) < 86400:
            return self._cached_models

        # If commercial provider and no api key configured, raise auth error
        if not self.api_key and any(k in self.base_url for k in ("openai.com", "openrouter.ai", "deepseek.com")):
            raise ProviderError(
                provider=self.name,
                model="*",
                status_code=401,
                kind="auth",
                message=f"Missing API key for provider '{self.name}'",
                retryable=False,
            )

        client = self._get_client()
        try:
            resp = await client.get(f"{self.base_url}/models")
            if resp.status_code in (401, 403):
                raise ProviderError(
                    provider=self.name,
                    model="*",
                    status_code=resp.status_code,
                    kind="auth",
                    message=f"Authentication failed: {resp.text}",
                    retryable=False,
                )
            if resp.is_success:
                data = resp.json()
                raw_list = data.get("data", [])
                models = [
                    ModelInfo(
                        id=item.get("id", ""),
                        display_name=item.get("name") or item.get("id", ""),
                        context_tokens=item.get("context_length", 128000),
                    )
                    for item in raw_list
                    if item.get("id")
                ]
                if models:
                    self._cached_models = models
                    self._cached_models_ts = now
                    return models
        except ProviderError:
            raise
        except Exception as err:
            logger.debug(f"Could not fetch models from {self.base_url}/models: {err}")

        # Fallback to default model definitions
        self._cached_models = self._default_models
        self._cached_models_ts = now
        return self._cached_models

    def _convert_messages(self, req: ModelRequest) -> list[dict[str, Any]]:
        """Converts SDK Messages and System context into OpenAI messages list."""
        messages: list[dict[str, Any]] = []

        # System prompt blocks
        system_text = "\n\n".join(b.text for b in req.system if b.text)
        if system_text:
            messages.append({"role": "system", "content": system_text})

        for msg in req.messages:
            if msg.role == Role.SYSTEM:
                messages.append({"role": "system", "content": msg.text()})

            elif msg.role == Role.USER:
                has_images = any(isinstance(c, ImageBlock) for c in msg.content)
                if not has_images:
                    messages.append({"role": "user", "content": msg.text()})
                else:
                    parts: list[dict[str, Any]] = []
                    for c in msg.content:
                        if isinstance(c, TextBlock):
                            parts.append({"type": "text", "text": c.text})
                        elif isinstance(c, ImageBlock):
                            img_url = c.url or (f"data:{c.media_type};base64,{c.data_b64}" if c.data_b64 else "")
                            if img_url:
                                parts.append({"type": "image_url", "image_url": {"url": img_url}})
                    messages.append({"role": "user", "content": parts})

            elif msg.role == Role.ASSISTANT:
                text_parts = [c.text for c in msg.content if isinstance(c, (TextBlock, ThinkingBlock))]
                tool_use_blocks = [c for c in msg.content if isinstance(c, ToolUseBlock)]

                content_val = "\n".join(text_parts) if text_parts else None
                m: dict[str, Any] = {"role": "assistant"}
                if content_val:
                    m["content"] = content_val
                if tool_use_blocks:
                    m["tool_calls"] = [
                        {
                            "id": b.id,
                            "type": "function",
                            "function": {
                                "name": b.name,
                                "arguments": json.dumps(b.input),
                            },
                        }
                        for b in tool_use_blocks
                    ]
                messages.append(m)

            elif msg.role == Role.TOOL:
                for c in msg.content:
                    if isinstance(c, ToolResultBlock):
                        tool_text = "\n".join(
                            sub.text for sub in c.content if isinstance(sub, TextBlock)
                        )
                        messages.append({
                            "role": "tool",
                            "tool_call_id": c.tool_use_id,
                            "content": tool_text,
                        })

        return messages

    def _convert_tools(self, req: ModelRequest) -> list[dict[str, Any]] | None:
        """Converts SDK ToolSpecs into OpenAI function definitions."""
        if not req.tools:
            return None
        tools = []
        for t in req.tools:
            tools.append({
                "type": "function",
                "function": {
                    "name": t.name,
                    "description": t.description,
                    "parameters": t.input_schema,
                },
            })
        return tools

    def _map_http_error(self, err: httpx.HTTPStatusError, model: str) -> ProviderError:
        status = err.response.status_code
        text = err.response.text
        if status in (401, 403):
            kind = "auth"
            retryable = False
        elif status == 429:
            kind = "rate_limit"
            retryable = True
        elif status in (400, 422):
            kind = "bad_request"
            retryable = False
        elif status >= 500:
            kind = "server"
            retryable = True
        else:
            kind = "unavailable"
            retryable = False

        return ProviderError(
            provider=self.name,
            model=model,
            status_code=status,
            kind=kind,
            message=f"HTTP {status} from {self.name}: {text}",
            retryable=retryable,
        )

    async def generate(self, req: ModelRequest) -> AsyncIterator[ModelEvent]:
        """
        Executes streaming completion request over Server-Sent Events (SSE).
        Yields TextDelta, ThinkingDelta, ToolCallEnd, UsageEvent, and Done.
        """
        if not self.api_key and any(k in self.base_url for k in ("openai.com", "openrouter.ai", "deepseek.com")):
            raise ProviderError(
                provider=self.name,
                model=req.model,
                status_code=401,
                kind="auth",
                message=f"No API key provided for {self.name}",
                retryable=False,
            )

        messages = self._convert_messages(req)
        tools = self._convert_tools(req)

        payload: dict[str, Any] = {
            "model": req.model,
            "messages": messages,
            "stream": True,
            "stream_options": {"include_usage": True},
        }

        if tools:
            payload["tools"] = tools

        params = req.params
        if params:
            if params.temperature is not None:
                payload["temperature"] = params.temperature
            if params.max_tokens is not None:
                payload["max_tokens"] = params.max_tokens
            if params.reasoning_effort:
                payload["reasoning_effort"] = params.reasoning_effort
            if params.stop:
                payload["stop"] = params.stop
            payload.update(params.extra)

        client = self._get_client()

        # In-flight tool calls accumulator: index -> {"id": ..., "name": ..., "arguments": ...}
        active_tools: dict[int, dict[str, str]] = {}
        stop_reason = "end_turn"
        done_emitted = False
        usage_emitted = False

        try:
            async with client.stream(
                "POST",
                f"{self.base_url}/chat/completions",
                json=payload,
                headers=self._build_headers(),
            ) as response:
                if not response.is_success:
                    await response.aread()
                    raise response.raise_for_status()

                async for line in response.aiter_lines():
                    line = line.strip()
                    if not line or not line.startswith("data:"):
                        continue

                    data_str = line[5:].strip()
                    if data_str == "[DONE]":
                        break

                    try:
                        chunk = json.loads(data_str)
                    except json.JSONDecodeError:
                        continue

                    # Process usage block
                    usage_data = chunk.get("usage")
                    if usage_data:
                        in_tok = usage_data.get("prompt_tokens", 0)
                        out_tok = usage_data.get("completion_tokens", 0)
                        prompt_details = usage_data.get("prompt_tokens_details", {})
                        cached_tok = prompt_details.get("cached_tokens", 0)
                        yield UsageEvent(
                            usage=Usage(
                                input_tokens=in_tok,
                                output_tokens=out_tok,
                                cache_read_tokens=cached_tok,
                            )
                        )
                        usage_emitted = True

                    choices = chunk.get("choices", [])
                    if not choices:
                        continue

                    choice = choices[0]
                    delta = choice.get("delta", {})

                    # Text delta
                    text_content = delta.get("content")
                    if text_content:
                        yield TextDelta(text=text_content)

                    # Thinking / reasoning delta
                    reasoning_content = delta.get("reasoning_content") or delta.get("reasoning")
                    if reasoning_content:
                        yield ThinkingDelta(text=reasoning_content)

                    # Tool call deltas
                    tool_chunks = delta.get("tool_calls", [])
                    for tc in tool_chunks:
                        idx = tc.get("index", 0)
                        if idx not in active_tools:
                            active_tools[idx] = {
                                "id": tc.get("id", ""),
                                "name": tc.get("function", {}).get("name", ""),
                                "arguments": "",
                            }
                        if tc.get("id"):
                            active_tools[idx]["id"] = tc["id"]
                        fn = tc.get("function", {})
                        if fn.get("name"):
                            active_tools[idx]["name"] = fn["name"]
                        if fn.get("arguments"):
                            active_tools[idx]["arguments"] += fn["arguments"]

                    # Finish reason mapping
                    finish_reason = choice.get("finish_reason")
                    if finish_reason:
                        if finish_reason == "tool_calls":
                            stop_reason = "tool_use"
                        elif finish_reason == "length":
                            stop_reason = "max_tokens"
                        else:
                            stop_reason = "end_turn"

            # Flush any accumulated tool calls
            for idx in sorted(active_tools.keys()):
                tc_data = active_tools[idx]
                args_str = tc_data["arguments"]
                try:
                    parsed_args = json.loads(args_str) if args_str else {}
                except Exception:
                    parsed_args = {}
                yield ToolCallEnd(
                    id=tc_data["id"],
                    name=tc_data["name"],
                    arguments=parsed_args,
                )

            # Ensure usage was emitted at least once
            if not usage_emitted:
                yield UsageEvent(usage=Usage(input_tokens=0, output_tokens=0))

            yield Done(stop_reason=stop_reason)
            done_emitted = True

        except httpx.HTTPStatusError as err:
            raise self._map_http_error(err, req.model) from err
        except httpx.TimeoutException as err:
            raise ProviderError(
                provider=self.name,
                model=req.model,
                status_code=None,
                kind="timeout",
                message=f"Request to {self.name} timed out after {self.timeout_s}s: {err}",
                retryable=True,
            ) from err
        except httpx.RequestError as err:
            raise ProviderError(
                provider=self.name,
                model=req.model,
                status_code=None,
                kind="unavailable",
                message=f"Network error communicating with {self.name}: {err}",
                retryable=True,
            ) from err
        except ProviderError:
            raise
        except Exception as err:
            raise ProviderError(
                provider=self.name,
                model=req.model,
                status_code=None,
                kind="server",
                message=f"Unexpected error in {self.name}: {err}",
                retryable=False,
            ) from err
        finally:
            if not done_emitted:
                # If generator was exited or cancelled unexpectedly
                pass
