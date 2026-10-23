"""
Tests for Anthropic Provider Plugin (PLG-10).
Conforms to ProviderKit with recorded SSE fixtures and transport mocks.
"""
from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import httpx
import pytest

from harness.kernel.bus import EventBus
from harness.kernel.registry import PluginRegistry
from harness.kernel.secrets import Secrets
from harness.kernel.settings import SettingsStore
from harness.plugins.providers.anthropic.provider import AnthropicProvider
from harness.sdk.errors import ProviderError
from harness.sdk.testing import ProviderKit
from harness.sdk.types import (
    ContextBlock,
    Done,
    Message,
    ModelParams,
    ModelRequest,
    RiskClass,
    Role,
    Stability,
    TextBlock,
    TextDelta,
    ThinkingDelta,
    ToolCallEnd,
    ToolSpec,
    UsageEvent,
)


def make_mock_anthropic_transport(
    sse_events: list[dict[str, Any]],
    status_code: int = 200,
    error_body: str = "",
) -> httpx.MockTransport:
    """Creates a MockTransport simulating Anthropic Messages API SSE stream."""
    def handler(request: httpx.Request) -> httpx.Response:
        if status_code != 200:
            return httpx.Response(status_code, text=error_body or "Anthropic API Error")

        lines = []
        for evt in sse_events:
            lines.append(f"data: {json.dumps(evt)}\n")

        content = "\n".join(lines).encode("utf-8")
        return httpx.Response(
            200,
            headers={"Content-Type": "text/event-stream"},
            content=content,
        )

    return httpx.MockTransport(handler)


@pytest.fixture
def mock_anthropic_provider() -> AnthropicProvider:
    events = [
        {
            "type": "message_start",
            "message": {
                "id": "msg_123",
                "type": "message",
                "role": "assistant",
                "model": "claude-3-5-sonnet-latest",
                "usage": {
                    "input_tokens": 50,
                    "cache_creation_input_tokens": 20,
                    "cache_read_input_tokens": 30,
                    "output_tokens": 0,
                },
            },
        },
        {
            "type": "content_block_start",
            "index": 0,
            "content_block": {"type": "text", "text": ""},
        },
        {
            "type": "content_block_delta",
            "index": 0,
            "delta": {"type": "text_delta", "text": "HELLO "},
        },
        {
            "type": "content_block_delta",
            "index": 0,
            "delta": {"type": "text_delta", "text": "WORLD"},
        },
        {"type": "content_block_stop", "index": 0},
        {
            "type": "message_delta",
            "delta": {"stop_reason": "end_turn"},
            "usage": {"output_tokens": 10},
        },
        {"type": "message_stop"},
    ]
    client = httpx.AsyncClient(transport=make_mock_anthropic_transport(events))
    return AnthropicProvider(api_key="sk-ant-test-key", client=client)


# -----------------------------------------------------------------------------
# 1. ProviderKit Conformance
# -----------------------------------------------------------------------------
@pytest.mark.asyncio
async def test_providerkit_conformance(mock_anthropic_provider: AnthropicProvider):
    kit = ProviderKit()
    await kit.test_readiness(mock_anthropic_provider)
    await kit.test_list_models(mock_anthropic_provider)
    await kit.test_plain_prompt(mock_anthropic_provider)
    await kit.test_streaming_multiple_deltas(mock_anthropic_provider)
    await kit.test_aclose_idempotent(mock_anthropic_provider)


