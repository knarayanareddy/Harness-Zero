from __future__ import annotations

import base64
import json
from typing import Any

import httpx
import pytest

from harness.plugins.providers.anthropic.provider import AnthropicProvider
from harness.plugins.providers.gemini.provider import GeminiProvider
from harness.plugins.providers.ollama.provider import OllamaProvider
from harness.sdk.errors import ProviderError
from harness.sdk.types import ImageBlock, Message, ModelParams, ModelRequest, Role, TextBlock, ToolResultBlock

PNG_BYTES = base64.b64decode(
    "iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAIAAACQd1PeAAAADElEQVR4nGP4z8AAAAMBAQDJ/pLvAAAAAElFTkSuQmCC"
)
JPEG_BYTES = base64.b64decode(
    "/9j/4AAQSkZJRgABAQEASABIAAD/2wBDAAYEBQYFBAYGBQYHBwYIChAKCgkJChQODwwQFxQYGBcUFhYaHSUfGhsjHBYWICwgIyYnKSopGR8tMC0oMCUoKSj/wAALCAABAAEBAREA/8QAFQABAQAAAAAAAAAAAAAAAAAAAAv/xAAUEAEAAAAAAAAAAAAAAAAAAAAA/9oACAEBAAA/AKp//9k="
)
MODEL_IDS = {
    "anthropic": "claude-3-5-sonnet-latest",
    "gemini": "gemini-2.0-flash",
    "ollama": "llama3.2:latest",
}


def _stream_body(provider_name: str) -> bytes:
    if provider_name == "anthropic":
        events = [
            {"type": "message_start", "message": {"usage": {"input_tokens": 1}}},
            {"type": "message_stop"},
        ]
        return "\n\n".join(f"data: {json.dumps(event)}" for event in events).encode()
    if provider_name == "gemini":
        event = {"candidates": [{"content": {"parts": [{"text": "ok"}], "role": "model"}, "finishReason": "STOP"}]}
        return f"data: {json.dumps(event)}\n\n".encode()
    event = {"choices": [{"delta": {}, "finish_reason": "stop"}]}
    return f"data: {json.dumps(event)}\n\ndata: [DONE]\n\n".encode()


