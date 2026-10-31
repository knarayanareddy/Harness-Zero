from __future__ import annotations

import asyncio
import json
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from typing import Any

import pytest

from harness.kernel.approvals import ApprovalBroker
from harness.kernel.breaker import CircuitBreaker, CircuitState
from harness.kernel.bus import EventBus
from harness.kernel.gate import PolicyGate
from harness.kernel.jail import WorkspaceJail
from harness.kernel.registry import PluginRegistry
from harness.kernel.runner import TurnRunner
from harness.kernel.secrets import Secrets
from harness.kernel.settings import SettingsStore
from harness.plugins.providers.anthropic.provider import AnthropicProvider
from harness.sdk.types import (
    Done,
    Message,
    ModelParams,
    ModelRequest,
    Role,
    SessionInfo,
    TextBlock,
    TextDelta,
    ThinkingDelta,
    ToolCallEnd,
    UsageEvent,
)


def _sse(*events: dict[str, Any]) -> bytes:
    return "".join(f"event: {event['type']}\ndata: {json.dumps(event, ensure_ascii=False)}\n\n" for event in events).encode()


@asynccontextmanager
async def _local_sse_peer(body: bytes) -> AsyncIterator[str]:
    """Serve an HTTP/1.1 SSE response in tiny writes to exercise arbitrary fragmentation."""

    async def handle(reader: asyncio.StreamReader, writer: asyncio.StreamWriter) -> None:
        try:
            await reader.readuntil(b"\r\n\r\n")
            writer.write(
                b"HTTP/1.1 200 OK\r\n"
                b"Content-Type: text/event-stream\r\n"
                b"Connection: close\r\n"
                + f"Content-Length: {len(body)}\r\n\r\n".encode()
            )
            await writer.drain()
            for offset in range(0, len(body), 3):
                writer.write(body[offset : offset + 3])
                await writer.drain()
        except (ConnectionError, asyncio.IncompleteReadError):
            pass
        finally:
            writer.close()
            await writer.wait_closed()

    server = await asyncio.start_server(handle, "127.0.0.1", 0)
    address = server.sockets[0].getsockname()
    try:
        yield f"http://127.0.0.1:{address[1]}/v1"
    finally:
        server.close()
        await server.wait_closed()


def _request() -> ModelRequest:
    return ModelRequest(
        model="claude-3-7-sonnet",
        system=[],
        messages=[Message(role=Role.USER, content=[TextBlock(text="test stream")])],
        tools=[],
        params=ModelParams(),
    )


@pytest.mark.asyncio
async def test_fragmented_local_sse_peer_preserves_text_tool_and_thinking_events():
    async with _local_sse_peer(
        _sse(
            {"type": "message_start", "message": {"usage": {"input_tokens": 12}}},
            {"type": "content_block_start", "index": 0, "content_block": {"type": "thinking", "thinking": ""}},
            {"type": "content_block_delta", "index": 0, "delta": {"type": "thinking_delta", "thinking": "reason 🌍"}},
            {"type": "content_block_stop", "index": 0},
            {"type": "content_block_start", "index": 1, "content_block": {"type": "text", "text": ""}},
            {"type": "content_block_delta", "index": 1, "delta": {"type": "text_delta", "text": "café 🌍"}},
            {"type": "content_block_stop", "index": 1},
            {"type": "content_block_start", "index": 2, "content_block": {"type": "tool_use", "id": "toolu_1", "name": "lookup"}},
            {"type": "content_block_delta", "index": 2, "delta": {"type": "input_json_delta", "partial_json": '{"q":'}},
            {"type": "content_block_delta", "index": 2, "delta": {"type": "input_json_delta", "partial_json": '"x"}'}},
            {"type": "content_block_stop", "index": 2},
            {"type": "message_delta", "delta": {"stop_reason": "tool_use"}, "usage": {"output_tokens": 4}},
            {"type": "message_delta", "usage": {"output_tokens": 8}},
            {"type": "message_stop"},
        )
    ) as base_url:
        provider = AnthropicProvider(base_url=base_url, api_key="local-test", timeout_s=5)
        try:
            events = [event async for event in provider.generate(_request())]
        finally:
            await provider.aclose()

    thinking = [event for event in events if isinstance(event, ThinkingDelta)]
    text = [event for event in events if isinstance(event, TextDelta)]
    tools = [event for event in events if isinstance(event, ToolCallEnd)]
    usage = [event for event in events if isinstance(event, UsageEvent)]
    done = [event for event in events if isinstance(event, Done)]

    assert [event.text for event in thinking] == ["reason 🌍"]
    assert [event.text for event in text] == ["café 🌍"]
    assert [(event.id, event.name, event.arguments) for event in tools] == [("toolu_1", "lookup", {"q": "x"})]
    assert len(usage) == 1
    assert usage[0].usage.input_tokens == 12
    assert usage[0].usage.output_tokens == 8
    assert [event.stop_reason for event in done] == ["tool_use"]