# -----------------------------------------------------------------------------
# 2. Prompt Caching and Usage Accounting (MDL-03, CONF-04)
# -----------------------------------------------------------------------------
@pytest.mark.asyncio
async def test_prompt_caching_and_system_breakpoints(mock_anthropic_provider: AnthropicProvider):
    req = ModelRequest(
        model="claude-3-5-sonnet-latest",
        system=[
            ContextBlock(id="b1", text="Static identity", stability=Stability.STATIC),
            ContextBlock(id="b2", text="Static instructions", stability=Stability.STATIC),
            ContextBlock(id="b3", text="Dynamic turn context", stability=Stability.TURN),
        ],
        messages=[Message(role=Role.USER, content=[TextBlock(text="Test cache")])],
        tools=[
            ToolSpec(
                name="test_tool",
                description="desc",
                input_schema={"type": "object", "additionalProperties": False},
                risk_class=RiskClass.READ,
            )
        ],
        params=ModelParams(),
    )

    # Inspect constructed system blocks
    system_blocks = mock_anthropic_provider._build_system(req.system)
    assert len(system_blocks) == 3
    # Last static block (b2) should have cache_control
    assert "cache_control" not in system_blocks[0]
    assert system_blocks[1].get("cache_control") == {"type": "ephemeral"}
    assert "cache_control" not in system_blocks[2]

    # Inspect constructed tools
    tool_blocks = mock_anthropic_provider._build_tools(req.tools)
    assert len(tool_blocks) == 1
    assert tool_blocks[0].get("cache_control") == {"type": "ephemeral"}

    # Execute stream and assert cache metrics in UsageEvent
    events = [e async for e in mock_anthropic_provider.generate(req)]
    usage_events = [e for e in events if isinstance(e, UsageEvent)]
    assert len(usage_events) == 1
    usage = usage_events[0].usage
    assert usage.input_tokens == 50
    assert usage.cache_read_tokens == 30
    assert usage.cache_write_tokens == 20
    assert usage.output_tokens == 10


# -----------------------------------------------------------------------------
# 3. Tool Calling and Argument Delta Streaming
# -----------------------------------------------------------------------------
@pytest.mark.asyncio
async def test_anthropic_tool_call_delta_streaming():
    events = [
        {
            "type": "message_start",
            "message": {
                "id": "msg_tool",
                "type": "message",
                "role": "assistant",
                "model": "claude-3-5-sonnet-latest",
                "usage": {"input_tokens": 100, "output_tokens": 0},
            },
        },
        {
            "type": "content_block_start",
            "index": 0,
            "content_block": {
                "type": "tool_use",
                "id": "toolu_01ABC",
                "name": "run_command",
            },
        },
        {
            "type": "content_block_delta",
            "index": 0,
            "delta": {"type": "input_json_delta", "partial_json": '{"command":'},
        },
        {
            "type": "content_block_delta",
            "index": 0,
            "delta": {"type": "input_json_delta", "partial_json": ' "pytest -q"}'},
        },
        {"type": "content_block_stop", "index": 0},
        {
            "type": "message_delta",
            "delta": {"stop_reason": "tool_use"},
            "usage": {"output_tokens": 25},
        },
        {"type": "message_stop"},
    ]

    client = httpx.AsyncClient(transport=make_mock_anthropic_transport(events))
    provider = AnthropicProvider(api_key="test-key", client=client)

    req = ModelRequest(
        model="claude-3-5-sonnet-latest",
        system=[],
        messages=[Message(role=Role.USER, content=[TextBlock(text="Run tests")])],
        tools=[],
        params=ModelParams(),
    )

    received_events = [e async for e in provider.generate(req)]
    tool_ends = [e for e in received_events if isinstance(e, ToolCallEnd)]
    dones = [e for e in received_events if isinstance(e, Done)]

    assert len(tool_ends) == 1
    t = tool_ends[0]
    assert t.id == "toolu_01ABC"
    assert t.name == "run_command"
    assert t.arguments == {"command": "pytest -q"}

    assert len(dones) == 1
    assert dones[0].stop_reason == "tool_use"


