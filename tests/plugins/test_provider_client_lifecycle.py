"""Unused providers must not allocate HTTP/TLS transports during bootstrap."""
from __future__ import annotations

import json
import os

import httpx
import pytest

from harness.kernel.bootstrap import bootstrap_kernel
from harness.plugins.providers.anthropic.provider import AnthropicProvider
from harness.plugins.providers.gemini.provider import GeminiProvider
from harness.plugins.providers.ollama.provider import OllamaProvider
from harness.sdk.types import Done, Message, ModelParams, ModelRequest, Role, TextBlock, TextDelta

PROVIDERS = [
    (AnthropicProvider, "claude-3-5-sonnet-latest"),
    (GeminiProvider, "gemini-2.5-flash"),
    (OllamaProvider, "llama3.2"),
]


def make_provider(provider_type, **kwargs):
    if provider_type is not OllamaProvider:
        kwargs["api_key"] = "synthetic-offline-provider-key"
    return provider_type(**kwargs)


def response_for(provider_type):
    if provider_type is AnthropicProvider:
        events = [
            {"type": "message_start", "message": {"usage": {"input_tokens": 1, "output_tokens": 0}}},
            {"type": "content_block_start", "index": 0, "content_block": {"type": "text", "text": ""}},
            {"type": "content_block_delta", "index": 0, "delta": {"type": "text_delta", "text": "OK"}},
            {"type": "content_block_stop", "index": 0},
            {"type": "message_delta", "delta": {"stop_reason": "end_turn"}, "usage": {"output_tokens": 1}},
            {"type": "message_stop"},
        ]
        body = "".join(f"event: {event['type']}\ndata: {json.dumps(event)}\n\n" for event in events)
        return httpx.Response(200, headers={"Content-Type": "text/event-stream"}, text=body)
    if provider_type is GeminiProvider:
        event = {
            "candidates": [{"content": {"parts": [{"text": "OK"}]}, "finishReason": "STOP"}],
            "usageMetadata": {"promptTokenCount": 1, "candidatesTokenCount": 1},
        }
        return httpx.Response(200, headers={"Content-Type": "text/event-stream"}, text=f"data: {json.dumps(event)}\n\n")
    # This adapter targets Ollama's OpenAI-compatible endpoint, not /api/chat.
    body = "".join(f"data: {json.dumps(event)}\n\n" for event in [
        {"choices": [{"delta": {"content": "OK"}, "finish_reason": None}]},
        {"choices": [{"delta": {}, "finish_reason": "stop"}],
         "usage": {"prompt_tokens": 1, "completion_tokens": 1}},
    ]) + "data: [DONE]\n\n"
    return httpx.Response(200, headers={"Content-Type": "text/event-stream"}, text=body)


def request_for(model):
    return ModelRequest(
        model=model, system=[], tools=[], params=ModelParams(),
        messages=[Message(role=Role.USER, content=[TextBlock(text="hello")])],
    )


@pytest.mark.asyncio
@pytest.mark.parametrize("provider_type,model", PROVIDERS)
async def test_transport_is_created_only_on_first_real_use_then_reused_and_closed(monkeypatch, provider_type, model):
    real_client = httpx.AsyncClient
    clients, requests = [], []

    def handler(request):
        requests.append(request)
        return response_for(provider_type)

    def factory(*args, **kwargs):
        kwargs["transport"] = httpx.MockTransport(handler)
        client = real_client(*args, **kwargs)
        clients.append(client)
        return client

    monkeypatch.setattr(httpx, "AsyncClient", factory)
    provider = make_provider(provider_type)
    try:
        assert clients == [], "Construction alone must not allocate a transport"
        for _ in range(2):
            events = [event async for event in provider.generate(request_for(model))]
            assert "".join(event.text for event in events if isinstance(event, TextDelta)) == "OK"
            expected_stop = "end_turn" if provider_type is AnthropicProvider else "stop"
            assert any(isinstance(event, Done) and event.stop_reason == expected_stop for event in events)
            assert len(clients) == 1
        assert len(requests) == 2
    finally:
        await provider.aclose()
    assert clients[0].is_closed


@pytest.mark.asyncio
@pytest.mark.parametrize("provider_type,model", PROVIDERS)
async def test_unused_close_does_not_create_a_client_or_allow_late_creation(monkeypatch, provider_type, model):
    calls = []
    real_client = httpx.AsyncClient

    def factory(*args, **kwargs):
        calls.append(True)
        kwargs["transport"] = httpx.MockTransport(lambda request: response_for(provider_type))
        return real_client(*args, **kwargs)

    monkeypatch.setattr(httpx, "AsyncClient", factory)
    provider = make_provider(provider_type)
    await provider.aclose()
    assert calls == []
    with pytest.raises(RuntimeError, match="closed"):
        provider._get_client()
    assert calls == []


@pytest.mark.asyncio
@pytest.mark.parametrize("provider_type,model", PROVIDERS)
async def test_injected_falsey_client_is_borrowed_not_replaced_or_closed(monkeypatch, provider_type, model):
    class FalseyClient(httpx.AsyncClient):
        def __bool__(self):
            return False

    external = FalseyClient(transport=httpx.MockTransport(lambda request: response_for(provider_type)))

    def forbidden(*args, **kwargs):
        raise AssertionError("An explicitly injected client must never be replaced")

    monkeypatch.setattr(httpx, "AsyncClient", forbidden)
    try:
        provider = make_provider(provider_type, client=external)
        assert provider._client is external
        await provider.aclose()
        assert not external.is_closed
        events = [event async for event in provider.generate(request_for(model))]
        assert "".join(event.text for event in events if isinstance(event, TextDelta)) == "OK"
    finally:
        await external.aclose()


@pytest.mark.asyncio
async def test_offline_bootstrap_keeps_providers_without_constructing_http_clients(tmp_path, monkeypatch):
    attempts = []
    (tmp_path / "home").mkdir(exist_ok=True)
    monkeypatch.setenv("HOME", str(tmp_path / "home"))
    for key in tuple(os.environ):
        if any(part in key.upper() for part in ("API_KEY", "TOKEN", "PASSWORD", "SECRET")):
            monkeypatch.delenv(key, raising=False)

    def forbidden(*args, **kwargs):
        attempts.append(True)
        raise AssertionError("Offline bootstrap must not create unused HTTP clients")

    monkeypatch.setattr(httpx, "AsyncClient", forbidden)
    runtime = await bootstrap_kernel(
        workspace=tmp_path / "workspace", config_dir=tmp_path / "config", resume_session=False,
    )
    try:
        assert attempts == []
        names = {provider.name for provider in runtime.registry.get_all_providers()}
        assert {"anthropic", "gemini", "ollama"} <= names, "Skipping failed providers is not lazy initialization"
    finally:
        await runtime.registry.unload_all()
