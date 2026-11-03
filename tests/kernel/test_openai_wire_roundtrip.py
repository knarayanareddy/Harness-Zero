"""T12: actual Commands/gate/store/OpenAI adapter against a strict local peer."""
from __future__ import annotations

import asyncio
import copy
import json
from types import SimpleNamespace

import httpx
import pytest
from aiohttp import web

from harness.kernel.bootstrap import bootstrap_kernel
from harness.kernel.context import derive_messages
from harness.kernel.transcript import TranscriptLog
from harness.plugins.providers.openai_compat.provider import OpenAICompatProvider
from harness.plugins.stores.jsonl_transcript.store import JsonlTranscriptStore
from harness.sdk.types import ModelParams, ModelRequest, RiskClass, ToolResult, ToolSpec


def continuation_error(messages):
    """Independent peer invariant: every assistant call needs exactly one adjacent result."""
    pending = set()
    for message in messages:
        role = message.get("role")
        if pending and role != "tool":
            return "missing tool results before the next non-tool message"
        if role == "assistant":
            calls = message.get("tool_calls", [])
            ids = [call["id"] for call in calls]
            if len(set(ids)) != len(ids):
                return "duplicate assistant call IDs"
            pending.update(ids)
        elif role == "tool":
            call_id = message.get("tool_call_id")
            if call_id not in pending:
                return "unknown or duplicate tool result ID"
            if not isinstance(message.get("content"), str):
                return "text tool results require string content"
            pending.remove(call_id)
    return "missing tool results at end of request" if pending else None


@pytest.fixture
async def wire_peer():
    peer = SimpleNamespace(requests=[])

    async def models(request):
        return web.json_response({"data": [{"id": "wire-model"}]})

    async def completion(request):
        payload = await request.json()
        peer.requests.append(payload)
        error = continuation_error(payload["messages"])
        if error:
            return web.json_response({"error": {"message": error}}, status=400)
        if len(peer.requests) == 1:
            calls = [{"index": index, "id": cid, "type": "function",
                      "function": {"name": name, "arguments": "{}"}}
                     for index, (cid, name) in enumerate((("good", "fixture_read"), ("bad", "fixture_error")))]
            choice = {"index": 0, "delta": {"tool_calls": calls}, "finish_reason": "tool_calls"}
        else:
            choice = {"index": 0, "delta": {"content": "all results received"}, "finish_reason": "stop"}
        body = "data: " + json.dumps({"choices": [choice]}) + "\n\ndata: [DONE]\n\n"
        return web.Response(text=body, content_type="text/event-stream")

    app = web.Application()
    app.router.add_get("/v1/models", models)
    app.router.add_post("/v1/chat/completions", completion)
    runner = web.AppRunner(app)
    await runner.setup()
    site = web.TCPSite(runner, "127.0.0.1", 0)
    await site.start()
    peer.url = f"http://127.0.0.1:{runner.addresses[0][1]}/v1"
    try:
        yield peer
    finally:
        await runner.cleanup()


async def test_commands_preserve_multiple_results_errors_and_reloaded_wire(tmp_path, wire_peer):
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    rt = await bootstrap_kernel(workspace=workspace, config_dir=tmp_path / "config",
        overrides={"kernel.provider": "wire", "kernel.model": "wire-model",
                   "kernel.security.permission_mode": "allow"}, resume_session=False)
    client = httpx.AsyncClient(trust_env=False)
    provider = OpenAICompatProvider(name="wire", base_url=wire_peer.url, client=client)
    rt.registry.register_provider(provider, plugin_name="test_wire")

    async def read(args, ctx):
        return ToolResult.text("synthetic file content")

    async def fail(args, ctx):
        return ToolResult.error("synthetic handler failure")

    for name, handler in (("fixture_read", read), ("fixture_error", fail)):
        rt.registry._register_tool(ToolSpec(name=name, description="Local test fixture",
            input_schema={"type": "object", "properties": {}, "additionalProperties": False},
            risk_class=RiskClass.READ), handler, "test_wire")
    try:
        session = await rt.session_manager.create_session()
        result = await asyncio.wait_for(rt.commands.submit_turn(session_id=session.session_id,
            prompt="Read both fixtures", options={"provider": "wire", "model": "wire-model"}), 30)
        assert result.error is None, result.error
        assert result.output == "all results received" and result.tool_calls == 2
        assert len(wire_peer.requests) == 2
        second = wire_peer.requests[1]
        results = [m for m in second["messages"] if m["role"] == "tool"]
        assert results == [
            {"role": "tool", "tool_call_id": "good", "content": "synthetic file content"},
            {"role": "tool", "tool_call_id": "bad", "content": "[Tool error]\nsynthetic handler failure"},
        ]
        restarted = TranscriptLog(JsonlTranscriptStore(session.log.session_dir))
        records = await restarted.load()
        final_assistant = max(i for i, row in enumerate(records) if row["kind"] == "assistant.message")
        request = ModelRequest(model="wire-model", system=[], tools=[], params=ModelParams(),
            messages=derive_messages(records[:final_assistant]))
        rebuilt = provider._convert_messages(request)
        assert rebuilt == [m for m in second["messages"] if m["role"] != "system"]
        assert continuation_error(rebuilt) is None
        replay = await client.post(wire_peer.url + "/chat/completions", json={**second, "messages": rebuilt})
        assert replay.status_code == 200
        broken = copy.deepcopy(second)
        broken["messages"] = [m for m in broken["messages"] if m.get("tool_call_id") != "bad"]
        rejected = await client.post(wire_peer.url + "/chat/completions", json=broken)
        assert rejected.status_code == 400
        assert "missing tool results" in rejected.json()["error"]["message"]
    finally:
        await client.aclose()
        await rt.registry.unload_all()


