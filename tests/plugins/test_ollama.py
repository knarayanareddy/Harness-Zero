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
from harness.plugins.providers.ollama.xml_protocol import XmlStreamParser
from harness.sdk.testing import ProviderKit
from harness.sdk.types import (
    Done,
    Message,
    ModelParams,
    ModelRequest,
    Role,
    TextBlock,
    TextDelta,
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


def test_ollama_xml_parser_handles_split_tool_closing_tag():
    source = 'before<tool_call>{"name":"echo","arguments":{"text":"hi"}}</tool_call>after'
    closing_tag = "</tool_call>"
    split_at = source.index(closing_tag) + 5
    parser = XmlStreamParser()

    events = parser.feed(source[:split_at])
    events.extend(parser.feed(source[split_at:]))
    events.extend(parser.flush())

    assert any(isinstance(event, ToolCallEnd) and event.name == "echo" for event in events)
    assert "".join(event.text for event in events if isinstance(event, TextDelta)) == "beforeafter"


def test_ollama_xml_parser_is_independent_of_every_byte_split():
    source = (
        'before<think>reasoning</think>between'
        '<tool_call>{"name":"echo","arguments":{"text":"ok"}}</tool_call>after'
    )

    def parse(chunks: list[str]) -> list[tuple[object, ...]]:
        parser = XmlStreamParser()
        events = []
        for chunk in chunks:
            events.extend(parser.feed(chunk))
        events.extend(parser.flush())
        normalized: list[tuple[object, ...]] = []
        for event in events:
            if isinstance(event, (TextDelta, ThinkingDelta)):
                kind = "text" if isinstance(event, TextDelta) else "thinking"
                if normalized and normalized[-1][0] == kind:
                    normalized[-1] = (kind, normalized[-1][1] + event.text)
                else:
                    normalized.append((kind, event.text))
            elif isinstance(event, ToolCallEnd):
                normalized.append(("tool", event.id, event.name, event.arguments))
        return normalized

    expected = parse([source])
    assert expected == [
        ("text", "before"),
        ("thinking", "reasoning"),
        ("text", "between"),
        ("tool", "xml_call_1", "echo", {"text": "ok"}),
        ("text", "after"),
    ]
    for split in range(1, len(source.encode("utf-8"))):
        assert parse([source[:split], source[split:]]) == expected, f"split at byte {split}"
    assert parse(list(source)) == expected


@pytest.mark.parametrize(
    "source",
    [
        '<tool_call>{not-json}</tool_call>',
        '<tool_call>{"name":"echo","arguments":[]}</tool_call>',
        '<tool_call>{"name":"echo","arguments":"not-json"}</tool_call>',
        '<tool_call>{"name":"echo","arguments":{}}',
        "<tool_call>[]</tool_call>",
        "<tool_call>",
        "<tool_cal",
    ],
)
def test_ollama_xml_parser_rejects_malformed_or_incomplete_tool_markup(source: str):
    parser = XmlStreamParser()

    with pytest.raises(ValueError, match="tool_call"):
        parser.feed(source)
        parser.flush()


def test_ollama_xml_parser_bounds_unterminated_tool_call_buffer():
    parser = XmlStreamParser()
    oversized_call = "<tool_call>" + ("x" * (64 * 1024 + 1))

    with pytest.raises(ValueError, match="too large"):
        parser.feed(oversized_call)

    assert parser.buffer == ""
    assert parser.tool_buffer == ""


@pytest.mark.parametrize("source", ["comparison: 2 <", "literal <t", "plain <"])
def test_ollama_xml_parser_preserves_plain_text_angle_brackets_at_eof(source):
    parser = XmlStreamParser()
    events = parser.feed(source) + parser.flush()
    assert "".join(event.text for event in events if isinstance(event, TextDelta)) == source
    assert not [event for event in events if isinstance(event, ToolCallEnd)]


def test_ollama_xml_parser_accepts_json_object_encoded_arguments_without_coercion():
    parser = XmlStreamParser()
    body = json.dumps({"name": "echo", "arguments": json.dumps({"text": "ok"})})
    events = parser.feed("<tool_call>" + body + "</tool_call>") + parser.flush()
    assert len(events) == 1 and isinstance(events[0], ToolCallEnd)
    assert events[0].arguments == {"text": "ok"}


@pytest.mark.asyncio
@pytest.mark.parametrize("markup", ['<tool_call>{bad}</tool_call>', '<tool_call>{"name":"echo","arguments":{}}'])
async def test_ollama_xml_failure_propagates_through_adapter_without_success(markup):
    chunks = ["data: " + json.dumps({"choices": [{"delta": {"content": markup}}]}),
              'data: {"choices": [{"delta": {}, "finish_reason": "stop"}]}', "data: [DONE]"]
    transport, captured = make_mock_ollama_transport([
        (400, '{"error":"tools are not supported"}'), (200, chunks)])
    async with httpx.AsyncClient(transport=transport) as client:
        provider = OllamaProvider(client=client)
        request = ModelRequest(model="fixture", system=[], params=ModelParams(),
            tools=[ToolSpec(name="echo", description="fixture", input_schema={"type": "object"})],
            messages=[Message(role=Role.USER, content=[TextBlock("test")])])
        observed = []
        with pytest.raises(ValueError, match="tool_call"):
            async for event in provider.generate(request):
                observed.append(event)
    assert len(captured) == 2
    assert not any(isinstance(event, (Done, ToolCallEnd)) for event in observed)


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