# -----------------------------------------------------------------------------
# 4. Thinking / Reasoning Block Streaming (Claude 3.7 Sonnet)
# -----------------------------------------------------------------------------
@pytest.mark.asyncio
async def test_anthropic_thinking_delta_streaming():
    events = [
        {
            "type": "message_start",
            "message": {
                "id": "msg_think",
                "type": "message",
                "role": "assistant",
                "model": "claude-3-7-sonnet",
                "usage": {"input_tokens": 40, "output_tokens": 0},
            },
        },
        {
            "type": "content_block_start",
            "index": 0,
            "content_block": {"type": "thinking", "thinking": ""},
        },
        {
            "type": "content_block_delta",
            "index": 0,
            "delta": {"type": "thinking_delta", "thinking": "Analyzing the codebase architecture..."},
        },
        {"type": "content_block_stop", "index": 0},
        {
            "type": "content_block_start",
            "index": 1,
            "content_block": {"type": "text", "text": ""},
        },
        {
            "type": "content_block_delta",
            "index": 1,
            "delta": {"type": "text_delta", "text": "Plan formulated."},
        },
        {"type": "content_block_stop", "index": 1},
        {
            "type": "message_delta",
            "delta": {"stop_reason": "end_turn"},
            "usage": {"output_tokens": 30},
        },
        {"type": "message_stop"},
    ]

    client = httpx.AsyncClient(transport=make_mock_anthropic_transport(events))
    provider = AnthropicProvider(api_key="test-key", client=client)

    req = ModelRequest(
        model="claude-3-7-sonnet",
        system=[],
        messages=[Message(role=Role.USER, content=[TextBlock(text="Think first")])],
        tools=[],
        params=ModelParams(extra={"thinking": True, "thinking_budget": 2048}),
    )

    received_events = [e async for e in provider.generate(req)]
    thinking_deltas = [e for e in received_events if isinstance(e, ThinkingDelta)]
    text_deltas = [e for e in received_events if isinstance(e, TextDelta)]

    assert len(thinking_deltas) == 1
    assert "Analyzing the codebase" in thinking_deltas[0].text
    assert len(text_deltas) == 1
    assert "Plan formulated" in text_deltas[0].text


# -----------------------------------------------------------------------------
# 5. Error Mapping
# -----------------------------------------------------------------------------
@pytest.mark.parametrize(
    "status_code, expected_kind, expected_retryable",
    [
        (401, "auth", False),
        (429, "rate_limit", True),
        (500, "server", True),
        (503, "server", True),
        (400, "bad_request", False),
    ],
)
@pytest.mark.asyncio
async def test_anthropic_error_mapping(status_code: int, expected_kind: str, expected_retryable: bool):
    client = httpx.AsyncClient(transport=make_mock_anthropic_transport([], status_code=status_code, error_body="API error"))
    provider = AnthropicProvider(api_key="test-key", client=client)

    req = ModelRequest(
        model="claude-3-5-sonnet-latest",
        system=[],
        messages=[Message(role=Role.USER, content=[TextBlock(text="trigger error")])],
        tools=[],
        params=ModelParams(),
    )

    with pytest.raises(ProviderError) as exc_info:
        async for _ in provider.generate(req):
            pass

    err = exc_info.value
    assert err.kind == expected_kind
    assert err.retryable == expected_retryable
    assert err.status_code == status_code


@pytest.mark.parametrize(
    "error_type, expected_kind, expected_retryable",
    [
        ("overloaded_error", "server", True),
        ("rate_limit_error", "rate_limit", True),
        ("invalid_request_error", "bad_request", False),
    ],
)
@pytest.mark.asyncio
async def test_anthropic_stream_error_event_raises_provider_error(
    error_type: str, expected_kind: str, expected_retryable: bool
):
    events = [
        {"type": "message_start", "message": {"usage": {"input_tokens": 3}}},
        {"type": "content_block_delta", "delta": {"type": "text_delta", "text": "partial"}},
        {"type": "error", "error": {"type": error_type, "message": "stream failed"}},
        {"type": "message_stop"},
    ]
    client = httpx.AsyncClient(transport=make_mock_anthropic_transport(events))
    provider = AnthropicProvider(api_key="test-key", client=client)
    req = ModelRequest(
        model="claude-3-5-sonnet-latest",
        system=[],
        messages=[Message(role=Role.USER, content=[TextBlock(text="trigger error")])],
        tools=[],
        params=ModelParams(),
    )

    emitted = []
    with pytest.raises(ProviderError) as exc_info:
        async for event in provider.generate(req):
            emitted.append(event)

    assert exc_info.value.kind == expected_kind
    assert exc_info.value.retryable is expected_retryable
    assert "stream failed" in str(exc_info.value)
    assert any(isinstance(event, TextDelta) for event in emitted)
    assert not any(isinstance(event, (UsageEvent, Done)) for event in emitted)


