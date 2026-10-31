from __future__ import annotations

import base64
import json
import sys
from pathlib import Path
from typing import Any

import httpx
import pytest

from harness.kernel.bootstrap import bootstrap_kernel
from harness.kernel.context import derive_messages
from harness.kernel.transcript import TranscriptLog
from harness.plugins.providers.anthropic.provider import AnthropicProvider
from harness.plugins.stores.jsonl_transcript.store import JsonlTranscriptStore
from harness.sdk.types import Done, ImageBlock, ModelParams, ModelRequest, TextBlock, ToolResultBlock

IMAGE_DATA_LIMIT = 48 * 1024
SYNTHETIC_SECRET = "synthetic-mcp-secret-value-001"
PNG_BYTES = base64.b64decode(
    "iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAIAAACQd1PeAAAADElEQVR4nGP4z8AAAAMBAQDJ/pLvAAAAAElFTkSuQmCC"
)
JPEG_BYTES = base64.b64decode(
    "/9j/4AAQSkZJRgABAQEASABIAAD/2wBDAAYEBQYFBAYGBQYHBwYIChAKCgkJChQODwwQFxQYGBcUFhYaHSUfGhsjHBYWICwgIyYnKSopGR8tMC0oMCUoKSj/wAALCAABAAEBAREA/8QAFQABAQAAAAAAAAAAAAAAAAAAAAv/xAAUEAEAAAAAAAAAAAAAAAAAAAAA/9oACAEBAAA/AKp//9k="
)

_MCP_MEDIA_PEER = r'''
import json
import sys

fixture = json.load(open(sys.argv[1], encoding="utf-8"))
tools = [
    {"name": name, "description": name, "inputSchema": {"type": "object", "properties": {}},
     "annotations": {"readOnlyHint": True}}
    for name in ("get_media", "get_image_only", "unsupported_content", "invalid_image", "oversized_image", "unsupported_mime")
]

def respond(request, result):
    sys.stdout.write(json.dumps({"jsonrpc": "2.0", "id": request["id"], "result": result}) + "\n")
    sys.stdout.flush()

for line in sys.stdin:
    request = json.loads(line)
    method = request.get("method")
    if method == "initialize":
        respond(request, {
            "protocolVersion": "2025-03-26",
            "capabilities": {"tools": {}},
            "serverInfo": {"name": "mcp-media-fixture", "version": "1.0"},
        })
    elif method == "notifications/initialized":
        continue
    elif method == "tools/list":
        respond(request, {"tools": tools})
    elif method == "tools/call":
        name = request.get("params", {}).get("name")
        if name == "get_media":
            content = [
                {"type": "text", "text": "credential=" + fixture["secret"]},
                {"type": "image", "data": fixture["png"], "mimeType": "image/png"},
                {"type": "text", "text": "between images"},
                {"type": "image", "data": fixture["jpeg"], "mimeType": "image/jpeg"},
            ]
        elif name == "get_image_only":
            content = [{"type": "image", "data": fixture["png"], "mimeType": "image/png"}]
        elif name == "unsupported_content":
            content = [{"type": "audio", "data": "AQID", "mimeType": "audio/wav"}]
        elif name == "invalid_image":
            content = [{"type": "image", "data": "not-base64!", "mimeType": "image/png"}]
        elif name == "oversized_image":
            content = [{"type": "image", "data": fixture["oversized"], "mimeType": "image/png"}]
        elif name == "unsupported_mime":
            content = [{"type": "image", "data": fixture["png"], "mimeType": "image/tiff"}]
        else:
            content = [{"type": "text", "text": "unknown tool"}]
        respond(request, {"content": content, "isError": False})
'''


def _sse(events: list[dict[str, Any]]) -> bytes:
    return "".join(
        f"event: {event['type']}\ndata: {json.dumps(event)}\n\n"
        for event in events
    ).encode("utf-8")


def _tool_use_response() -> bytes:
    tool_names = ["get_media", "get_image_only", "unsupported_content", "invalid_image", "oversized_image", "unsupported_mime"]
    events: list[dict[str, Any]] = [
        {"type": "message_start", "message": {"usage": {"input_tokens": 2, "output_tokens": 0}}}
    ]
    for index, name in enumerate(tool_names):
        events.extend(
            [
                {
                    "type": "content_block_start",
                    "index": index,
                    "content_block": {"type": "tool_use", "id": f"toolu_{name}", "name": f"mcp__media__{name}", "input": {}},
                },
                {"type": "content_block_stop", "index": index},
            ]
        )
    events.extend(
        [
            {"type": "message_delta", "delta": {"stop_reason": "tool_use"}, "usage": {"output_tokens": 8}},
            {"type": "message_stop"},
        ]
    )
    return _sse(events)