@pytest.mark.parametrize(
    "response_body, expected_error",
    [
        (
            _sse(
                {"type": "message_start", "message": {"usage": {"input_tokens": 2}}},
                {"type": "content_block_delta", "delta": {"type": "text_delta", "text": "partial"}},
                {"type": "error", "error": {"type": "overloaded_error", "message": "busy"}},
                {"type": "message_stop"},
            ),
            "busy",
        ),
        (
            _sse(
                {"type": "message_start", "message": {"usage": {"input_tokens": 2}}},
                {"type": "message_delta", "delta": {"stop_reason": "end_turn"}, "usage": {"output_tokens": 3}},
            ),
            "before message_stop",
        ),
    ],
    ids=["sse-error-event", "premature-eof"],
)
@pytest.mark.asyncio
async def test_stream_failure_is_runner_error_and_counts_toward_breaker(
    tmp_path, response_body: bytes, expected_error: str
):
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    settings = SettingsStore(tmp_path / "settings.json")
    secrets = Secrets(tmp_path / "secrets.json")
    bus = EventBus()
    registry = PluginRegistry(workspace=workspace, settings=settings, secrets=secrets, bus=bus)
    breaker = CircuitBreaker(max_failures=1, reset_timeout_s=30)

    async with _local_sse_peer(response_body) as base_url:
        provider = AnthropicProvider(base_url=base_url, api_key="local-test", timeout_s=5)
        registry.register_provider(provider)
        gate = PolicyGate(
            registry=registry,
            jail=WorkspaceJail(workspace),
            settings=settings,
            secrets=secrets,
            approvals=ApprovalBroker(bus),
            bus=bus,
        )
        runner = TurnRunner(registry=registry, gate=gate, bus=bus, settings=settings, breaker=breaker)
        try:
            result = await runner.execute_turn(
                session=SessionInfo(session_id="anthropic-stream", workspace=workspace),
                prompt="make a streamed request",
                options={"provider": "anthropic", "model": "claude-3-7-sonnet"},
            )
        finally:
            await provider.aclose()

    assert result.stop_reason == "error"
    assert expected_error in (result.error or "")
    assert breaker.get_state("anthropic", "claude-3-7-sonnet") is CircuitState.OPEN


@pytest.mark.asyncio
async def test_stream_cancellation_remains_cancellation_not_provider_error():
    async with _local_sse_peer(
        _sse(
            {"type": "message_start", "message": {"usage": {"input_tokens": 2}}},
            {"type": "content_block_delta", "delta": {"type": "text_delta", "text": "first"}},
            {"type": "message_stop"},
        )
    ) as base_url:
        provider = AnthropicProvider(base_url=base_url, api_key="local-test", timeout_s=5)
        stream = provider.generate(_request())
        try:
            assert isinstance(await anext(stream), TextDelta)
            with pytest.raises(asyncio.CancelledError):
                await stream.athrow(asyncio.CancelledError())
        finally:
            await provider.aclose()
