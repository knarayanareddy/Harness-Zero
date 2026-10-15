"""
Tests for Ollama Provider Plugin (PLG-12).
Verifies native tool calling, XML fallback on HTTP 400, think tag parsing, and ProviderKit conformance.
"""
from __future__ import annotations

import json
import time
from pathlib import Path

import httpx
import pytest

from harness.kernel.bus import EventBus
from harness.kernel.registry import PluginRegistry
from harness.kernel.secrets import Secrets
from harness.kernel.settings import SettingsStore
from harness.plugins.providers.ollama.provider import DEFAULT_OLLAMA_MODELS, OllamaProvider
from harness.sdk.testing import ProviderKit
from harness.sdk.types import (
    Done,
    Message,
    ModelParams,
    ModelRequest,
    Role,
    TextBlock,
    ThinkingDelta,
    ToolCallEnd,
    ToolSpec,
)


def make_mock_ollama_transport(
    chat_responses: list[tuple[int, list[str] | str]],
    tags_models: list[str] | None = None,
) -> tuple[httpx.MockTransport, list[httpx.Request]]:
    """Creates a MockTransport for Ollama simulating sequential chat responses and /api/tags."""
    captured: list[httpx.Request] = []
    response_queue = list(chat_responses)

    def handler(request: httpx.Request) -> httpx.Response:
        captured.append(request)

        if request.url.path.endswith("/api/tags"):
            models_list = tags_models or ["llama3.2:latest", "qwen2.5-coder:latest"]
            return httpx.Response(
                200,
                json={"models": [{"name": m} for m in models_list]},
            )

        if not response_queue:
            return httpx.Response(500, text="No mock responses left")

        status, body = response_queue.pop(0) if len(response_queue) > 1 else response_queue[0]
        if status != 200:
            return httpx.Response(status, text=body if isinstance(body, str) else "Error")

        lines = body if isinstance(body, list) else [body]
        content = "\n".join(lines).encode("utf-8")
        return httpx.Response(
            200,
            headers={"Content-Type": "text/event-stream"},
            content=content,
        )

    return httpx.MockTransport(handler), captured


@pytest.fixture
def mock_ollama_provider() -> OllamaProvider:
    sse_response = [
        'data: {"choices": [{"delta": {"content": "HELLO "}}]}',
        'data: {"choices": [{"delta": {"content": "LOCAL"}}]}',
        'data: {"usage": {"prompt_tokens": 10, "completion_tokens": 4}}',
        'data: {"choices": [{"delta": {}, "finish_reason": "stop"}]}',
        "data: [DONE]",
    ]
    transport, _ = make_mock_ollama_transport([(200, sse_response)])
    client = httpx.AsyncClient(transport=transport)
    return OllamaProvider(client=client)


@pytest.mark.asyncio
async def test_ollama_conformance_provider_kit(mock_ollama_provider: OllamaProvider):
    """Verifies Ollama provider passes all standard ProviderKit tests."""
    kit = ProviderKit()
    await kit.test_readiness(mock_ollama_provider)
    await kit.test_list_models(mock_ollama_provider)
    await kit.test_plain_prompt(mock_ollama_provider)
    await kit.test_streaming_multiple_deltas(mock_ollama_provider)
    await kit.test_aclose_idempotent(mock_ollama_provider)


