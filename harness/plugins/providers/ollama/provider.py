"""
Ollama Provider implementation (§4, PLG-12).
Supports local models via /v1/chat/completions with native tools and automatic XML tool fallback.
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
    ThinkingDelta,
    ToolCallEnd,
    ToolResultBlock,
    ToolUseBlock,
    Usage,
    UsageEvent,
)

from .xml_protocol import XmlStreamParser, format_tool_guidance

logger = logging.getLogger(__name__)

DEFAULT_OLLAMA_MODELS: list[ModelInfo] = [
    ModelInfo(
        id="llama3.2:latest",
        display_name="Llama 3.2 (Local)",
        context_tokens=131072,
        input_usd_per_mtok=0.0,
        output_usd_per_mtok=0.0,
    ),
    ModelInfo(
        id="qwen2.5-coder:latest",
        display_name="Qwen 2.5 Coder (Local)",
        context_tokens=131072,
        input_usd_per_mtok=0.0,
        output_usd_per_mtok=0.0,
    ),
    ModelInfo(
        id="deepseek-r1:latest",
        display_name="DeepSeek R1 (Local)",
        context_tokens=131072,
        input_usd_per_mtok=0.0,
        output_usd_per_mtok=0.0,
    ),
]


class OllamaProvider:
    """
    Local Ollama inference provider with dual-mode tool invocation:
    Native OpenAPI tools first, with automatic XML <tool_call> fallback on HTTP 400.
    """

    caps = ProviderCaps(
        native_tools=True,
        streaming=True,
        vision=True,
        cache_hints=False,
        thinking=True,
        json_output=True,
        max_context_tokens=131072,
    )

    #: How long one ``/api/tags`` verdict (up or down, and the installed models) is trusted.
    PROBE_TTL_S: float = 30.0
    #: Budget for the liveness probe — a local daemon answers in milliseconds or not at all.
    PROBE_TIMEOUT_S: float = 0.3

    def __init__(
        self,
        name: str = "ollama",
        base_url: str = "http://localhost:11434",
        timeout_s: float = 120.0,
        client: httpx.AsyncClient | None = None,
    ) -> None:
        self.name = name
        self.base_url = base_url.rstrip("/")
        self.timeout_s = timeout_s
        self._external_client = client is not None
        self._client: httpx.AsyncClient = client or httpx.AsyncClient(
            timeout=httpx.Timeout(
                connect=10.0,
                read=timeout_s,
                write=30.0,
                pool=10.0,
            )
        )
        # One probe feeds both is_reachable() and list_models(): (timestamp, up?, installed models)
        self._probe_ts: float = 0.0
        self._probe_up: bool | None = None
        self._installed: list[ModelInfo] = []

    async def aclose(self) -> None:
        if not self._external_client and not self._client.is_closed:
            await self._client.aclose()

    def is_configured(self) -> bool:
        """Ollama needs no credentials; whether it is *running* is `is_reachable()`'s answer."""
        return True

    async def _probe(self) -> bool:
        """``GET /api/tags`` with a short timeout, cached for ``PROBE_TTL_S``. Never raises."""
        now = time.time()
        if self._probe_up is not None and (now - self._probe_ts) < self.PROBE_TTL_S:
            return self._probe_up
        up = False
        installed: list[ModelInfo] = []
        try:
            resp = await self._client.get(f"{self.base_url}/api/tags", timeout=self.PROBE_TIMEOUT_S)
            if resp.status_code == 200:
                up = True
                for m in resp.json().get("models", []):
                    m_name = m.get("name", "")
                    if m_name:
                        installed.append(
                            ModelInfo(
                                id=m_name,
                                display_name=f"{m_name} (Local)",
                                context_tokens=131072,
                                input_usd_per_mtok=0.0,
                                output_usd_per_mtok=0.0,
                            )
                        )
        except Exception as e:  # noqa: BLE001 — "down" is an answer, not an error
            logger.debug("Ollama %s unreachable: %s", self.base_url, e)
        self._probe_ts, self._probe_up, self._installed = now, up, installed
        return up

    async def is_reachable(self) -> bool:
        return await self._probe()

    async def list_models(self) -> list[ModelInfo]:
        """Installed models when Ollama is up; the built-in catalogue (flagged unreachable upstream) when not."""
        if await self._probe() and self._installed:
            return list(self._installed)
        return list(DEFAULT_OLLAMA_MODELS)

    def _prepare_payload(self, req: ModelRequest, use_xml_tools: bool = False) -> dict[str, Any]:
        """Prepares OpenAI-compatible payload, either with native tools or XML prompt fallback."""
        messages: list[dict[str, Any]] = []

        # System messages
        system_texts: list[str] = [b.text for b in req.system if b.text]
        if use_xml_tools and req.tools:
            system_texts.append(format_tool_guidance(req.tools))

        if system_texts:
            messages.append({"role": "system", "content": "\n\n".join(system_texts)})

        # History messages
        for m in req.messages:
            if m.role == Role.SYSTEM:
                content_text = "\n".join(b.text for b in m.content if isinstance(b, TextBlock))
                if content_text:
                    messages.append({"role": "system", "content": content_text})
            elif m.role == Role.USER:
                parts: list[dict[str, Any]] = []
                text_accum: list[str] = []
                for c in m.content:
                    if isinstance(c, TextBlock):
                        text_accum.append(c.text)
                    elif isinstance(c, ImageBlock):
                        parts.append({
                            "type": "image_url",
                            "image_url": {"url": f"data:{c.media_type};base64,{c.data}"},
                        })
                    elif isinstance(c, ToolResultBlock):
                        out_text = "\n".join(b.text for b in c.content if isinstance(b, TextBlock))
                        if use_xml_tools:
                            text_accum.append(
                                f"<tool_response>\n{{\"name\": \"{c.tool_use_id}\", \"output\": {json.dumps(out_text)}}}\n</tool_response>"
                            )
                        else:
                            messages.append({
                                "role": "tool",
                                "tool_call_id": c.tool_use_id,
                                "content": out_text,
                            })

                if text_accum:
                    if parts:
                        parts.insert(0, {"type": "text", "text": "\n".join(text_accum)})
                        messages.append({"role": "user", "content": parts})
                    else:
                        messages.append({"role": "user", "content": "\n".join(text_accum)})
                elif parts:
                    messages.append({"role": "user", "content": parts})

            elif m.role == Role.ASSISTANT:
                text_accum = []
                native_tool_calls: list[dict[str, Any]] = []
                for c in m.content:
                    if isinstance(c, TextBlock):
                        text_accum.append(c.text)
                    elif isinstance(c, ToolUseBlock):
                        if use_xml_tools:
                            text_accum.append(
                                f"<tool_call>\n{{\"name\": \"{c.name}\", \"arguments\": {json.dumps(c.input)}}}\n</tool_call>"
                            )
                        else:
                            native_tool_calls.append({
                                "id": c.id,
                                "type": "function",
                                "function": {
                                    "name": c.name,
                                    "arguments": json.dumps(c.input),
                                },
                            })

                msg_obj: dict[str, Any] = {"role": "assistant"}
                if text_accum:
                    msg_obj["content"] = "\n".join(text_accum)
                if native_tool_calls and not use_xml_tools:
                    msg_obj["tool_calls"] = native_tool_calls
                messages.append(msg_obj)

        model_id = req.model or "llama3.2:latest"
        payload: dict[str, Any] = {
            "model": model_id,
            "messages": messages,
            "stream": True,
        }

        if not use_xml_tools and req.tools:
            tools_payload = []
            for t in req.tools:
                tools_payload.append({
                    "type": "function",
                    "function": {
                        "name": t.name,
                        "description": t.description,
                        "parameters": t.input_schema,
                    },
                })
            payload["tools"] = tools_payload

        if req.params.temperature is not None:
            payload["temperature"] = req.params.temperature
        if req.params.max_tokens is not None:
            payload["max_tokens"] = req.params.max_tokens

        return payload

    async def generate(self, req: ModelRequest) -> AsyncIterator[ModelEvent]:
        """
        Streams events from Ollama.
        Attempts native tool calling; if HTTP 400 is returned and tools are requested,
        automatically falls back to XML <tool_call> protocol.
        """
        model_id = req.model or "llama3.2:latest"
        url = f"{self.base_url}/v1/chat/completions"
        headers = {"Content-Type": "application/json"}

        # First attempt: native tools
        payload = self._prepare_payload(req, use_xml_tools=False)

        try:
            req_obj = self._client.build_request("POST", url, json=payload, headers=headers)
            resp = await self._client.send(req_obj, stream=True)
        except httpx.TimeoutException as exc:
            raise ProviderError(
                provider=self.name,
                model=model_id,
                kind="timeout",
                message=f"Ollama request timed out: {exc}",
                retryable=True,
            ) from exc
        except httpx.NetworkError as exc:
            raise ProviderError(
                provider=self.name,
                model=model_id,
                kind="network",
                message=f"Ollama connection error: {exc}",
                retryable=True,
            ) from exc

        # Check for HTTP 400 with tool errors
        if resp.status_code == 400 and req.tools:
            err_body = ""
            try:
                err_body = (await resp.aread()).decode("utf-8")
            except Exception:
                pass
            await resp.aclose()

            # Retry with XML fallback
            logger.info("Ollama native tools returned 400; retrying with XML fallback protocol: %s", err_body)
            xml_payload = self._prepare_payload(req, use_xml_tools=True)
            try:
                req_obj = self._client.build_request("POST", url, json=xml_payload, headers=headers)
                resp = await self._client.send(req_obj, stream=True)
            except Exception as exc:
                raise ProviderError(
                    provider=self.name,
                    model=model_id,
                    kind="network",
                    message=f"Ollama XML retry failed: {exc}",
                    retryable=True,
                ) from exc

            if resp.status_code != 200:
                body = ""
                try:
                    body = (await resp.aread()).decode("utf-8")
                except Exception:
                    pass
                await resp.aclose()
                raise ProviderError(
                    provider=self.name,
                    model=model_id,
                    kind="server_error" if resp.status_code >= 500 else "invalid_request",
                    message=f"Ollama XML fallback error ({resp.status_code}): {body}",
                    status_code=resp.status_code,
                    retryable=resp.status_code >= 500,
                )

            # Stream response through XmlStreamParser
            async for evt in self._stream_xml_response(resp):
                yield evt
            return

        if resp.status_code != 200:
            err_body = ""
            try:
                err_body = (await resp.aread()).decode("utf-8")
            except Exception:
                pass
            await resp.aclose()
            raise ProviderError(
                provider=self.name,
                model=model_id,
                kind="server_error" if resp.status_code >= 500 else "invalid_request",
                message=f"Ollama error ({resp.status_code}): {err_body}",
                status_code=resp.status_code,
                retryable=resp.status_code >= 500,
            )

        # Stream native response
        async for evt in self._stream_native_response(resp):
            yield evt

    async def _stream_native_response(self, resp: httpx.Response) -> AsyncIterator[ModelEvent]:
        """Streams standard OpenAI-compatible SSE events with <think> de-multiplexing."""
        finish_reason = "stop"
        current_tool_calls: dict[int, dict[str, Any]] = {}
        think_parser = XmlStreamParser()

        try:
            async for line in resp.aiter_lines():
                line = line.strip()
                if not line or not line.startswith("data:"):
                    continue

                raw = line[5:].strip()
                if raw == "[DONE]":
                    break

                try:
                    chunk = json.loads(raw)
                except json.JSONDecodeError:
                    continue

                # Parse usage
                if "usage" in chunk and chunk["usage"]:
                    u = chunk["usage"]
                    yield UsageEvent(
                        usage=Usage(
                            input_tokens=u.get("prompt_tokens", 0),
                            output_tokens=u.get("completion_tokens", 0),
                        )
                    )

                choices = chunk.get("choices", [])
                if not choices:
                    continue

                choice = choices[0]
                if choice.get("finish_reason"):
                    finish_reason = choice["finish_reason"]

                delta = choice.get("delta", {})

                # DeepSeek-R1 reasoning_content delta
                if "reasoning_content" in delta and delta["reasoning_content"]:
                    yield ThinkingDelta(text=delta["reasoning_content"])

                # Standard text delta (parse think tags if present)
                if "content" in delta and delta["content"]:
                    text = delta["content"]
                    for parsed_evt in think_parser.feed(text):
                        yield parsed_evt

                # Native tool calls
                if "tool_calls" in delta and delta["tool_calls"]:
                    for tc in delta["tool_calls"]:
                        idx = tc.get("index", 0)
                        if idx not in current_tool_calls:
                            current_tool_calls[idx] = {
                                "id": tc.get("id", f"call_{idx}"),
                                "name": "",
                                "arguments": "",
                            }
                        if tc.get("id"):
                            current_tool_calls[idx]["id"] = tc["id"]
                        fn = tc.get("function", {})
                        if fn.get("name"):
                            current_tool_calls[idx]["name"] += fn["name"]
                        if fn.get("arguments"):
                            current_tool_calls[idx]["arguments"] += fn["arguments"]

            for remaining_evt in think_parser.flush():
                yield remaining_evt

            for _, tc_data in sorted(current_tool_calls.items()):
                parsed_args: dict[str, Any] = {}
                raw_args = tc_data["arguments"].strip()
                if raw_args:
                    try:
                        parsed_args = json.loads(raw_args)
                    except Exception:
                        parsed_args = {"raw": raw_args}
                yield ToolCallEnd(
                    id=tc_data["id"],
                    name=tc_data["name"],
                    arguments=parsed_args,
                )

            yield Done(stop_reason=finish_reason)
        finally:
            await resp.aclose()

    async def _stream_xml_response(self, resp: httpx.Response) -> AsyncIterator[ModelEvent]:
        """Streams response through XML fallback parser."""
        finish_reason = "stop"
        parser = XmlStreamParser()

        try:
            async for line in resp.aiter_lines():
                line = line.strip()
                if not line or not line.startswith("data:"):
                    continue

                raw = line[5:].strip()
                if raw == "[DONE]":
                    break

                try:
                    chunk = json.loads(raw)
                except json.JSONDecodeError:
                    continue

                if "usage" in chunk and chunk["usage"]:
                    u = chunk["usage"]
                    yield UsageEvent(
                        usage=Usage(
                            input_tokens=u.get("prompt_tokens", 0),
                            output_tokens=u.get("completion_tokens", 0),
                        )
                    )

                choices = chunk.get("choices", [])
                if not choices:
                    continue

                choice = choices[0]
                if choice.get("finish_reason"):
                    finish_reason = choice["finish_reason"]

                delta = choice.get("delta", {})

                if "reasoning_content" in delta and delta["reasoning_content"]:
                    yield ThinkingDelta(text=delta["reasoning_content"])

                if "content" in delta and delta["content"]:
                    for evt in parser.feed(delta["content"]):
                        yield evt

            for remaining_evt in parser.flush():
                yield remaining_evt

            yield Done(stop_reason=finish_reason)
        finally:
            await resp.aclose()
