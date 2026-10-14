"""
Tests for PLG-01: OpenAI-compatible Provider plugin (§4, §11, PLG-01).
Uses ProviderKit and recorded mock SSE streams.
"""
from __future__ import annotations

import json
from pathlib import Path

import httpx
import pytest

from harness.kernel.bus import EventBus
from harness.kernel.registry import PluginRegistry
from harness.kernel.secrets import Secrets
from harness.kernel.settings import SettingsStore
from harness.plugins.providers.openai_compat.provider import OpenAICompatProvider
from harness.sdk.errors import ProviderError
from harness.sdk.testing import ProviderKit
from harness.sdk.types import (
    Done,
    Message,
    ModelParams,
    ModelRequest,
    RiskClass,
    Role,
    TextBlock,
    TextDelta,
    ThinkingDelta,
    ToolCallEnd,
    ToolSpec,
)


def make_mock_transport(sse_lines: list[str], status_code: int = 200) -> httpx.MockTransport:
    """Creates a MockTransport simulating SSE streaming responses."""
    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path.endswith("/models"):
            models_payload = {
                "data": [
                    {"id": "gpt-4o", "name": "GPT-4o", "context_length": 128000},
                    {"id": "gpt-4o-mini", "name": "GPT-4o Mini", "context_length": 128000},
                ]
            }
            return httpx.Response(status_code, json=models_payload)

        # /chat/completions endpoint
        if status_code != 200:
            return httpx.Response(status_code, text="Error payload")

        content = "\n".join(sse_lines).encode("utf-8")
        return httpx.Response(
            status_code=200,
            headers={"Content-Type": "text/event-stream"},
            content=content,
        )

    return httpx.MockTransport(handler)


@pytest.fixture
def mock_provider() -> OpenAICompatProvider:
    sse_response = [
        'data: {"choices": [{"delta": {"content": "HELLO"}}]}',
        'data: {"usage": {"prompt_tokens": 15, "completion_tokens": 5, "prompt_tokens_details": {"cached_tokens": 10}}}',
        'data: {"choices": [{"delta": {}, "finish_reason": "stop"}]}',
        "data: [DONE]",
    ]
    client = httpx.AsyncClient(transport=make_mock_transport(sse_response))
    return OpenAICompatProvider(
        name="mock_openai",
        base_url="https://api.openai.com/v1",
        api_key="sk-test-mock-key-12345",
        client=client,
    )


@pytest.mark.asyncio
async def test_provider_kit_list_models(mock_provider: OpenAICompatProvider):
    """Verifies list_models with ProviderKit."""
    kit = ProviderKit()
    await kit.test_list_models(mock_provider)
    models = await mock_provider.list_models()
    assert len(models) >= 2
    assert models[0].id == "gpt-4o"


@pytest.mark.asyncio
async def test_provider_kit_plain_prompt(mock_provider: OpenAICompatProvider):
    """Verifies streaming plain prompt with ProviderKit."""
    kit = ProviderKit()
    await kit.test_plain_prompt(mock_provider)


