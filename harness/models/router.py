"""
BYOK Multi-Provider Model Router & Universal Adapter.
Supports OpenAI, Anthropic, Gemini, DeepSeek, OpenRouter, and Local Inference (Ollama/vLLM/MLX).
Includes Qwen XML tool-calling fallback for open-weight local models.
"""
from __future__ import annotations

import json
import os
from pathlib import Path
import re
from typing import Any, AsyncGenerator, Dict, List, Optional
import uuid

import httpx

from harness.core.types import ModelResponse, ToolCall


class ModelRouter:
    def __init__(
        self,
        default_provider: str = "mock",
        default_model: str = "mock-model",
        api_keys: Optional[Dict[str, str]] = None,
        local_base_url: str = "http://localhost:11434/v1",
    ):
        self.default_provider = default_provider
        self.default_model = default_model
        self.local_base_url = local_base_url
        self.api_keys = api_keys or {
            "openai": os.environ.get("OPENAI_API_KEY", ""),
            "anthropic": os.environ.get("ANTHROPIC_API_KEY", ""),
            "gemini": os.environ.get("GEMINI_API_KEY", ""),
            "deepseek": os.environ.get("DEEPSEEK_API_KEY", ""),
            "openrouter": os.environ.get("OPENROUTER_API_KEY", ""),
        }
        self._mock_responses: List[ModelResponse] = []

    def set_mock_responses(self, responses: List[ModelResponse]) -> None:
        """Injects responses for deterministic offline testing."""
        self._mock_responses = list(responses)

    async def generate(
        self,
        messages: List[Dict[str, Any]],
        tools: Optional[List[Dict[str, Any]]] = None,
        provider: Optional[str] = None,
        model: Optional[str] = None,
        temperature: float = 0.2,
    ) -> ModelResponse:
        prov = provider or self.default_provider
        mdl = model or self.default_model
        tools = tools or []

        # 1. Mock mode
        if prov == "mock":
            if self._mock_responses:
                return self._mock_responses.pop(0)
            return ModelResponse(
                content="[Mock Response] Task understood and executed.",
                thinking="Processing request offline.",
            )

        # 2. Local mode (Ollama / vLLM / MLX)
        if prov == "local":
            return await self._call_local(mdl, messages, tools, temperature)

        # 3. OpenAI / OpenRouter / DeepSeek (OpenAI compatible)
        if prov in ("openai", "openrouter", "deepseek"):
            return await self._call_openai_compatible(prov, mdl, messages, tools, temperature)

        # 4. Anthropic
        if prov == "anthropic":
            return await self._call_anthropic(mdl, messages, tools, temperature)

        # 5. Google Gemini
        if prov == "gemini":
            return await self._call_gemini(mdl, messages, tools, temperature)

        raise ValueError(f"Unsupported model provider: {prov}")

    async def _call_local(
        self,
        model: str,
        messages: List[Dict[str, Any]],
        tools: List[Dict[str, Any]],
        temperature: float,
    ) -> ModelResponse:
        """Employs Qwen XML adapter for robust tool calling on local models."""
        async with httpx.AsyncClient(timeout=120.0) as client:
            formatted_messages = list(messages)
            if tools:
                tools_doc = json.dumps(tools, indent=2)
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
            tool_calls: List[ToolCall] = []
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

    async def _call_openai_compatible(
        self,
        provider: str,
        model: str,
        messages: List[Dict[str, Any]],
        tools: List[Dict[str, Any]],
        temperature: float,
    ) -> ModelResponse:
        base_urls = {
            "openai": "https://api.openai.com/v1",
            "deepseek": "https://api.deepseek.com/v1",
            "openrouter": "https://openrouter.ai/api/v1",
        }
        api_key = self.api_keys.get(provider) or os.environ.get(f"{provider.upper()}_API_KEY", "")
        if not api_key:
            raise ValueError(f"Missing API key for provider: {provider}")

        headers = {
            "Authorization": f"Bearer {api_key}",
            "Content-Type": "application/json",
        }
        payload: Dict[str, Any] = {
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

        async with httpx.AsyncClient(timeout=120.0) as client:
            resp = await client.post(f"{base_urls[provider]}/chat/completions", headers=headers, json=payload)
            resp.raise_for_status()
            data = resp.json()
            choice = data["choices"][0]["message"]
            content = choice.get("content") or ""
            thinking = choice.get("reasoning_content") or ""

            parsed_tools: List[ToolCall] = []
            if "tool_calls" in choice and choice["tool_calls"]:
                for tc in choice["tool_calls"]:
                    fn = tc.get("function", {})
                    args = fn.get("arguments", "{}")
                    parsed_args = json.loads(args) if isinstance(args, str) else args
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

    async def _call_anthropic(
        self,
        model: str,
        messages: List[Dict[str, Any]],
        tools: List[Dict[str, Any]],
        temperature: float,
    ) -> ModelResponse:
        api_key = self.api_keys.get("anthropic") or os.environ.get("ANTHROPIC_API_KEY", "")
        if not api_key:
            raise ValueError("Missing ANTHROPIC_API_KEY")

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
        payload: Dict[str, Any] = {
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

        async with httpx.AsyncClient(timeout=120.0) as client:
            resp = await client.post("https://api.anthropic.com/v1/messages", headers=headers, json=payload)
            resp.raise_for_status()
            data = resp.json()

            text_blocks = []
            parsed_tools: List[ToolCall] = []
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

    async def _call_gemini(
        self,
        model: str,
        messages: List[Dict[str, Any]],
        tools: List[Dict[str, Any]],
        temperature: float,
    ) -> ModelResponse:
        api_key = self.api_keys.get("gemini") or os.environ.get("GEMINI_API_KEY", "")
        if not api_key:
            raise ValueError("Missing GEMINI_API_KEY")

        # Simplified Gemini REST request
        url = f"https://generativelanguage.googleapis.com/v1beta/models/{model}:generateContent?key={api_key}"
        contents = []
        for m in messages:
            role = "user" if m["role"] in ("user", "system") else "model"
            contents.append({"role": role, "parts": [{"text": m["content"]}]})

        payload = {"contents": contents, "generationConfig": {"temperature": temperature}}
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