@pytest.mark.asyncio
async def test_anthropic_stream_eof_before_message_stop_is_failure():
    events = [
        {"type": "message_start", "message": {"usage": {"input_tokens": 3}}},
        {"type": "message_delta", "delta": {"stop_reason": "end_turn"}, "usage": {"output_tokens": 7}},
    ]
    client = httpx.AsyncClient(transport=make_mock_anthropic_transport(events))
    provider = AnthropicProvider(api_key="test-key", client=client)
    req = ModelRequest(
        model="claude-3-5-sonnet-latest",
        system=[],
        messages=[Message(role=Role.USER, content=[TextBlock(text="trigger eof")])],
        tools=[],
        params=ModelParams(),
    )

    emitted = []
    with pytest.raises(ProviderError, match="before message_stop"):
        async for event in provider.generate(req):
            emitted.append(event)

    assert not any(isinstance(event, (UsageEvent, Done)) for event in emitted)


@pytest.mark.asyncio
async def test_anthropic_message_delta_usage_is_cumulative_not_summed():
    events = [
        {"type": "message_start", "message": {"usage": {"input_tokens": 3}}},
        {"type": "message_delta", "usage": {"output_tokens": 4}},
        {"type": "message_delta", "usage": {"output_tokens": 9}},
        {"type": "message_stop"},
    ]
    client = httpx.AsyncClient(transport=make_mock_anthropic_transport(events))
    provider = AnthropicProvider(api_key="test-key", client=client)
    req = ModelRequest(
        model="claude-3-5-sonnet-latest",
        system=[],
        messages=[Message(role=Role.USER, content=[TextBlock(text="usage")])],
        tools=[],
        params=ModelParams(),
    )

    received = [event async for event in provider.generate(req)]
    usage = next(event.usage for event in received if isinstance(event, UsageEvent))
    assert usage.output_tokens == 9


@pytest.mark.asyncio
@pytest.mark.parametrize("chunk_size", [1, 2, 17])
async def test_anthropic_valid_stream_preserves_utf8_under_controlled_byte_fragmentation(chunk_size):
    events = [
        {"type": "message_start", "message": {"usage": {"input_tokens": 3}}},
        {"type": "content_block_delta", "delta": {"type": "text_delta", "text": "café 🌍"}},
        {"type": "message_delta", "delta": {"stop_reason": "end_turn"}, "usage": {"output_tokens": 2}},
        {"type": "message_stop"},
    ]
    body = "".join("data: " + json.dumps(event, ensure_ascii=False) + "\n\n" for event in events).encode()

    class FragmentedStream(httpx.AsyncByteStream):
        async def __aiter__(self):
            for offset in range(0, len(body), chunk_size):
                yield body[offset:offset + chunk_size]

        async def aclose(self):
            pass

    transport = httpx.MockTransport(lambda request: httpx.Response(
        200, headers={"Content-Type": "text/event-stream"}, stream=FragmentedStream()))
    async with httpx.AsyncClient(transport=transport) as client:
        provider = AnthropicProvider(api_key="synthetic", client=client)
        request = ModelRequest(model="test", system=[], tools=[], params=ModelParams(),
            messages=[Message(role=Role.USER, content=[TextBlock("test")])])
        received = [event async for event in provider.generate(request)]
    assert [event.text for event in received if isinstance(event, TextDelta)] == ["café 🌍"]
    assert [event.stop_reason for event in received if isinstance(event, Done)] == ["end_turn"]
    assert [event.usage.output_tokens for event in received if isinstance(event, UsageEvent)] == [2]


# -----------------------------------------------------------------------------
# 6. Plugin Discovery and Setup
# -----------------------------------------------------------------------------
@pytest.mark.asyncio
async def test_anthropic_plugin_discovery(tmp_path: Path):
    bus = EventBus()
    settings = SettingsStore(path=tmp_path / "settings.json")
    secrets = Secrets(path=tmp_path / "secrets.json")
    registry = PluginRegistry(
        workspace=tmp_path,
        settings=settings,
        secrets=secrets,
        bus=bus,
    )
    plugin_dir = Path(__file__).resolve().parents[2] / "harness" / "plugins"
    registry.discover_all(builtin_dir=plugin_dir)
    await registry.load_all()

    assert "anthropic" in registry.providers
