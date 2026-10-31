"""MCP tool-result content mapping, including typed inline image payloads."""
from __future__ import annotations

import asyncio
import base64
import json
import os
import sys
from pathlib import Path
from typing import Any

import pytest

from harness.plugins.bridges.mcp_server.server import MCPServer
from harness.sdk.types import (
    ImageBlock,
    RiskClass,
    TextBlock,
    ThinkingBlock,
    ToolCall,
    ToolResult,
    ToolSpec,
)

_PNG_B64 = "iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAQAAAC1HAwCAAAAC0lEQVR42mP8/x8AAwMCAO+/TioAAAAASUVORK5CYII="
_SPEC = ToolSpec(
    name="result_content",
    description="Returns a typed result for MCP serialization tests.",
    input_schema={"type": "object"},
    risk_class=RiskClass.READ,
)


def _server_for(result: ToolResult) -> MCPServer:
    async def execute(_: ToolCall) -> ToolResult:
        return result

    return MCPServer(lambda: [_SPEC], execute, expose_list=[_SPEC.name])


async def _call(server: MCPServer) -> dict[str, Any]:
    response = await server.handle_request({
        "jsonrpc": "2.0",
        "id": "result",
        "method": "tools/call",
        "params": {"name": _SPEC.name, "arguments": {}},
    })
    assert response is not None
    return response["result"]


@pytest.mark.asyncio
async def test_inline_image_result_preserves_exact_mcp_image_fields():
    result = await _call(_server_for(ToolResult(content=[ImageBlock("image/png", _PNG_B64)])))

    assert result == {
        "content": [{"type": "image", "data": _PNG_B64, "mimeType": "image/png"}],
        "isError": False,
    }
    assert base64.b64decode(result["content"][0]["data"], validate=True) == base64.b64decode(_PNG_B64)


@pytest.mark.asyncio
async def test_image_mime_type_is_case_insensitive_and_preserves_wire_spelling():
    result = await _call(_server_for(ToolResult(content=[ImageBlock("Image/PNG", _PNG_B64)])))

    assert result == {
        "content": [{"type": "image", "data": _PNG_B64, "mimeType": "Image/PNG"}],
        "isError": False,
    }


@pytest.mark.asyncio
async def test_mixed_and_multiple_text_blocks_keep_order_and_boundaries():
    result = await _call(_server_for(ToolResult(content=[
        TextBlock("first"),
        ImageBlock("image/png", _PNG_B64),
        TextBlock("second"),
        TextBlock("third"),
    ])))

    assert result["content"] == [
        {"type": "text", "text": "first"},
        {"type": "image", "data": _PNG_B64, "mimeType": "image/png"},
        {"type": "text", "text": "second"},
        {"type": "text", "text": "third"},
    ]
    assert result["isError"] is False


@pytest.mark.asyncio
async def test_image_tool_error_flag_is_preserved_and_empty_result_stays_success():
    failed = await _call(_server_for(ToolResult(content=[ImageBlock("image/png", _PNG_B64)], is_error=True)))
    empty = await _call(_server_for(ToolResult(content=[])))

    assert failed["content"] == [{"type": "image", "data": _PNG_B64, "mimeType": "image/png"}]
    assert failed["isError"] is True
    assert empty == {"content": [{"type": "text", "text": "Success"}], "isError": False}


@pytest.mark.asyncio
@pytest.mark.parametrize("block", [
    ImageBlock("image/png", url="https://example.invalid/private?token=sentinel"),
    ImageBlock("image/png", _PNG_B64, url="https://example.invalid/private?token=sentinel"),
])
async def test_non_inline_or_ambiguous_images_fail_safely_without_echoing_payload(block: ImageBlock):
    result = await _call(_server_for(ToolResult(content=[TextBlock("before"), block])))

    assert result["isError"] is True
    assert result["content"] and result["content"][0]["type"] == "text"
    text = result["content"][0]["text"]
    assert "inline" in text.lower() or "both" in text.lower()
    assert "example.invalid" not in text
    assert "sentinel" not in text
    assert _PNG_B64 not in text


@pytest.mark.asyncio
@pytest.mark.parametrize("block", [
    ImageBlock("image/png", "not base64!"),
    ImageBlock("text/plain", _PNG_B64),
    ImageBlock("image/png", ""),
    ImageBlock("image/png", None),
    ImageBlock("image/png; charset=utf-8", _PNG_B64),
])
async def test_malformed_inline_image_shape_returns_safe_tool_error(block: ImageBlock):
    result = await _call(_server_for(ToolResult(content=[block])))

    assert result["isError"] is True
    text = result["content"][0]["text"]
    assert "image" in text.lower()
    assert _PNG_B64 not in text
    assert "not base64" not in text


@pytest.mark.asyncio
async def test_unsupported_typed_block_is_not_flattened_or_leaked():
    hidden = "private-thinking-sentinel"
    result = await _call(_server_for(ToolResult(content=[TextBlock("visible"), ThinkingBlock(hidden)])))

    assert result["isError"] is True
    assert len(result["content"]) == 1
    assert result["content"][0]["type"] == "text"
    assert hidden not in result["content"][0]["text"]
    assert "ThinkingBlock" not in result["content"][0]["text"]


