"""
Tests for Gemini Provider Plugin (PLG-11).
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
from harness.plugins.providers.gemini.provider import GeminiProvider
from harness.sdk.errors import ProviderError
from harness.sdk.testing import ProviderKit
from harness.sdk.types import (
    ContextBlock,
    Done,
    Message,
    ModelParams,
    ModelRequest,
    Role,
    Stability,
    TextBlock,
    TextDelta,
    ToolCallEnd,
    ToolResultBlock,
    ToolSpec,
    ToolUseBlock,
)


def make_mock_gemini_transport(
    sse_events: list[dict[str, Any]],
    status_code: int = 200,
    error_body: str = "",
) -> tuple[httpx.MockTransport, list[httpx.Request]]:
    """Creates a MockTransport simulating Gemini SSE stream, recording requests."""
    captured: list[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        captured.append(request)
        if status_code != 200:
            return httpx.Response(status_code, text=error_body or "Gemini API Error")

        lines = []
        for evt in sse_events:
            lines.append(f"data: {json.dumps(evt)}\n")

        content = "\n".join(lines).encode("utf-8")
        return httpx.Response(
            200,
            headers={"Content-Type": "text/event-stream"},
            content=content,
        )

    return httpx.MockTransport(handler), captured


@pytest.fixture
def mock_gemini_provider() -> GeminiProvider:
    events = [
        {
            "candidates": [
                {
                    "content": {
                        "parts": [{"text": "HELLO "}],
                        "role": "model",
                    },
                }
            ],
        },
        {
            "candidates": [
                {
                    "content": {
                        "parts": [{"text": "WORLD"}],
                        "role": "model",
                    },
                    "finishReason": "STOP",
                }
            ],
            "usageMetadata": {
                "promptTokenCount": 15,
                "candidatesTokenCount": 6,
                "totalTokenCount": 21,
                "cachedContentTokenCount": 0,
            },
        },
    ]

    transport, _ = make_mock_gemini_transport(events)
    client = httpx.AsyncClient(transport=transport)
    return GeminiProvider(api_key="test_gemini_key", client=client)


@pytest.mark.asyncio
async def test_gemini_conformance_provider_kit(mock_gemini_provider: GeminiProvider):
    """Verifies Gemini provider passes all standard ProviderKit tests."""
    kit = ProviderKit()
    await kit.test_list_models(mock_gemini_provider)
    await kit.test_plain_prompt(mock_gemini_provider)
    await kit.test_streaming_multiple_deltas(mock_gemini_provider)
    await kit.test_aclose_idempotent(mock_gemini_provider)


@pytest.mark.asyncio
async def test_gemini_header_auth_and_no_query_param():
    """Verifies API key is passed in x-goog-api-key header and not in query parameters."""
    events = [
        {
            "candidates": [
                {
                    "content": {"parts": [{"text": "OK"}], "role": "model"},
                    "finishReason": "STOP",
                }
            ],
            "usageMetadata": {
                "promptTokenCount": 5,
                "candidatesTokenCount": 2,
            },
        }
    ]
    transport, captured = make_mock_gemini_transport(events)
    client = httpx.AsyncClient(transport=transport)
    provider = GeminiProvider(api_key="secret_google_key_999", client=client)

    req = ModelRequest(
        model="gemini-2.0-flash",
        system=[],
        messages=[Message(role=Role.USER, content=[TextBlock(text="Ping")])],
        tools=[],
        params=ModelParams(),
    )

    events_out = [e async for e in provider.generate(req)]
    assert any(isinstance(e, TextDelta) and e.text == "OK" for e in events_out)
    assert len(captured) == 1
    sent_req = captured[0]

    # Verify header key and no secret in URL query string
    assert sent_req.headers.get("x-goog-api-key") == "secret_google_key_999"
    assert "secret_google_key" not in str(sent_req.url.query)


@pytest.mark.asyncio
async def test_gemini_role_merging_and_system_instruction():
    """Verifies systemInstruction is set and consecutive same-role turns are merged."""
    events = [
        {
            "candidates": [
                {
                    "content": {"parts": [{"text": "Understood"}], "role": "model"},
                    "finishReason": "STOP",
                }
            ],
            "usageMetadata": {"promptTokenCount": 10, "candidatesTokenCount": 2},
        }
    ]
    transport, captured = make_mock_gemini_transport(events)
    client = httpx.AsyncClient(transport=transport)
    provider = GeminiProvider(api_key="test_key", client=client)

    req = ModelRequest(
        model="gemini-1.5-pro",
        system=[ContextBlock(id="sys1", text="You are an expert engineer.", stability=Stability.STATIC)],
        messages=[
            Message(role=Role.USER, content=[TextBlock(text="Part 1")]),
            Message(role=Role.USER, content=[TextBlock(text="Part 2")]),
            Message(role=Role.ASSISTANT, content=[TextBlock(text="Draft answer")]),
            Message(role=Role.ASSISTANT, content=[TextBlock(text="Refined answer")]),
            Message(role=Role.USER, content=[TextBlock(text="Final query")]),
        ],
        tools=[],
        params=ModelParams(temperature=0.3, max_tokens=1024, extra={"top_p": 0.95}),
    )

    _ = [e async for e in provider.generate(req)]
    assert len(captured) == 1
    sent_body = json.loads(captured[0].content)

    # 1. systemInstruction check
    assert "systemInstruction" in sent_body
    assert sent_body["systemInstruction"]["parts"][0]["text"] == "You are an expert engineer."

    # 2. merged turns check (5 messages collapsed to 3 alternating turns: user, model, user)
    contents = sent_body["contents"]
    assert len(contents) == 3
    assert contents[0]["role"] == "user"
    assert len(contents[0]["parts"]) == 2
    assert contents[0]["parts"][0]["text"] == "Part 1"
    assert contents[0]["parts"][1]["text"] == "Part 2"

    assert contents[1]["role"] == "model"
    assert len(contents[1]["parts"]) == 2

    assert contents[2]["role"] == "user"
    assert contents[2]["parts"][0]["text"] == "Final query"

    # 3. generationConfig check
    assert sent_body["generationConfig"]["temperature"] == 0.3
    assert sent_body["generationConfig"]["maxOutputTokens"] == 1024
    assert sent_body["generationConfig"]["topP"] == 0.95


@pytest.mark.asyncio
async def test_gemini_function_calling_and_response():
    """Verifies tool declarations, functionCall parsing into ToolCallEnd, and functionResponse translation."""
    events = [
        {
            "candidates": [
                {
                    "content": {
                        "parts": [
                            {
                                "functionCall": {
                                    "name": "view_file",
                                    "args": {"path": "src/main.py"},
                                }
                            }
                        ],
                        "role": "model",
                    },
                    "finishReason": "STOP",
                }
            ],
            "usageMetadata": {"promptTokenCount": 20, "candidatesTokenCount": 10},
        }
    ]
    transport, captured = make_mock_gemini_transport(events)
    client = httpx.AsyncClient(transport=transport)
    provider = GeminiProvider(api_key="test_key", client=client)

    tool_spec = ToolSpec(
        name="view_file",
        description="View file contents",
        input_schema={
            "type": "object",
            "properties": {"path": {"type": "string"}},
            "required": ["path"],
        },
    )

    req = ModelRequest(
        model="gemini-2.0-flash",
        system=[],
        messages=[
            Message(role=Role.USER, content=[TextBlock(text="Read main.py")]),
            Message(role=Role.ASSISTANT, content=[ToolUseBlock(id="call_99", name="view_file", input={"path": "src/main.py"})]),
            Message(role=Role.USER, content=[ToolResultBlock(tool_use_id="call_99", content=[TextBlock(text="def main(): pass")])]),
        ],
        tools=[tool_spec],
        params=ModelParams(),
    )

    events_out = [e async for e in provider.generate(req)]

    # 1. Verify sent function declarations
    sent_body = json.loads(captured[0].content)
    assert "tools" in sent_body
    fn_decls = sent_body["tools"][0]["functionDeclarations"]
    assert len(fn_decls) == 1
    assert fn_decls[0]["name"] == "view_file"

    # 2. Verify functionResponse sent back in user message
    user_turn = sent_body["contents"][2]
    assert user_turn["role"] == "user"
    fn_resp = user_turn["parts"][0]["functionResponse"]
    assert fn_resp["name"] == "view_file"
    assert fn_resp["response"]["content"] == "def main(): pass"

    # 3. Verify functionCall parsed as ToolCallEnd
    tool_calls = [e for e in events_out if isinstance(e, ToolCallEnd)]
    assert len(tool_calls) == 1
    assert tool_calls[0].name == "view_file"
    assert tool_calls[0].arguments == {"path": "src/main.py"}
    assert any(isinstance(e, Done) for e in events_out)


@pytest.mark.asyncio
async def test_gemini_error_status_mapping():
    """Verifies Gemini HTTP error status codes map to typed ProviderErrors."""
    # 400 Bad Request
    transport_400, _ = make_mock_gemini_transport([], status_code=400, error_body='{"error": {"message": "Invalid JSON"}}')
    p_400 = GeminiProvider(api_key="key", client=httpx.AsyncClient(transport=transport_400))
    req = ModelRequest(model="gemini-2.0-flash", system=[], messages=[], tools=[], params=ModelParams())
    with pytest.raises(ProviderError) as exc_info:
        async for _ in p_400.generate(req):
            pass
    assert exc_info.value.kind == "invalid_request"
    assert exc_info.value.status_code == 400

    # 401 Auth
    transport_401, _ = make_mock_gemini_transport([], status_code=401, error_body='{"error": {"message": "API key not valid"}}')
    p_401 = GeminiProvider(api_key="bad_key", client=httpx.AsyncClient(transport=transport_401))
    with pytest.raises(ProviderError) as exc_info:
        async for _ in p_401.generate(req):
            pass
    assert exc_info.value.kind == "auth"
    assert exc_info.value.status_code == 401

    # 429 Rate Limit
    transport_429, _ = make_mock_gemini_transport([], status_code=429, error_body='{"error": {"message": "Quota exceeded"}}')
    p_429 = GeminiProvider(api_key="key", client=httpx.AsyncClient(transport=transport_429))
    with pytest.raises(ProviderError) as exc_info:
        async for _ in p_429.generate(req):
            pass
    assert exc_info.value.kind == "rate_limit"
    assert exc_info.value.retryable is True


@pytest.mark.asyncio
async def test_gemini_plugin_discovery_and_registration(tmp_path: Path):
    """Verifies provider-gemini plugin is discovered and registered in PluginRegistry."""
    bus = EventBus()
    settings = SettingsStore(tmp_path / "settings")
    secrets = Secrets(tmp_path / "secrets")
    secrets.set("GEMINI_API_KEY", "discovered_test_key_123")

    registry = PluginRegistry(
        workspace=tmp_path / "ws",
        settings=settings,
        secrets=secrets,
        bus=bus,
    )

    builtin_dir = Path(__file__).resolve().parents[2] / "harness" / "plugins"
    registry.discover_all(builtin_dir=builtin_dir)
    assert "provider-gemini" in registry.discovered

    await registry.load_all()
    assert "gemini" in registry.providers
    gem_provider, _ = registry.providers["gemini"]
    assert isinstance(gem_provider, GeminiProvider)
    assert gem_provider.api_key == "discovered_test_key_123"