def _provider_with_capture(provider_name: str) -> tuple[Any, httpx.AsyncClient, list[httpx.Request]]:
    requests: list[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        requests.append(request)
        return httpx.Response(
            200,
            headers={"Content-Type": "text/event-stream"},
            content=_stream_body(provider_name),
        )

    client = httpx.AsyncClient(transport=httpx.MockTransport(handler))
    if provider_name == "anthropic":
        provider = AnthropicProvider(api_key="test-key", client=client)
    elif provider_name == "gemini":
        provider = GeminiProvider(api_key="test-key", client=client)
    else:
        provider = OllamaProvider(client=client)
    return provider, client, requests


def _request(provider_name: str, content: list[Any], *, model: str | None = None) -> ModelRequest:
    return ModelRequest(
        model=model or MODEL_IDS[provider_name],
        system=[],
        messages=[Message(role=Role.USER, content=content)],
        tools=[],
        params=ModelParams(),
    )


def _image_bytes_from_body(provider_name: str, request_body: dict[str, Any]) -> tuple[str, str]:
    if provider_name == "anthropic":
        image = request_body["messages"][0]["content"][1]
        assert image["type"] == "image"
        assert image["source"]["type"] == "base64"
        return image["source"]["media_type"], image["source"]["data"]
    if provider_name == "gemini":
        image = request_body["contents"][0]["parts"][1]["inlineData"]
        return image["mimeType"], image["data"]
    image = request_body["messages"][0]["content"][1]
    assert image["type"] == "image_url"
    assert isinstance(image["image_url"], str)
    data_url = image["image_url"]
    mime_type, data = data_url.removeprefix("data:").split(";base64,", 1)
    return mime_type, data


@pytest.mark.parametrize("provider_name", ["anthropic", "gemini", "ollama"])
@pytest.mark.parametrize("media_type,image_bytes", [("image/png", PNG_BYTES), ("image/jpeg", JPEG_BYTES)])
@pytest.mark.asyncio
async def test_base64_image_block_serializes_from_sdk_fields_without_reencoding(
    provider_name: str, media_type: str, image_bytes: bytes
):
    assert image_bytes.startswith(b"\x89PNG\r\n\x1a\n") if media_type == "image/png" else image_bytes.startswith(b"\xff\xd8")
    assert image_bytes.endswith(b"IEND\xaeB`\x82") if media_type == "image/png" else image_bytes.endswith(b"\xff\xd9")
    data_b64 = base64.b64encode(image_bytes).decode("ascii")
    image = ImageBlock(media_type=media_type, data_b64=data_b64)
    provider, client, captured = _provider_with_capture(provider_name)
    try:
        await anext(provider.generate(_request(provider_name, [TextBlock(text="describe"), image])))
    finally:
        await client.aclose()

    assert len(captured) == 1
    body = json.loads(captured[0].content)
    if provider_name == "anthropic":
        assert body["messages"][0]["content"][0] == {"type": "text", "text": "describe"}
    elif provider_name == "gemini":
        assert body["contents"][0]["parts"][0] == {"text": "describe"}
    else:
        assert body["messages"][0]["content"][0] == {"type": "text", "text": "describe"}
    actual_type, actual_b64 = _image_bytes_from_body(provider_name, body)
    assert actual_type == media_type
    assert actual_b64 == data_b64
    assert base64.b64decode(actual_b64, validate=True) == image_bytes


@pytest.mark.asyncio
async def test_anthropic_https_image_url_is_serialized_as_url_source_without_fetching():
    url = "https://images.example.test/synthetic.png"
    provider, client, captured = _provider_with_capture("anthropic")
    try:
        await anext(provider.generate(_request("anthropic", [TextBlock(text="describe"), ImageBlock(media_type="image/png", url=url)])))
    finally:
        await client.aclose()
    assert len(captured) == 1
    body = json.loads(captured[0].content)
    assert body["messages"][0]["content"][1] == {"type": "image", "source": {"type": "url", "url": url}}


@pytest.mark.asyncio
async def test_anthropic_tool_result_image_is_serialized_as_content_block():
    image = ImageBlock(media_type="image/png", data_b64=base64.b64encode(PNG_BYTES).decode("ascii"))
    tool_result = ToolResultBlock(tool_use_id="toolu_1", content=[TextBlock(text="found it"), image])
    provider, client, captured = _provider_with_capture("anthropic")
    try:
        await anext(provider.generate(_request("anthropic", [tool_result])))
    finally:
        await client.aclose()
    body = json.loads(captured[0].content)
    content = body["messages"][0]["content"][0]["content"]
    assert content == [
        {"type": "text", "text": "found it"},
        {"type": "image", "source": {"type": "base64", "media_type": "image/png", "data": base64.b64encode(PNG_BYTES).decode("ascii")}},
    ]


@pytest.mark.parametrize("provider_name", ["ollama"])
@pytest.mark.asyncio
async def test_unsupported_image_urls_fail_explicitly_without_network_request(provider_name: str):
    provider, client, captured = _provider_with_capture(provider_name)
    try:
        with pytest.raises(ProviderError) as exc_info:
            await anext(provider.generate(_request(provider_name, [ImageBlock(media_type="image/png", url="https://images.example.test/a.png")])))
    finally:
        await client.aclose()
    assert exc_info.value.provider == provider_name
    assert exc_info.value.kind == "bad_request"
    assert exc_info.value.retryable is False
    assert captured == []


@pytest.mark.asyncio
async def test_gemini_external_https_image_url_uses_generate_content_file_data():
    url = "https://images.example.test/synthetic.jpg"
    provider, client, captured = _provider_with_capture("gemini")
    try:
        await anext(provider.generate(_request("gemini", [TextBlock(text="describe"), ImageBlock(media_type="image/jpeg", url=url)], model="gemini-2.5-flash")))
    finally:
        await client.aclose()
    assert len(captured) == 1
    body = json.loads(captured[0].content)
    assert body["contents"][0]["parts"] == [{"text": "describe"}, {"fileData": {"mimeType": "image/jpeg", "fileUri": url}}]


@pytest.mark.asyncio
async def test_gemini_rejects_external_url_for_unsupported_gemini_2_model():
    provider, client, captured = _provider_with_capture("gemini")
    try:
        with pytest.raises(ProviderError) as exc_info:
            await anext(provider.generate(_request("gemini", [ImageBlock(media_type="image/png", url="https://images.example.test/a.png")])))
    finally:
        await client.aclose()
    assert exc_info.value.kind == "bad_request"
    assert "Gemini 2.0" in str(exc_info.value)
    assert captured == []


@pytest.mark.asyncio
@pytest.mark.parametrize("model", ["", "gemini/gemini-2.0-flash", "google/gemini-2.0-flash"])
async def test_gemini_url_validation_uses_the_actual_fallback_or_prefixed_model(model):
    provider, client, captured = _provider_with_capture("gemini")
    request = ModelRequest(model=model, system=[], tools=[], params=ModelParams(), messages=[
        Message(role=Role.USER, content=[ImageBlock(media_type="image/png", url="https://images.example.test/a.png")])])
    try:
        with pytest.raises(ProviderError) as error:
            await anext(provider.generate(request))
    finally:
        await client.aclose()
    assert error.value.kind == "bad_request"
    assert error.value.model == "gemini-2.0-flash"
    assert captured == []
    assert request.model == model, "Resolving the wire model must not mutate the canonical request"


@pytest.mark.parametrize("provider_name,model,image", [
    ("anthropic", MODEL_IDS["anthropic"], ImageBlock(media_type="image/png", url="http://images.example.test/a.png")),
    ("gemini", "gemini-2.5-flash", ImageBlock(media_type="image/png", url="http://images.example.test/a.png")),
    ("gemini", "gemini-2.5-flash", ImageBlock(media_type="image/heic", url="https://images.example.test/a.heic")),
], ids=["anthropic-http", "gemini-http", "gemini-unsupported-url-format"])
@pytest.mark.asyncio
async def test_url_contract_rejects_insecure_or_undocumented_image_urls(provider_name: str, model: str, image: ImageBlock):
    provider, client, captured = _provider_with_capture(provider_name)
    try:
        with pytest.raises(ProviderError) as exc_info:
            await anext(provider.generate(_request(provider_name, [image], model=model)))
    finally:
        await client.aclose()
    assert exc_info.value.kind == "bad_request"
    assert captured == []


@pytest.mark.parametrize("provider_name,model,url", [
    ("anthropic", MODEL_IDS["anthropic"], "https://[::1/a.png"),
    ("anthropic", MODEL_IDS["anthropic"], "https://images.example.test:notaport/a.png"),
    ("gemini", "gemini-2.5-flash", "https://[::1/a.png"),
    ("gemini", "gemini-2.5-flash", "https://images.example.test:notaport/a.png"),
], ids=["anthropic-malformed-ipv6", "anthropic-invalid-port", "gemini-malformed-ipv6", "gemini-invalid-port"])
@pytest.mark.asyncio
async def test_malformed_supported_https_authority_fails_as_bad_request_without_sending(provider_name: str, model: str, url: str):
    provider, client, captured = _provider_with_capture(provider_name)
    try:
        with pytest.raises(ProviderError) as exc_info:
            await anext(provider.generate(_request(provider_name, [ImageBlock(media_type="image/png", url=url)], model=model)))
    finally:
        await client.aclose()
    assert exc_info.value.provider == provider_name
    assert exc_info.value.model == model
    assert exc_info.value.kind == "bad_request"
    assert exc_info.value.retryable is False
    assert captured == []


@pytest.mark.parametrize("provider_name", ["anthropic", "gemini", "ollama"])
@pytest.mark.parametrize("image", [
    ImageBlock(media_type="image/tiff", data_b64=base64.b64encode(PNG_BYTES).decode("ascii")),
    ImageBlock(media_type="image/png"),
    ImageBlock(media_type="image/png", data_b64=base64.b64encode(PNG_BYTES).decode("ascii"), url="https://images.example.test/a.png"),
    ImageBlock(media_type="image/png", data_b64="not-base64!"),
], ids=["unsupported-media-type", "missing-source", "ambiguous-source", "invalid-base64"])
@pytest.mark.asyncio
async def test_invalid_image_contract_fails_as_sdk_provider_error(provider_name: str, image: ImageBlock):
    provider, client, captured = _provider_with_capture(provider_name)
    try:
        with pytest.raises(ProviderError) as exc_info:
            await anext(provider.generate(_request(provider_name, [image])))
    finally:
        await client.aclose()
    assert exc_info.value.provider == provider_name
    assert exc_info.value.model == MODEL_IDS[provider_name]
    assert exc_info.value.kind == "bad_request"
    assert exc_info.value.retryable is False
    assert captured == []


@pytest.mark.parametrize("provider_name", ["gemini", "ollama"])
@pytest.mark.asyncio
async def test_tool_result_images_rejected_when_this_adapter_does_not_support_them(provider_name: str):
    image = ImageBlock(media_type="image/png", data_b64=base64.b64encode(PNG_BYTES).decode("ascii"))
    tool_result = ToolResultBlock(tool_use_id="toolu_1", content=[TextBlock(text="result"), image])
    provider, client, captured = _provider_with_capture(provider_name)
    try:
        with pytest.raises(ProviderError) as exc_info:
            await anext(provider.generate(_request(provider_name, [tool_result])))
    finally:
        await client.aclose()
    assert exc_info.value.kind == "bad_request"
    assert "tool result" in str(exc_info.value).lower()
    assert captured == []
