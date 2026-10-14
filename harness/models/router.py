"""
BYOK Multi-Provider Model Router & Universal Adapter.
Supports OpenAI, Anthropic, Gemini, DeepSeek, OpenRouter, and Local Inference (Ollama/vLLM/MLX).
Includes Qwen XML tool-calling fallback for open-weight local models.
"""
from __future__ import annotations

import json
import os
import re
import uuid
from pathlib import Path
from typing import Any

import httpx

from harness.core.types import ModelResponse, ToolCall
from harness.models.mock_provider import MockProvider


class ProviderError(Exception):
    def __init__(
        self,
        provider: str,
        model: str,
        status_code: int | None,
        message: str,
        retryable: bool = False,
    ):
        super().__init__(
            f"[{provider}/{model}] {message}"
            + (f" (HTTP {status_code})" if status_code else "")
        )
        self.provider = provider
        self.model = model
        self.status_code = status_code
        self.message = message
        self.retryable = retryable


class ModelRouter:
    def __init__(
        self,
        default_provider: str = "mock",
        default_model: str = "mock-model",
        api_keys: dict[str, str] | None = None,
        local_base_url: str = "http://localhost:11434/v1",
        config_dir: Path | None = None,
    ):
        self.config_dir = Path(config_dir) if config_dir else Path(os.environ.get("HARNESS_CONFIG_DIR", Path.home() / ".harness"))
        config = self._load_config()
        saved_keys = config.get("api_keys", {})

        self.api_keys = api_keys or {
            "openai": saved_keys.get("openai") or os.environ.get("OPENAI_API_KEY", ""),
            "anthropic": saved_keys.get("anthropic") or os.environ.get("ANTHROPIC_API_KEY", ""),
            "gemini": saved_keys.get("gemini") or os.environ.get("GEMINI_API_KEY", ""),
            "deepseek": saved_keys.get("deepseek") or os.environ.get("DEEPSEEK_API_KEY", ""),
            "openrouter": saved_keys.get("openrouter") or os.environ.get("OPENROUTER_API_KEY", ""),
        }
        
        # If user configured a default provider in config, use it
        cfg_provider = config.get("default_provider")
        cfg_model = config.get("default_model")
        
        # Auto-detect provider if any key is set
        if cfg_provider:
            self.default_provider = cfg_provider
            self.default_model = cfg_model or "default"
        elif self.api_keys.get("openrouter"):
            self.default_provider = "openrouter"
            self.default_model = "deepseek/deepseek-chat"
        elif self.api_keys.get("openai"):
            self.default_provider = "openai"
            self.default_model = "gpt-4o-mini"
        elif self.api_keys.get("anthropic"):
            self.default_provider = "anthropic"
            self.default_model = "claude-3-7-sonnet"
        elif self.api_keys.get("deepseek"):
            self.default_provider = "deepseek"
            self.default_model = "deepseek-reasoner"
        elif self.api_keys.get("gemini"):
            self.default_provider = "gemini"
            self.default_model = "gemini-2.0-flash"
        else:
            self.default_provider = default_provider
            self.default_model = default_model

        self.local_base_url = config.get("local_base_url") or local_base_url
        self.mock_provider = MockProvider()
        self._mock_responses: list[ModelResponse] = self.mock_provider._responses

    def _config_path(self) -> Path:
        return self.config_dir / "config.json"

    def _load_config(self) -> dict[str, Any]:
        p = self._config_path()
        if p.exists():
            try:
                return json.loads(p.read_text(encoding="utf-8"))
            except Exception:
                return {}
        return {}

    def save_config(
        self,
        keys: dict[str, str] | None = None,
        default_provider: str | None = None,
        default_model: str | None = None,
    ):
        self.config_dir.mkdir(parents=True, exist_ok=True)
        try:
            os.chmod(self.config_dir, 0o700)
        except Exception:
            pass

        cfg = self._load_config()
        if keys:
            cur_keys = cfg.get("api_keys", {})
            cur_keys.update(keys)
            cfg["api_keys"] = cur_keys
            self.api_keys.update(keys)
        if default_provider:
            cfg["default_provider"] = default_provider
            self.default_provider = default_provider
        if default_model:
            cfg["default_model"] = default_model
            self.default_model = default_model

        p = self._config_path()
        content = json.dumps(cfg, indent=2).encode("utf-8")
        fd = os.open(p, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
        with open(fd, "wb") as f:
            f.write(content)

    def set_mock_responses(self, responses: list[ModelResponse]) -> None:
        """Injects responses for deterministic offline testing."""
        self.mock_provider.set_responses(responses)
        self._mock_responses = self.mock_provider._responses

    async def generate(
        self,
        messages: list[dict[str, Any]],
        tools: list[dict[str, Any]] | None = None,
        provider: str | None = None,
        model: str | None = None,
        temperature: float = 0.2,
    ) -> ModelResponse:
        tools = tools or []
        mdl = model or self.default_model

        if provider:
            prov = provider
        elif "/" in mdl or mdl.startswith("openrouter"):
            prov = "openrouter"
        elif mdl.startswith("claude"):
            prov = "anthropic"
        elif mdl.startswith("gpt-") or mdl.startswith("o1") or mdl.startswith("o3"):
            prov = "openai"
        elif mdl.startswith("gemini"):
            prov = "gemini"
        elif mdl.startswith("deepseek"):
            prov = "deepseek"
        elif mdl in ("local", "ollama"):
            prov = "local"
        else:
            prov = self.default_provider

        # 1. Hermetic mock mode
        if prov == "mock":
            return self.mock_provider.generate(messages)

        # 2. Check API key presence for commercial frontier models
        if prov in ("anthropic", "openai", "gemini", "deepseek", "openrouter"):
            if not self.api_keys.get(prov):
                raise ProviderError(
                    provider=prov,
                    model=mdl,
                    status_code=None,
                    message=f"No API key configured for provider '{prov}'",
                    retryable=False,
                )

        # 3. Local mode (Ollama / vLLM / MLX)
        if prov == "local":
            return await self._call_local(mdl, messages, tools, temperature)

        # 4. OpenAI / OpenRouter / DeepSeek (OpenAI compatible)
        if prov in ("openai", "openrouter", "deepseek"):
            return await self._call_openai_compatible(prov, mdl, messages, tools, temperature)

        # 5. Anthropic
        if prov == "anthropic":
            return await self._call_anthropic(mdl, messages, tools, temperature)

        # 6. Google Gemini
        if prov == "gemini":
            return await self._call_gemini(mdl, messages, tools, temperature)

        raise ProviderError(
            provider=prov,
            model=mdl,
            status_code=None,
            message=f"Unsupported model provider: '{prov}'",
            retryable=False,
        )

    async def _call_local(
        self,
        model: str,
        messages: list[dict[str, Any]],
        tools: list[dict[str, Any]],
        temperature: float,
    ) -> ModelResponse:
        """Employs Qwen XML adapter for robust tool calling on local models."""
        try:
            async with httpx.AsyncClient(timeout=120.0) as client:
                formatted_messages = list(messages)
                if tools:
                    instructions = (
                        "\n\n# Tool Calling Format:\n"
                        "If you need to execute a tool, write:\n"
                        "<tool_call>\n"
                        '{"name": "tool_name", "arguments": {"arg1": "val1"}}\n'
                        "</tool_call>\n"
                    )
                    if formatted_messages and formatted_messages[0]["role"] == "system":
                        formatted_messages[0] = {
                            "role": "system",
                            "content": formatted_messages[0]["content"] + instructions,
                        }

                payload = {
                    "model": model,
                    "messages": formatted_messages,
                    "temperature": temperature,
                }
                resp = await client.post(f"{self.local_base_url}/chat/completions", json=payload)
                resp.raise_for_status()
                data = resp.json()
                raw_content = data["choices"][0]["message"]["content"]

                # Parse XML tool call
                tool_calls: list[ToolCall] = []
                clean_content = raw_content

                tool_match = re.search(r"<tool_call>\s*(.*?)\s*</tool_call>", raw_content, re.DOTALL)
                if tool_match:
                    try:
                        call_dict = json.loads(tool_match.group(1))
                        tool_calls.append(
                            ToolCall(
                                id=f"call_{uuid.uuid4().hex[:8]}",
                                name=call_dict.get("name", ""),
                                arguments=call_dict.get("arguments", {}),
                            )
                        )
                        clean_content = raw_content[: tool_match.start()].strip()
                    except Exception:
                        pass

                return ModelResponse(content=clean_content, tool_calls=tool_calls, raw_response=data)
        except httpx.HTTPStatusError as e:
            status = e.response.status_code
            raise ProviderError(
                provider="local",
                model=model,
                status_code=status,
                message=f"HTTP {status} from local endpoint {self.local_base_url}: {e.response.text}",
                retryable=status in (429, 500, 502, 503, 504),
            ) from e
        except httpx.RequestError as e:
            raise ProviderError(
                provider="local",
                model=model,
                status_code=None,
                message=f"Network error contacting local inference at {self.local_base_url}: {e}",
                retryable=True,
            ) from e
        except Exception as e:
            raise ProviderError(
                provider="local",
                model=model,
                status_code=None,
                message=f"Unexpected error calling local inference: {e}",
                retryable=False,
            ) from e

    async def _call_openai_compatible(
        self,
        provider: str,
        model: str,
        messages: list[dict[str, Any]],
        tools: list[dict[str, Any]],
        temperature: float,
    ) -> ModelResponse:
        base_urls = {
            "openai": "https://api.openai.com/v1",
            "deepseek": "https://api.deepseek.com/v1",
            "openrouter": "https://openrouter.ai/api/v1",
        }
        api_key = self.api_keys.get(provider) or os.environ.get(f"{provider.upper()}_API_KEY", "")
        if not api_key:
            raise ProviderError(
                provider=provider,
                model=model,
                status_code=None,
                message=f"Missing API key for provider: {provider}",
                retryable=False,
            )

        headers = {
            "Authorization": f"Bearer {api_key}",
            "Content-Type": "application/json",
        }
        if provider == "openrouter":
            headers["HTTP-Referer"] = "https://github.com/knarayanareddy/Harness-Zero"
            headers["X-Title"] = "Harness-Zero Sovereign Agent"

        payload: dict[str, Any] = {
            "model": model,
            "messages": messages,
            "temperature": temperature,
        }

        if tools:
            payload["tools"] = [
                {
                    "type": "function",
                    "function": {
                        "name": t.get("name", ""),
                        "description": t.get("description", ""),
                        "parameters": t.get("parameters", {}),
                    },
                }
                for t in tools
            ]

        try:
            async with httpx.AsyncClient(timeout=60.0) as client:
                resp = await client.post(f"{base_urls[provider]}/chat/completions", headers=headers, json=payload)
                resp.raise_for_status()
                data = resp.json()
                choice = data["choices"][0]["message"]
                content = choice.get("content") or ""
                thinking = choice.get("reasoning_content") or choice.get("reasoning") or ""
                if not content and thinking and not choice.get("tool_calls"):
                    content = thinking

                parsed_tools: list[ToolCall] = []
                if "tool_calls" in choice and choice["tool_calls"]:
                    for tc in choice["tool_calls"]:
                        fn = tc.get("function", {})
                        args = fn.get("arguments", "{}")
                        try:
                            parsed_args = json.loads(args) if isinstance(args, str) else (args or {})
                        except Exception:
                            parsed_args = {}
                        parsed_tools.append(
                            ToolCall(
                                id=tc.get("id", f"call_{uuid.uuid4().hex[:8]}"),
                                name=fn.get("name", ""),
                                arguments=parsed_args,
                            )
                        )

                return ModelResponse(
                    content=content,
                    thinking=thinking,
                    tool_calls=parsed_tools,
                    raw_response=data,
                )
        except httpx.HTTPStatusError as e:
            status = e.response.status_code
            raise ProviderError(
                provider=provider,
                model=model,
                status_code=status,
                message=f"HTTP {status} from {provider}: {e.response.text}",
                retryable=status in (429, 500, 502, 503, 504),
            ) from e
        except httpx.RequestError as e:
            raise ProviderError(
                provider=provider,
                model=model,
                status_code=None,
                message=f"Network error contacting {provider}: {e}",
                retryable=True,
            ) from e
        except Exception as e:
            raise ProviderError(
                provider=provider,
                model=model,
                status_code=None,
                message=f"Unexpected error calling {provider}: {e}",
                retryable=False,
            ) from e

    async def _call_anthropic(
        self,
        model: str,
        messages: list[dict[str, Any]],
        tools: list[dict[str, Any]],
        temperature: float,
    ) -> ModelResponse:
        api_key = self.api_keys.get("anthropic") or os.environ.get("ANTHROPIC_API_KEY", "")
        if not api_key:
            raise ProviderError(
                provider="anthropic",
                model=model,
                status_code=None,
                message="Missing ANTHROPIC_API_KEY",
                retryable=False,
            )

        system_prompt = ""
        user_assistant_messages = []
        for m in messages:
            if m["role"] == "system":
                system_prompt += m["content"] + "\n\n"
            else:
                user_assistant_messages.append({"role": m["role"], "content": m["content"]})

        headers = {
            "x-api-key": api_key,
            "anthropic-version": "2023-06-01",
            "content-type": "application/json",
        }
        payload: dict[str, Any] = {
            "model": model,
            "messages": user_assistant_messages,
            "max_tokens": 4096,
            "temperature": temperature,
        }
        if system_prompt:
            payload["system"] = system_prompt.strip()

        if tools:
            payload["tools"] = [
                {
                    "name": t.get("name", ""),
                    "description": t.get("description", ""),
                    "input_schema": t.get("parameters", {}),
                }
                for t in tools
            ]

        try:
            async with httpx.AsyncClient(timeout=120.0) as client:
                resp = await client.post("https://api.anthropic.com/v1/messages", headers=headers, json=payload)
                resp.raise_for_status()
                data = resp.json()

                text_blocks = []
                parsed_tools: list[ToolCall] = []
                for block in data.get("content", []):
                    if block.get("type") == "text":
                        text_blocks.append(block.get("text", ""))
                    elif block.get("type") == "tool_use":
                        parsed_tools.append(
                            ToolCall(
                                id=block.get("id", f"call_{uuid.uuid4().hex[:8]}"),
                                name=block.get("name", ""),
                                arguments=block.get("input", {}),
                            )
                        )

                return ModelResponse(
                    content="\n".join(text_blocks),
                    tool_calls=parsed_tools,
                    raw_response=data,
                )
        except httpx.HTTPStatusError as e:
            status = e.response.status_code
            raise ProviderError(
                provider="anthropic",
                model=model,
                status_code=status,
                message=f"HTTP {status} from Anthropic: {e.response.text}",
                retryable=status in (429, 500, 502, 503, 504),
            ) from e
        except httpx.RequestError as e:
            raise ProviderError(
                provider="anthropic",
                model=model,
                status_code=None,
                message=f"Network error contacting Anthropic: {e}",
                retryable=True,
            ) from e
        except Exception as e:
            raise ProviderError(
                provider="anthropic",
                model=model,
                status_code=None,
                message=f"Unexpected error calling Anthropic: {e}",
                retryable=False,
            ) from e

    async def _call_gemini(
        self,
        model: str,
        messages: list[dict[str, Any]],
        tools: list[dict[str, Any]],
        temperature: float,
    ) -> ModelResponse:
        api_key = self.api_keys.get("gemini") or os.environ.get("GEMINI_API_KEY", "")
        if not api_key:
            raise ProviderError(
                provider="gemini",
                model=model,
                status_code=None,
                message="Missing GEMINI_API_KEY",
                retryable=False,
            )

        url = f"https://generativelanguage.googleapis.com/v1beta/models/{model}:generateContent?key={api_key}"
        contents = []
        for m in messages:
            role = "user" if m["role"] in ("user", "system") else "model"
            contents.append({"role": role, "parts": [{"text": m["content"]}]})

        payload = {"contents": contents, "generationConfig": {"temperature": temperature}}
        try:
            async with httpx.AsyncClient(timeout=120.0) as client:
                resp = await client.post(url, json=payload)
                resp.raise_for_status()
                data = resp.json()
                candidates = data.get("candidates", [])
                text = ""
                if candidates:
                    parts = candidates[0].get("content", {}).get("parts", [])
                    text = "".join(p.get("text", "") for p in parts)

                return ModelResponse(content=text, raw_response=data)
        except httpx.HTTPStatusError as e:
            status = e.response.status_code
            raise ProviderError(
                provider="gemini",
                model=model,
                status_code=status,
                message=f"HTTP {status} from Gemini: {e.response.text}",
                retryable=status in (429, 500, 502, 503, 504),
            ) from e
        except httpx.RequestError as e:
            raise ProviderError(
                provider="gemini",
                model=model,
                status_code=None,
                message=f"Network error contacting Gemini: {e}",
                retryable=True,
            ) from e
        except Exception as e:
            raise ProviderError(
                provider="gemini",
                model=model,
                status_code=None,
                message=f"Unexpected error calling Gemini: {e}",
                retryable=False,
            ) from e