@pytest.mark.asyncio
async def test_actual_cli_gate_returns_and_audits_registered_workspace_image_tool(tmp_path: Path):
    workspace = tmp_path / "workspace"
    config = tmp_path / "config"
    home = tmp_path / "home"
    plugin = workspace / ".harness" / "plugins" / "t18d-image-result"
    plugin.mkdir(parents=True)
    config.mkdir()
    home.mkdir(exist_ok=True)
    (config / "settings.json").write_text(json.dumps({
        "kernel": {"plugins": {"trusted_workspace_plugins": True}},
        "plugins": {"bridge-mcp-server": {"expose": ["fixed_inline_image"]}},
    }), encoding="utf-8")
    (plugin / "harness_plugin.toml").write_text(
        '[plugin]\nname = "t18d-image-result"\nversion = "1.0.0"\napi_version = "1"\n'
        'summary = "Temporary fixed image test tool"\nprovides = ["tool"]\n'
        'capabilities = []\nentry = "plugin:FixedImagePlugin"\n',
        encoding="utf-8",
    )
    (plugin / "plugin.py").write_text(
        "from harness.sdk.contracts import ToolSpec\n"
        "from harness.sdk.types import ImageBlock, RiskClass, TextBlock, ToolResult\n"
        "class FixedImagePlugin:\n"
        "    async def setup(self, ctx):\n"
        "        spec = ToolSpec(name='fixed_inline_image', description='fixed image', "
        "input_schema={'type': 'object'}, risk_class=RiskClass.READ)\n"
        "        async def handle(args, tool_ctx):\n"
        f"            return ToolResult(content=[TextBlock('before'), ImageBlock('image/png', '{_PNG_B64}'), TextBlock('after')])\n"
        "        ctx.register_tool(spec, handle)\n",
        encoding="utf-8",
    )

    env = {
        "HOME": str(home),
        "HARNESS_CONFIG_DIR": str(config),
        "PYTHONPATH": str(Path(__file__).resolve().parents[3]),
        "PYTHONDONTWRITEBYTECODE": "1",
        "PATH": os.pathsep.join((str(Path(sys.executable).parent), os.defpath)),
        "TMPDIR": os.environ.get("TMPDIR", "/private/var/folders/zh/p2jpcjh97s13b5lk6hr5xz8r0000gn/T/opencode"),
    }
    process = await asyncio.create_subprocess_exec(
        sys.executable,
        "-m",
        "harness.cli",
        "mcp",
        "--workspace",
        str(workspace),
        cwd=Path(__file__).resolve().parents[3],
        env=env,
        stdin=asyncio.subprocess.PIPE,
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.PIPE,
    )
    assert process.stdin is not None and process.stdout is not None and process.stderr is not None
    stderr_task = asyncio.create_task(process.stderr.read())
    responses: dict[str, dict[str, Any]] = {}
    try:
        for request_id, method, params in [
            ("init", "initialize", None),
            ("list", "tools/list", None),
            ("image", "tools/call", {"name": "fixed_inline_image", "arguments": {}}),
            ("denied", "tools/call", {"name": "not_exported", "arguments": {}}),
        ]:
            request: dict[str, Any] = {"jsonrpc": "2.0", "id": request_id, "method": method}
            if params is not None:
                request["params"] = params
            process.stdin.write((json.dumps(request) + "\n").encode())
            await asyncio.wait_for(process.stdin.drain(), timeout=2)
            line = await asyncio.wait_for(process.stdout.readline(), timeout=8)
            assert line, f"MCP CLI exited before request {request_id} response"
            response = json.loads(line)
            assert response.get("id") == request_id
            responses[request_id] = response
    finally:
        if not process.stdin.is_closing():
            process.stdin.close()
            try:
                await asyncio.wait_for(process.stdin.wait_closed(), timeout=2)
            except (BrokenPipeError, ConnectionResetError):
                pass
        try:
            await asyncio.wait_for(process.wait(), timeout=8)
        except TimeoutError as err:
            process.kill()
            await asyncio.wait_for(process.wait(), timeout=5)
            raise AssertionError("MCP CLI failed to close after stdin EOF") from err
    stderr = (await stderr_task).decode("utf-8", errors="replace")
    assert process.returncode == 0, stderr

    assert [tool["name"] for tool in responses["list"]["result"]["tools"]] == ["fixed_inline_image"]
    image = responses["image"]["result"]
    assert image == {
        "content": [
            {"type": "text", "text": "before"},
            {"type": "image", "data": _PNG_B64, "mimeType": "image/png"},
            {"type": "text", "text": "after"},
        ],
        "isError": False,
    }
    assert responses["denied"]["result"]["isError"] is True
    assert "not allowed or not found" in responses["denied"]["result"]["content"][0]["text"]

    sessions = list((workspace / ".harness" / "sessions").iterdir())
    assert len(sessions) == 1
    records = [json.loads(line) for line in (sessions[0] / "transcript.jsonl").read_text().splitlines()]
    calls = [call for record in records for call in record.get("tool_calls", [])]
    results = [result for record in records for result in record.get("tool_results", [])]
    assert len(calls) == len(results) == 1
    assert calls[0]["id"] == results[0]["tool_use_id"]
    assert calls[0]["name"] == "fixed_inline_image"
    assert results[0]["content"] == [
        {"type": "text", "text": "before"},
        {"type": "image", "media_type": "image/png", "data_b64": _PNG_B64},
        {"type": "text", "text": "after"},
    ]