@pytest.mark.asyncio
async def test_streaming_reasoning_and_tool_calls():
    """Verifies streaming reasoning chunks and accumulated tool calls."""
    sse_response = [
        'data: {"choices": [{"delta": {"reasoning_content": "Analyzing request..."}}]}',
        'data: {"choices": [{"delta": {"content": "I will run the tool."}}]}',
        'data: {"choices": [{"delta": {"tool_calls": [{"index": 0, "id": "call_123", "function": {"name": "read_file", "arguments": "{\\"path\\": "}}]}}]}',
        'data: {"choices": [{"delta": {"tool_calls": [{"index": 0, "function": {"arguments": "\\"config.json\\"}"}}]}}]}',
        'data: {"choices": [{"delta": {}, "finish_reason": "tool_calls"}]}',
        "data: [DONE]",
    ]
    client = httpx.AsyncClient(transport=make_mock_transport(sse_response))
    provider = OpenAICompatProvider(
        name="openai",
        api_key="sk-test-mock",
        client=client,
    )

    req = ModelRequest(
        model="gpt-4o",
        system=[],
        messages=[Message(role=Role.USER, content=[TextBlock(text="Read config")])],
        tools=[
            ToolSpec(
                name="read_file",
                description="Read a file",
                input_schema={"type": "object", "properties": {"path": {"type": "string"}}},
                risk_class=RiskClass.READ,
            )
        ],
        params=ModelParams(),
    )

    events = []
    async for event in provider.generate(req):
        events.append(event)

    # Check for Reasoning / ThinkingDelta
    thinking_deltas = [e for e in events if isinstance(e, ThinkingDelta)]
    assert len(thinking_deltas) == 1
    assert thinking_deltas[0].text == "Analyzing request..."

    # Check for TextDelta
    text_deltas = [e for e in events if isinstance(e, TextDelta)]
    assert len(text_deltas) == 1
    assert text_deltas[0].text == "I will run the tool."

    # Check for ToolCallEnd with parsed JSON arguments
    tool_calls = [e for e in events if isinstance(e, ToolCallEnd)]
    assert len(tool_calls) == 1
    assert tool_calls[0].id == "call_123"
    assert tool_calls[0].name == "read_file"
    assert tool_calls[0].arguments == {"path": "config.json"}

    # Check for Done stop reason
    done_events = [e for e in events if isinstance(e, Done)]
    assert len(done_events) == 1
    assert done_events[0].stop_reason == "tool_use"


@pytest.mark.asyncio
async def test_error_mapping_status_codes():
    """Verifies that HTTP errors map cleanly to typed ProviderError exceptions."""
    for status, expected_kind in [
        (401, "auth"),
        (403, "auth"),
        (429, "rate_limit"),
        (400, "bad_request"),
        (500, "server"),
        (503, "server"),
    ]:
        client = httpx.AsyncClient(transport=make_mock_transport([], status_code=status))
        prov = OpenAICompatProvider(name="test", api_key="sk-test", client=client)
        req = ModelRequest(
            model="gpt-4o",
            system=[],
            messages=[Message(role=Role.USER, content=[TextBlock(text="hi")])],
            tools=[],
            params=ModelParams(),
        )
        with pytest.raises(ProviderError) as exc_info:
            async for _ in prov.generate(req):
                pass
        assert exc_info.value.kind == expected_kind, f"Expected {expected_kind} for status {status}"


@pytest.mark.asyncio
async def test_plugin_discovery_and_setup(tmp_path: Path):
    """Verifies that PluginRegistry discovers and sets up openai_compat plugin."""
    ws = tmp_path / "ws"
    ws.mkdir()
    conf = tmp_path / "conf"
    conf.mkdir()

    secrets_file = conf / "secrets.json"
    secrets_file.write_text(json.dumps({"openai_api_key": "sk-real-secret-test"}), encoding="utf-8")

    bus = EventBus()
    settings = SettingsStore(path=conf)
    secrets = Secrets(path=conf)
    registry = PluginRegistry(workspace=ws, settings=settings, secrets=secrets, bus=bus)

    builtin_dir = Path(__file__).resolve().parents[2] / "harness" / "plugins"
    registry.discover_all(builtin_dir=builtin_dir)
    await registry.load_all()

    assert "openai-compat" in registry.discovered
    assert registry.is_loaded("openai-compat")

    # Verify registered providers
    openai_prov = registry.get_provider("openai")
    assert openai_prov is not None
    assert openai_prov.name == "openai"
    assert openai_prov.api_key == "sk-real-secret-test"

    openrouter_prov = registry.get_provider("openrouter")
    assert openrouter_prov is not None

    deepseek_prov = registry.get_provider("deepseek")
    assert deepseek_prov is not None