@pytest.mark.asyncio
async def test_ollama_native_tool_calling():
    """Verifies native tool calling when supported by model endpoint."""
    sse_events = [
        'data: {"choices": [{"delta": {"tool_calls": [{"index": 0, "id": "call_1", "function": {"name": "run_command", "arguments": "{\\"command\\": \\"ls -la\\"}"}}]}}]}',
        'data: {"choices": [{"delta": {}, "finish_reason": "tool_calls"}]}',
        "data: [DONE]",
    ]
    transport, captured = make_mock_ollama_transport([(200, sse_events)])
    client = httpx.AsyncClient(transport=transport)
    provider = OllamaProvider(client=client)

    tool_spec = ToolSpec(
        name="run_command",
        description="Run shell command",
        input_schema={"type": "object", "properties": {"command": {"type": "string"}}},
    )

    req = ModelRequest(
        model="llama3.2:latest",
        system=[],
        messages=[Message(role=Role.USER, content=[TextBlock(text="List files")])],
        tools=[tool_spec],
        params=ModelParams(),
    )

    events = [e async for e in provider.generate(req)]
    assert len(captured) == 1
    sent_body = json.loads(captured[0].content)
    assert "tools" in sent_body
    assert sent_body["tools"][0]["function"]["name"] == "run_command"

    tool_calls = [e for e in events if isinstance(e, ToolCallEnd)]
    assert len(tool_calls) == 1
    assert tool_calls[0].name == "run_command"
    assert tool_calls[0].arguments == {"command": "ls -la"}
    assert any(isinstance(e, Done) for e in events)


@pytest.mark.asyncio
async def test_ollama_xml_fallback_on_http_400():
    """Verifies that on HTTP 400 with tools, provider falls back to XML <tool_call> protocol."""
    # Attempt 1: 400 Bad Request (model does not support native tools)
    err_400 = '{"error": "tools are not supported for this model"}'

    # Attempt 2: 200 OK with XML stream containing <think> and <tool_call>
    xml_stream = [
        'data: {"choices": [{"delta": {"content": "<think>I should check git status</think>"}}]}',
        'data: {"choices": [{"delta": {"content": "Checking status:\\n<tool_call>{\\"name\\": \\"run_command\\", \\"arguments\\": {\\"command\\": \\"git status\\"}}\\n</tool_call>"}}]}',
        'data: {"choices": [{"delta": {}, "finish_reason": "stop"}]}',
        "data: [DONE]",
    ]

    transport, captured = make_mock_ollama_transport([(400, err_400), (200, xml_stream)])
    client = httpx.AsyncClient(transport=transport)
    provider = OllamaProvider(client=client)

    tool_spec = ToolSpec(
        name="run_command",
        description="Run shell command",
        input_schema={"type": "object", "properties": {"command": {"type": "string"}}},
    )

    req = ModelRequest(
        model="deepseek-r1:latest",
        system=[],
        messages=[Message(role=Role.USER, content=[TextBlock(text="Check git status")])],
        tools=[tool_spec],
        params=ModelParams(),
    )

    events = [e async for e in provider.generate(req)]

    # 1. Verify two network requests were made (first native tools, second XML fallback)
    assert len(captured) == 2
    req_1_body = json.loads(captured[0].content)
    assert "tools" in req_1_body

    req_2_body = json.loads(captured[1].content)
    assert "tools" not in req_2_body
    # Second request must have tool guidance in system prompt
    assert any("To call a tool, format your output strictly as:" in m.get("content", "") for m in req_2_body["messages"])

    # 2. Verify events parsed: thinking delta, text delta, and tool call end
    thoughts = [e for e in events if isinstance(e, ThinkingDelta)]
    assert len(thoughts) >= 1
    assert "I should check git status" in thoughts[0].text

    tool_calls = [e for e in events if isinstance(e, ToolCallEnd)]
    assert len(tool_calls) == 1
    assert tool_calls[0].name == "run_command"
    assert tool_calls[0].arguments == {"command": "git status"}


@pytest.mark.asyncio
async def test_ollama_list_models_from_api_tags():
    """Verifies models are read from /api/tags."""
    transport, captured = make_mock_ollama_transport(
        chat_responses=[],
        tags_models=["codellama:7b", "mistral:instruct", "deepseek-r1:8b"],
    )
    client = httpx.AsyncClient(transport=transport)
    provider = OllamaProvider(client=client)

    models = await provider.list_models()
    assert len(models) == 3
    ids = [m.id for m in models]
    assert "codellama:7b" in ids
    assert "mistral:instruct" in ids
    assert "deepseek-r1:8b" in ids


