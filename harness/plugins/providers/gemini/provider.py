"""
Google Gemini API Provider implementation (§4, PLG-11).
Absorbs MDL-03 (Gemini) and Decision D6.
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
    TextBlock,
    TextDelta,
    ToolCallEnd,
    ToolResultBlock,
    ToolUseBlock,
    Usage,
    UsageEvent,
)

logger = logging.getLogger(__name__)

DEFAULT_GEMINI_MODELS: list[ModelInfo] = [
    ModelInfo(
        id="gemini-2.0-flash",
        display_name="Gemini 2.0 Flash",
        context_tokens=1048576,
        input_usd_per_mtok=0.10,
        output_usd_per_mtok=0.40,
    ),
    ModelInfo(
        id="gemini-1.5-pro",
        display_name="Gemini 1.5 Pro",
        context_tokens=2097152,
        input_usd_per_mtok=1.25,
        output_usd_per_mtok=5.00,
    ),
    ModelInfo(
        id="gemini-1.5-flash",
        display_name="Gemini 1.5 Flash",
        context_tokens=1048576,
        input_usd_per_mtok=0.075,
        output_usd_per_mtok=0.30,
    ),
]


class GeminiProvider:
    """
    Google Gemini provider implementing streamGenerateContent with function calling,
    role alternation merging, and token usage parsing.
    """

    caps = ProviderCaps(
        native_tools=True,
        streaming=True,
        vision=True,
        cache_hints=True,
        thinking=False,
        json_output=True,
        max_context_tokens=1048576,
    )

    def __init__(
        self,
        name: str = "gemini",
        base_url: str = "https://generativelanguage.googleapis.com/v1beta",
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

    def is_configured(self) -> bool:
        """An API key is required (an injected client counts as configured for tests)."""
        return bool(self.api_key) or self._external_client

    async def is_reachable(self) -> bool | None:
        """Hosted API: not probed."""
        return None

    async def list_models(self) -> list[ModelInfo]:
        """Live ``/models`` when configured, else the built-in catalogue — never a network call without a key."""
        now = time.time()
        if self._cached_models and (now - self._cached_models_ts) < 86400:
            return self._cached_models

        if not self.is_configured():
            self._cached_models = DEFAULT_GEMINI_MODELS
            self._cached_models_ts = now
            return DEFAULT_GEMINI_MODELS

        try:
            headers = {"x-goog-api-key": self.api_key, **self.extra_headers}
            resp = await self._client.get(f"{self.base_url}/models", headers=headers)
            if resp.status_code == 200:
                data = resp.json()
                raw_models = data.get("models", [])
                models: list[ModelInfo] = []
                for m in raw_models:
                    raw_name = m.get("name", "")
                    m_id = raw_name.replace("models/", "")
                    if "gemini" in m_id:
                        models.append(
                            ModelInfo(
                                id=m_id,
                                display_name=m.get("displayName", m_id),
                                context_tokens=m.get("inputTokenLimit", 1048576),
                            )
                        )
                if models:
                    self._cached_models = models
                    self._cached_models_ts = now
                    return models
        except Exception as e:
            logger.debug("Failed to fetch live Gemini models: %s", e)

        self._cached_models = DEFAULT_GEMINI_MODELS
        self._cached_models_ts = now
        return DEFAULT_GEMINI_MODELS

    def _prepare_payload(self, req: ModelRequest) -> dict[str, Any]:
        """Constructs Gemini streamGenerateContent request body."""
        payload: dict[str, Any] = {}

        # 1. System instructions
        system_texts: list[str] = [b.text for b in req.system if b.text]
        for m in req.messages:
            if m.role == Role.SYSTEM:
                for c in m.content:
                    if isinstance(c, TextBlock) and c.text:
                        system_texts.append(c.text)

        if system_texts:
            payload["systemInstruction"] = {
                "parts": [{"text": "\n\n".join(system_texts)}]
            }

        # 2. Tool definitions
        if req.tools:
            function_declarations: list[dict[str, Any]] = []
            for spec in req.tools:
                decl: dict[str, Any] = {
                    "name": spec.name,
                    "description": spec.description,
                }
                if spec.input_schema:
                    decl["parameters"] = spec.input_schema
                function_declarations.append(decl)
            payload["tools"] = [{"functionDeclarations": function_declarations}]

        # 3. Tool ID mapping
        tool_id_to_name: dict[str, str] = {}
        for m in req.messages:
            for c in m.content:
                if isinstance(c, ToolUseBlock):
                    tool_id_to_name[c.id] = c.name

        # 4. Turn contents with consecutive same-role merging
        raw_turns: list[dict[str, Any]] = []
        for m in req.messages:
            if m.role == Role.SYSTEM:
                continue

            gemini_role = "user" if m.role == Role.USER else "model"
            parts: list[dict[str, Any]] = []

            for c in m.content:
                if isinstance(c, TextBlock):
                    if c.text:
                        parts.append({"text": c.text})
                elif isinstance(c, ImageBlock):
                    parts.append({
                        "inlineData": {
                            "mimeType": c.media_type,
                            "data": c.data,
                        }
                    })
                elif isinstance(c, ToolUseBlock):
                    parts.append({
                        "functionCall": {
                            "name": c.name,
                            "args": c.input,
                        }
                    })
                elif isinstance(c, ToolResultBlock):
                    fn_name = tool_id_to_name.get(c.tool_use_id, c.tool_use_id)
                    out_text = "\n".join(b.text for b in c.content if isinstance(b, TextBlock))
                    parts.append({
                        "functionResponse": {
                            "name": fn_name,
                            "response": {
                                "name": fn_name,
                                "content": out_text,
                                "is_error": c.is_error,
                            },
                        }
                    })

            if parts:
                raw_turns.append({"role": gemini_role, "parts": parts})

        # Merge consecutive turns with the same role
        merged_turns: list[dict[str, Any]] = []
        for t in raw_turns:
            if merged_turns and merged_turns[-1]["role"] == t["role"]:
                merged_turns[-1]["parts"].extend(t["parts"])
            else:
                merged_turns.append(t)

        payload["contents"] = merged_turns

        # 5. Generation config
        gen_config: dict[str, Any] = {}
        if req.params.temperature is not None:
            gen_config["temperature"] = req.params.temperature
        if req.params.max_tokens is not None:
            gen_config["maxOutputTokens"] = req.params.max_tokens
        if req.params.extra.get("top_p") is not None:
            gen_config["topP"] = req.params.extra.get("top_p")

        if gen_config:
            payload["generationConfig"] = gen_config

        return payload

    async def generate(self, req: ModelRequest) -> AsyncIterator[ModelEvent]:
        """Streams ModelEvents from Gemini streamGenerateContent."""
        model_id = req.model or "gemini-2.0-flash"
        # Strip provider prefix if present
        if model_id.startswith("gemini/"):
            model_id = model_id[len("gemini/"):]
        elif model_id.startswith("google/"):
            model_id = model_id[len("google/"):]

        payload = self._prepare_payload(req)
        url = f"{self.base_url}/models/{model_id}:streamGenerateContent?alt=sse"
        headers = {
            "Content-Type": "application/json",
            "x-goog-api-key": self.api_key,
            **self.extra_headers,
        }

        try:
            req_network = self._client.build_request("POST", url, json=payload, headers=headers)
            resp = await self._client.send(req_network, stream=True)
        except httpx.TimeoutException as exc:
            raise ProviderError(
                provider=self.name,
                model=model_id,
                kind=ProviderErrorKind.TIMEOUT,
                message=f"Gemini API timeout: {exc}",
                retryable=True,
            ) from exc
        except httpx.NetworkError as exc:
            raise ProviderError(
                provider=self.name,
                model=model_id,
                kind=ProviderErrorKind.NETWORK,
                message=f"Gemini connection failure: {exc}",
                retryable=True,
            ) from exc

        if resp.status_code != 200:
            error_body = ""
            try:
                error_body = (await resp.aread()).decode("utf-8")
            except Exception:
                pass
            await resp.aclose()
            self._handle_error_status(resp.status_code, error_body, model_id)

        try:
            finish_reason = "stop"
            tool_call_idx = 0

            async for line in resp.aiter_lines():
                line = line.strip()
                if not line or not line.startswith("data:"):
                    continue

                raw_json = line[5:].strip()
                if not raw_json:
                    continue

                try:
                    data = json.loads(raw_json)
                except json.JSONDecodeError:
                    continue

                # Parse usageMetadata
                if "usageMetadata" in data:
                    meta = data["usageMetadata"]
                    in_tok = meta.get("promptTokenCount", 0)
                    out_tok = meta.get("candidatesTokenCount", 0)
                    cached_tok = meta.get("cachedContentTokenCount", 0)
                    yield UsageEvent(
                        usage=Usage(
                            input_tokens=in_tok,
                            output_tokens=out_tok,
                            cache_read_tokens=cached_tok,
                            cache_write_tokens=0,
                        )
                    )

                # Parse candidates
                for cand in data.get("candidates", []):
                    if "finishReason" in cand and cand["finishReason"]:
                        finish_reason = cand["finishReason"].lower()

                    content = cand.get("content", {})
                    for part in content.get("parts", []):
                        if "text" in part and part["text"]:
                            yield TextDelta(text=part["text"])

                        if "functionCall" in part:
                            fc = part["functionCall"]
                            tool_call_idx += 1
                            call_id = f"call_{tool_call_idx}_{int(time.time() * 1000)}"
                            yield ToolCallEnd(
                                id=call_id,
                                name=fc.get("name", ""),
                                arguments=fc.get("args", {}),
                            )

            yield Done(stop_reason=finish_reason)
        finally:
            await resp.aclose()

    def _handle_error_status(self, status: int, error_body: str, model: str) -> None:
        """Maps HTTP status and error body into typed ProviderError."""
        message = error_body
        try:
            data = json.loads(error_body)
            if "error" in data:
                err = data["error"]
                message = err.get("message", error_body)
        except Exception:
            pass

        if status == 400:
            raise ProviderError(
                provider=self.name,
                model=model,
                kind="invalid_request",
                message=f"Gemini Bad Request: {message}",
                status_code=400,
                retryable=False,
            )
        elif status in (401, 403):
            raise ProviderError(
                provider=self.name,
                model=model,
                kind="auth",
                message=f"Gemini Authentication/Permission Failure: {message}",
                status_code=status,
                retryable=False,
            )
        elif status == 404:
            raise ProviderError(
                provider=self.name,
                model=model,
                kind="model_not_found",
                message=f"Gemini Model Not Found: {message}",
                status_code=404,
                retryable=False,
            )
        elif status == 429:
            raise ProviderError(
                provider=self.name,
                model=model,
                kind="rate_limit",
                message=f"Gemini Rate Limit Exceeded: {message}",
                status_code=429,
                retryable=True,
            )
        elif status >= 500:
            raise ProviderError(
                provider=self.name,
                model=model,
                kind="server_error",
                message=f"Gemini Internal Server Error ({status}): {message}",
                status_code=status,
                retryable=True,
            )
        else:
            raise ProviderError(
                provider=self.name,
                model=model,
                kind="invalid_request",
                message=f"Gemini Request Failed ({status}): {message}",
                status_code=status,
                retryable=False,
            )