@pytest.mark.asyncio
@pytest.mark.parametrize("completed_tool", [False, True])
async def test_partial_stream_timeout_closes_response_without_tools_or_automatic_replay(tmp_path, completed_tool):
    requests, responses, handler_starts = [], [], []
    release_stall = asyncio.Event()

    async def completion(request):
        requests.append(await request.json())
        if completed_tool and len(requests) == 1:
            choice = {"delta": {"tool_calls": [{"index": 0, "id": "completed", "function": {
                "name": "fixture_forbidden", "arguments": "{}"}}]}, "finish_reason": "tool_calls"}
            return web.Response(text="data: " + json.dumps({"choices": [choice]}) + "\n\ndata: [DONE]\n\n",
                                content_type="text/event-stream")
        if len(requests) != (2 if completed_tool else 1):
            choice = {"delta": {"content": "RECOVERED"}, "finish_reason": "stop"}
            return web.Response(text="data: " + json.dumps({"choices": [choice]}) + "\n\ndata: [DONE]\n\n",
                                content_type="text/event-stream")
        response = web.StreamResponse(headers={"Content-Type": "text/event-stream"})
        await response.prepare(request)
        chunk = {"choices": [{"delta": {"content": "unfinished output", "tool_calls": [
            {"index": 0, "id": "uncertain", "function": {"name": "fixture_forbidden", "arguments": "{"}},
        ]}}]}
        await response.write(("data: " + json.dumps(chunk) + "\n\n").encode())
        await release_stall.wait()
        return response

    async def models(request):
        return web.json_response({"data": [{"id": "wire-model"}]})

    async def observe_response(response):
        if response.request.method == "POST":
            responses.append(response)

    app = web.Application()
    app.router.add_post("/v1/chat/completions", completion)
    app.router.add_get("/v1/models", models)
    server = web.AppRunner(app)
    await server.setup()
    site = web.TCPSite(server, "127.0.0.1", 0)
    await site.start()
    client = httpx.AsyncClient(trust_env=False, timeout=httpx.Timeout(0.05), event_hooks={"response": [observe_response]})
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    rt = await bootstrap_kernel(workspace=workspace, config_dir=tmp_path / "config", resume_session=False)

    async def forbidden(args, ctx):
        handler_starts.append(True)
        return ToolResult.text("must not execute")

    rt.registry.register_provider(OpenAICompatProvider(name="wire", base_url=f"http://127.0.0.1:{server.addresses[0][1]}/v1",
        timeout_s=0.05, client=client), plugin_name="test_wire")
    rt.registry._register_tool(ToolSpec(name="fixture_forbidden", description="Must not execute a partial call",
        input_schema={"type": "object"}, risk_class=RiskClass.READ), forbidden, "test_wire")
    sub = rt.bus.subscribe()
    try:
        session = await rt.session_manager.create_session()
        options = {"provider": "wire", "model": "wire-model"}
        failed = await asyncio.wait_for(rt.commands.submit_turn(session_id=session.session_id, prompt="first", options=options), 5)
        assert failed.error and "timed out" in failed.error and failed.stop_reason == "error"
        assert len(requests) == (2 if completed_tool else 1), "A timeout must not trigger an unrequested model replay"
        assert all(response.is_closed for response in responses)
        assert len(handler_starts) == int(completed_tool), "Only the earlier completed call may execute"
        assert not session.is_busy and await rt.commands.interactions_list() == []
        assert not [r for r in session.log.records if r["kind"] == "turn.completed"]
        if not completed_tool:
            assert not [r for r in session.log.records if r["kind"] in {"assistant.message", "tool.call"}]
        errors = []
        while not sub.empty():
            event = sub.get_nowait()
            if event.type == "turn.error":
                errors.append(event)
        assert len(errors) == 1 and errors[0].data["kind"] == "timeout"
        assert errors[0].data["retryable"] is (not completed_tool)

        recovered = await asyncio.wait_for(rt.commands.submit_turn(session_id=session.session_id, prompt="recover", options=options), 5)
        assert recovered.error is None and recovered.output == "RECOVERED"
        assert len(requests) == (3 if completed_tool else 2) and len(handler_starts) == int(completed_tool)
        assert [r["content"] for r in session.log.records if r["kind"] == "user.message"] == ["first", "recover"]
        assert continuation_error(requests[-1]["messages"]) is None
    finally:
        release_stall.set()
        rt.bus.unsubscribe(sub)
        await rt.registry.unload_all()
        await client.aclose()
        await server.cleanup()