# -----------------------------------------------------------------------------
# Readiness (P4): the provider — not the kernel — says whether Ollama is usable
# -----------------------------------------------------------------------------
def _down_transport() -> tuple[httpx.MockTransport, list[httpx.Request]]:
    """Every request fails the way a closed local port does."""
    captured: list[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        captured.append(request)
        raise httpx.ConnectError("Connection refused", request=request)

    return httpx.MockTransport(handler), captured


@pytest.mark.asyncio
async def test_ollama_readiness_when_running():
    transport, captured = make_mock_ollama_transport(chat_responses=[], tags_models=["codellama:7b"])
    provider = OllamaProvider(client=httpx.AsyncClient(transport=transport))
    await ProviderKit().test_readiness(provider)
    assert provider.is_configured() is True
    assert await provider.is_reachable() is True
    assert [m.id for m in await provider.list_models()] == ["codellama:7b"]
    # One probe serves is_reachable() and list_models() for PROBE_TTL_S.
    assert len([r for r in captured if r.url.path.endswith("/api/tags")]) == 1


@pytest.mark.asyncio
async def test_ollama_readiness_when_down_is_fast_cached_and_honest():
    transport, captured = _down_transport()
    provider = OllamaProvider(client=httpx.AsyncClient(transport=transport))

    t0 = time.monotonic()
    assert await provider.is_reachable() is False  # never raises
    assert time.monotonic() - t0 < 1.0

    # The catalogue is still known (so the picker can show it as "not running"), and nothing raised.
    ids = [m.id for m in await provider.list_models()]
    assert ids == [m.id for m in DEFAULT_OLLAMA_MODELS]

    # Negative verdicts are cached too: no reconnect storm on every page load.
    for _ in range(5):
        assert await provider.is_reachable() is False
    assert len(captured) == 1

    # After the TTL the probe runs again (and the daemon may have come up meanwhile).
    provider._probe_ts -= OllamaProvider.PROBE_TTL_S + 1
    assert await provider.is_reachable() is False
    assert len(captured) == 2


@pytest.mark.asyncio
async def test_ollama_probe_uses_short_timeout():
    """The liveness probe must not inherit the 120 s generation timeout."""
    seen: list[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        seen.append(request)
        return httpx.Response(200, json={"models": []})

    provider = OllamaProvider(client=httpx.AsyncClient(transport=httpx.MockTransport(handler)))
    assert await provider.is_reachable() is True
    timeout = seen[0].extensions.get("timeout", {})
    assert timeout.get("connect") == pytest.approx(OllamaProvider.PROBE_TIMEOUT_S)
    assert timeout.get("read") == pytest.approx(OllamaProvider.PROBE_TIMEOUT_S)
    # Up but nothing pulled yet → the catalogue of suggestions, still "ready".
    assert [m.id for m in await provider.list_models()] == [m.id for m in DEFAULT_OLLAMA_MODELS]


@pytest.mark.asyncio
async def test_ollama_plugin_discovery_and_registration(tmp_path: Path):
    """Verifies provider-ollama plugin is discovered and registered in PluginRegistry."""
    bus = EventBus()
    settings = SettingsStore(tmp_path / "settings")
    secrets = Secrets(tmp_path / "secrets")

    registry = PluginRegistry(
        workspace=tmp_path / "ws",
        settings=settings,
        secrets=secrets,
        bus=bus,
    )

    builtin_dir = Path(__file__).resolve().parents[2] / "harness" / "plugins"
    registry.discover_all(builtin_dir=builtin_dir)
    assert "provider-ollama" in registry.discovered

    await registry.load_all()
    assert "ollama" in registry.providers
    provider_inst, _ = registry.providers["ollama"]
    assert isinstance(provider_inst, OllamaProvider)