def _final_response() -> bytes:
    return _sse(
        [
            {"type": "message_start", "message": {"usage": {"input_tokens": 2, "output_tokens": 0}}},
            {"type": "content_block_start", "index": 0, "content_block": {"type": "text", "text": ""}},
            {"type": "content_block_delta", "index": 0, "delta": {"type": "text_delta", "text": "Media inspected."}},
            {"type": "content_block_stop", "index": 0},
            {"type": "message_delta", "delta": {"stop_reason": "end_turn"}, "usage": {"output_tokens": 3}},
            {"type": "message_stop"},
        ]
    )


@pytest.mark.asyncio
async def test_mcp_images_survive_real_gate_transcript_reload_and_anthropic_request(tmp_path: Path):
    assert PNG_BYTES.startswith(b"\x89PNG\r\n\x1a\n") and PNG_BYTES.endswith(b"IEND\xaeB`\x82")
    assert JPEG_BYTES.startswith(b"\xff\xd8") and JPEG_BYTES.endswith(b"\xff\xd9")
    png_b64 = base64.b64encode(PNG_BYTES).decode("ascii")
    jpeg_b64 = base64.b64encode(JPEG_BYTES).decode("ascii")
    fixture_data = {
        "png": png_b64,
        "jpeg": jpeg_b64,
        "secret": SYNTHETIC_SECRET,
        "oversized": "A" * (IMAGE_DATA_LIMIT + 4),
    }
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    config = tmp_path / "config"
    config.mkdir()
    peer = tmp_path / "mcp_media_peer.py"
    fixture = tmp_path / "mcp_media.json"
    peer.write_text(_MCP_MEDIA_PEER, encoding="utf-8")
    fixture.write_text(json.dumps(fixture_data), encoding="utf-8")

    runtime = await bootstrap_kernel(
        workspace=workspace,
        config_dir=config,
        resume_session=False,
        overrides={
            "plugins.bridge-mcp-client.servers": {
                "media": {"transport": "stdio", "command": sys.executable, "args": [str(peer), str(fixture)]},
            },
        },
    )

    captured_bodies: list[dict[str, Any]] = []

    def anthropic_response(request: httpx.Request) -> httpx.Response:
        captured_bodies.append(json.loads(request.content))
        body = _tool_use_response() if len(captured_bodies) == 1 else _final_response()
        return httpx.Response(200, headers={"Content-Type": "text/event-stream"}, content=body)

    provider_client = httpx.AsyncClient(transport=httpx.MockTransport(anthropic_response))
    provider = AnthropicProvider(name="anthropic_media_test", api_key="synthetic-test-key", client=provider_client)
    runtime.registry.register_provider(provider, plugin_name="test_anthropic_media")
    runtime.secrets.set("MCP_MEDIA_TEST_SECRET", SYNTHETIC_SECRET)
    session = await runtime.session_manager.create_session()

    try:
        assert runtime.registry.get_tool_spec("mcp__media__get_media") is not None
        assert runtime.registry.get_tool_spec("mcp__media__get_image_only") is not None
        assert session.log.store is not None
        result = await runtime.runner.execute_turn(
            session=session.info,
            prompt="Read the attachment and report the supported content.",
            options={"provider": provider.name, "model": "claude-3-5-sonnet-latest"},
        )
        assert result.stop_reason == "end_turn"
        assert "Media inspected." in result.output
        assert len(captured_bodies) == 2

        request_messages = captured_bodies[1]["messages"]
        serialized = json.dumps(request_messages)
        assert SYNTHETIC_SECRET not in serialized
        tool_results = {
            block["tool_use_id"]: block
            for message in request_messages
            for block in message.get("content", [])
            if isinstance(block, dict) and block.get("type") == "tool_result"
        }

        image_only_result = tool_results["toolu_get_image_only"]
        assert image_only_result["is_error"] is False
        assert image_only_result["content"] == [{
            "type": "image",
            "source": {"type": "base64", "media_type": "image/png", "data": png_b64},
        }]
        assert "Success" not in str(image_only_result["content"])

        media_result = tool_results["toolu_get_media"]
        assert media_result["is_error"] is False
        assert [block["type"] for block in media_result["content"]] == ["text", "image", "text", "image"]
        assert media_result["content"][0]["text"] == "credential=[REDACTED SECRET]"
        assert media_result["content"][1]["source"] == {
            "type": "base64", "media_type": "image/png", "data": png_b64,
        }
        assert media_result["content"][2]["text"] == "between images"
        assert media_result["content"][3]["source"] == {
            "type": "base64", "media_type": "image/jpeg", "data": jpeg_b64,
        }

        for call_id, expected in [
            ("toolu_unsupported_content", "unsupported MCP tool result content type"),
            ("toolu_invalid_image", "base64"),
            ("toolu_oversized_image", "size limit"),
            ("toolu_unsupported_mime", "media type"),
        ]:
            tool_result = tool_results[call_id]
            assert tool_result["is_error"] is True
            assert expected.lower() in str(tool_result["content"]).lower()
            assert "Success" not in str(tool_result["content"])

        reloaded_log = TranscriptLog(JsonlTranscriptStore(session.log.session_dir))
        reloaded_records = await reloaded_log.load(compact=False)
        reloaded_messages = derive_messages(reloaded_records)
        persisted_media = next(
            block
            for message in reloaded_messages
            for block in message.content
            if isinstance(block, ToolResultBlock) and block.tool_use_id == "toolu_get_media"
        )
        assert [type(block) for block in persisted_media.content] == [TextBlock, ImageBlock, TextBlock, ImageBlock]
        assert persisted_media.content[0].text == "credential=[REDACTED SECRET]"
        assert persisted_media.content[1] == ImageBlock(media_type="image/png", data_b64=png_b64)
        assert persisted_media.content[3] == ImageBlock(media_type="image/jpeg", data_b64=jpeg_b64)
        persisted_image_only = next(
            block
            for message in reloaded_messages
            for block in message.content
            if isinstance(block, ToolResultBlock) and block.tool_use_id == "toolu_get_image_only"
        )
        assert persisted_image_only.is_error is False
        assert persisted_image_only.content == [ImageBlock(media_type="image/png", data_b64=png_b64)]
        assert SYNTHETIC_SECRET not in json.dumps(reloaded_records)

        final_assistant = max(
            i for i, row in enumerate(reloaded_records) if row["kind"] == "assistant.message"
        )
        reloaded_request = ModelRequest(
            model="claude-3-5-sonnet-latest",
            system=[],
            tools=[],
            params=ModelParams(),
            messages=derive_messages(reloaded_records[:final_assistant]),
        )
        replay_bodies: list[dict[str, Any]] = []

        def strict_anthropic_replay(request: httpx.Request) -> httpx.Response:
            assert request.method == "POST"
            assert request.url.path.endswith("/messages")
            body = json.loads(request.content)
            replay_bodies.append(body)
            serialized = json.dumps(body)
            assert SYNTHETIC_SECRET not in serialized
            results = {
                block["tool_use_id"]: block
                for message in body["messages"]
                for block in message.get("content", [])
                if isinstance(block, dict) and block.get("type") == "tool_result"
            }
            assert results["toolu_get_image_only"]["is_error"] is False
            assert results["toolu_get_image_only"]["content"] == [{
                "type": "image",
                "source": {"type": "base64", "media_type": "image/png", "data": png_b64},
            }]
            assert results["toolu_get_media"]["is_error"] is False
            assert [part["type"] for part in results["toolu_get_media"]["content"]] == [
                "text", "image", "text", "image",
            ]
            assert results["toolu_get_media"]["content"][0]["text"] == "credential=[REDACTED SECRET]"
            assert results["toolu_get_media"]["content"][1]["source"] == {
                "type": "base64", "media_type": "image/png", "data": png_b64,
            }
            assert results["toolu_get_media"]["content"][2]["text"] == "between images"
            assert results["toolu_get_media"]["content"][3]["source"] == {
                "type": "base64", "media_type": "image/jpeg", "data": jpeg_b64,
            }
            assert "Success" not in str(results["toolu_get_image_only"]["content"])
            return httpx.Response(200, headers={"Content-Type": "text/event-stream"}, content=_final_response())

        replay_client = httpx.AsyncClient(transport=httpx.MockTransport(strict_anthropic_replay))
        replay_provider = AnthropicProvider(
            name="anthropic_media_replay", api_key="synthetic-test-key", client=replay_client,
        )
        try:
            replay_events = [event async for event in replay_provider.generate(reloaded_request)]
            assert len(replay_bodies) == 1
            assert any(isinstance(event, Done) for event in replay_events)
        finally:
            await replay_client.aclose()
    finally:
        await runtime.registry.unload_all()
        await provider_client.aclose()
