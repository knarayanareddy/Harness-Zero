"""Explicitly selected, intentionally red assertions for review findings R1–R19."""
from __future__ import annotations

import asyncio
import json
import os
import runpy
import shutil
import subprocess
import sys
import zipfile
from pathlib import Path
from types import SimpleNamespace

import httpx
import pytest
import pytest_asyncio

from harness.kernel.bootstrap import bootstrap_kernel
from harness.plugins.bridges.mcp_client.client import MCPClient
from harness.plugins.bridges.mcp_client.tools import create_mcp_tool_handler
from harness.plugins.providers.anthropic.provider import AnthropicProvider
from harness.plugins.providers.gemini.provider import GeminiProvider
from harness.plugins.providers.ollama.provider import OllamaProvider
from harness.plugins.providers.ollama.xml_protocol import XmlStreamParser
from harness.plugins.providers.openai_compat.provider import OpenAICompatProvider
from harness.plugins.stores.jsonl_transcript.store import JsonlTranscriptStore
from harness.sdk.errors import ProviderError
from harness.sdk.sandbox_seatbelt import SeatbeltBackend
from harness.sdk.testing.scripted import ScriptedProvider
from harness.sdk.types import (
    Done,
    ExecRequest,
    ImageBlock,
    Message,
    ModelInfo,
    ModelParams,
    ModelRequest,
    Role,
    SandboxAvailability,
    SandboxPolicy,
    TextBlock,
    TextDelta,
    ToolCall,
    ToolCallEnd,
    TranscriptRecord,
)

ROOT = Path(__file__).resolve().parents[2]


@pytest.fixture(autouse=True)
def isolate(tmp_path, monkeypatch):
    home = tmp_path / "home"
    home.mkdir()
    monkeypatch.setenv("HOME", str(home))
    monkeypatch.setenv("HARNESS_CONFIG_DIR", str(home / ".harness"))
    for key in tuple(os.environ):
        if any(s in key.upper() for s in ("API_KEY", "TOKEN", "PASSWORD", "SECRET")):
            monkeypatch.delenv(key, raising=False)


@pytest_asyncio.fixture
async def runtime(tmp_path, monkeypatch):
    ws = tmp_path / "workspace"
    ws.mkdir()
    (ws / "hello.txt").write_text("synthetic workspace file")
    rt = await bootstrap_kernel(workspace=ws, config_dir=tmp_path / "config", resume_session=False)
    # CLI subprocesses must read the SAME persisted configuration as this kernel.
    monkeypatch.setenv("HARNESS_CONFIG_DIR", str(rt.settings.path.parent))
    try:
        yield rt
    finally:
        await rt.registry.unload_all()


def scripted(rt, responses):
    provider = ScriptedProvider(responses=responses, models=[ModelInfo(id="review", display_name="Review")])
    rt.registry.register_provider(provider, plugin_name="review")
    return provider


def request():
    return ModelRequest(model="review", system=[], tools=[], params=ModelParams(),
                        messages=[Message(role=Role.USER, content=[TextBlock("hello")])])


async def cli(kind, workspace, messages):
    proc = await asyncio.create_subprocess_exec(
        sys.executable, "-m", "harness.cli", kind, "--workspace", str(workspace),
        stdin=asyncio.subprocess.PIPE, stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.PIPE,
    )
    try:
        wire = "".join(json.dumps(m) + "\n" for m in messages).encode()
        stdout, stderr = await asyncio.wait_for(proc.communicate(wire), 15)
        assert proc.returncode == 0, stderr.decode()
        return [json.loads(line) for line in stdout.decode().splitlines() if line.strip()]
    finally:
        if proc.returncode is None:
            proc.kill()
            await proc.wait()


async def test_R01_locked_search_cannot_read_outside_symlink(runtime, tmp_path):
    outside = tmp_path / "outside.txt"
    private_line = "OUTSIDE_REVIEW_CANARY synthetic-private-payload"
    outside.write_text(private_line)
    (runtime.registry.workspace / "linked.txt").symlink_to(outside)
    runtime.settings.set_override("kernel.security.preset", "locked")
    result = await runtime.gate.execute(ToolCall(id="search", name="search_codebase", arguments={"query": "OUTSIDE_REVIEW_CANARY"}))
    # No-match messages legitimately echo the query; distinguish that from data.
    assert not result.is_error, result.output
    assert private_line not in result.output, result.output
    assert result.metadata["count"] == 0, result.output


async def test_R02_real_tool_result_reaches_openai_wire(runtime):
    p = scripted(runtime, [[ToolCallEnd(id="read", name="view_file", arguments={"file_path": "hello.txt"}), Done(stop_reason="tool_use")],
                           [TextDelta(text="done"), Done(stop_reason="end_turn")]])
    session = await runtime.session_manager.create_session()
    await runtime.commands.submit_turn(session_id=session.session_id, prompt="Read hello.txt", options={"provider": "scripted", "model": "review"})
    assert len(p.requests) == 2, "setup: expected real tool continuation"
    adapter = OpenAICompatProvider(name="review", api_key="synthetic")
    try:
        wire = adapter._convert_messages(p.requests[1])
        assert any(m.get("role") == "tool" and m.get("tool_call_id") == "read" and "synthetic workspace file" in m["content"] for m in wire), wire
    finally:
        await adapter.aclose()


async def test_R03_cancel_stops_delayed_write(runtime):
    runtime.settings.set_override("kernel.security.preset", "open")
    scripted(runtime, [[ToolCallEnd(id="shell", name="run_command", arguments={"command": "printf 'started\\n'; sleep 0.5; printf wrote > after-stop.txt"}), Done(stop_reason="tool_use")],
                       [Done(stop_reason="end_turn")]])
    session = await runtime.session_manager.create_session()
    sub = runtime.bus.subscribe()
    task = asyncio.create_task(runtime.commands.submit_turn(session_id=session.session_id, prompt="Run the fixture", options={"provider": "scripted", "model": "review"}))
    try:
        async with asyncio.timeout(5):
            while True:
                event = await sub.get()
                if event.type == "tool.output.delta" and "started" in event.data.get("text", ""):
                    break
        assert await runtime.commands.cancel_turn(session.session_id)
        result = await asyncio.wait_for(task, 5)
        assert result.stop_reason == "cancelled", result
        assert not (runtime.registry.workspace / "after-stop.txt").exists(), "Delayed write completed after Stop"
    finally:
        if not task.done():
            task.cancel()
        await asyncio.gather(task, return_exceptions=True)
        runtime.bus.unsubscribe(sub.id)


async def test_R04_actual_mcp_cli_executes_allowed_read(runtime):
    runtime.settings.set("plugins.mcp-server.expose", ["view_file"])
    responses = await cli("mcp", runtime.registry.workspace, [{"jsonrpc": "2.0", "id": 1, "method": "tools/call", "params": {"name": "view_file", "arguments": {"file_path": "hello.txt"}}}])
    assert not responses[0]["result"]["isError"], responses


async def test_R05_actual_acp_cli_creates_session(runtime):
    responses = await cli("acp", runtime.registry.workspace, [{"jsonrpc": "2.0", "id": 1, "method": "session/new"}])
    assert "result" in responses[0], responses


def test_R06_supported_image_builders_use_sdk_fields():
    req = request()
    req.messages = [Message(role=Role.USER, content=[ImageBlock(media_type="image/png", data_b64="c3ludGhldGlj")])]
    errors = {}
    for cls, method, arg in [(AnthropicProvider, "_build_messages", req.messages), (GeminiProvider, "_prepare_payload", req), (OllamaProvider, "_prepare_payload", req)]:
        try:
            getattr(cls.__new__(cls), method)(arg)
        except AttributeError as exc:
            errors[cls.__name__] = str(exc)
    assert not errors, errors


def test_R07_shipped_ui_rejects_other_session_and_stale_history():
    result = subprocess.run(["node", str(Path(__file__).with_name("ui_probe.mjs"))], cwd=ROOT, capture_output=True, text=True, timeout=15, check=True)
    observed = json.loads(result.stdout)
    assert observed == {"foreign_visible": False, "foreign_busy": False, "stale_visible": False}, observed


def test_R08_seatbelt_preserves_nested_cwd(tmp_path):
    ws = tmp_path.resolve()
    nested = ws / "nested"
    nested.mkdir()
    backend = SeatbeltBackend()
    backend._availability = SandboxAvailability(backend="seatbelt", available=True, enforcement="partial")
    backend._executable = "/usr/bin/sandbox-exec"
    wrapped = backend.wrap(ExecRequest(command=["/bin/pwd"], cwd=nested), SandboxPolicy(workspace=ws))
    assert wrapped.cwd == nested, wrapped.cwd


async def test_R09_anthropic_sse_error_is_not_success():
    body = b'data: {"type":"error","error":{"type":"overloaded_error","message":"synthetic"}}\n\n'
    async with httpx.AsyncClient(transport=httpx.MockTransport(lambda req: httpx.Response(200, content=body, headers={"Content-Type": "text/event-stream"}))) as client:
        p = AnthropicProvider(api_key="synthetic", client=client)
        with pytest.raises(ProviderError):
            events = [event async for event in p.generate(request())]
            assert not any(isinstance(e, Done) for e in events), events


def test_R10_xml_tool_calls_survive_split_closing_tag():
    text = '<tool_call>{"name":"view_file","arguments":{"file_path":"hello.txt"}}</tool_call>'
    whole = XmlStreamParser()
    expected = whole.feed(text) + whole.flush()
    assert len(expected) == 1, "setup: whole-tag control failed"
    split = XmlStreamParser()
    observed = split.feed(text[:-5]) + split.feed(text[-5:]) + split.flush()
    assert observed == expected, observed


async def test_R11_initial_mcp_recovery_publishes_tools(tmp_path):
    late = tmp_path / "late.py"
    ready = asyncio.Event()
    changes = []

    def status(value):
        if value["state"] == "connected":
            ready.set()

    client = MCPClient(name="late", command=sys.executable, args=[str(late)], connect_timeout_s=1,
                       reconnect_backoff_s=.1, reconnect_max_backoff_s=.1, reconnect_max_attempts=10,
                       on_status=status, on_tools_changed=lambda *args: changes.append(args))
    try:
        await client.start()
        assert client.status == "reconnecting", "setup: missing peer must fail initially"
        shutil.copyfile(ROOT / "tests/fixtures/mcp_echo_server.py", late)
        await asyncio.wait_for(ready.wait(), 10)
        assert client.tools, "setup: peer must discover tools"
        assert len(changes) == 1, f"connected with {len(client.tools)} tools but {len(changes)} callbacks"
    finally:
        await client.stop()


async def test_R12_mcp_image_result_is_not_replaced_by_success():
    class Peer:
        async def call_tool(self, *args, **kwargs):
            return {"content": [{"type": "image", "mimeType": "image/png", "data": "c3ludGhldGlj"}]}
    result = await create_mcp_tool_handler(Peer(), "screen")({}, object())
    assert any(isinstance(b, ImageBlock) for b in result.content), result


async def test_R13_canonical_mcp_exports_are_enforced(runtime):
    runtime.settings.set("plugins.bridge-mcp-server.expose", ["view_file"])
    assert runtime.settings.get("plugins.bridge-mcp-server.expose") == ["view_file"], "setup: canonical allow-list was not saved"
    responses = await cli("mcp", runtime.registry.workspace, [{"jsonrpc": "2.0", "id": 1, "method": "tools/list"}])
    names = {t["name"] for t in responses[0]["result"]["tools"]}
    assert names == {"view_file"}, names


async def test_R14_gemini_network_error_keeps_sdk_classification():
    def timeout(req):
        raise httpx.ConnectTimeout("synthetic timeout")
    async with httpx.AsyncClient(transport=httpx.MockTransport(timeout)) as client:
        p = GeminiProvider(api_key="synthetic", client=client)
        with pytest.raises(ProviderError) as error:
            _ = [event async for event in p.generate(request())]
        assert error.value.kind == "timeout"


async def test_R15_real_threshold_crossing_compacts(runtime):
    runtime.settings.set_override("kernel.context.budget_tokens", 1000)
    p = scripted(runtime, [[TextDelta(text="## Goal\nRetain goal\n## Decisions\nNone\n## State of work\nReady\n## Open items\nNone"), Done(stop_reason="end_turn")],
                           [TextDelta(text="done"), Done(stop_reason="end_turn")]])
    session = await runtime.session_manager.create_session()
    result = await runtime.commands.submit_turn(session_id=session.session_id, prompt="word " * 6000, options={"provider": "scripted", "model": "review"})
    assert result.stop_reason == "end_turn", (result.error, len(p.requests))
    assert any(r["kind"] == "compaction.summary" for r in session.log.records)


async def test_R16_unterminated_tail_cannot_swallow_acknowledged_append(tmp_path):
    store = JsonlTranscriptStore(tmp_path)
    await store.append({"role": "user", "content": "before"})
    for path in (store.full_path, store.compact_path):
        with path.open("a") as f:
            f.write('{"partial":')
    restarted = JsonlTranscriptStore(tmp_path)
    acknowledged = await restarted.append({"role": "user", "content": "after-resume"})
    records = await restarted.read(compact=False)
    assert any(r.get("content") == "after-resume" and r["step_index"] == acknowledged for r in records), records


async def test_R17_all_dangling_calls_repair_durably(runtime):
    session = await runtime.session_manager.create_session()
    await session.log.append(TranscriptRecord(kind="assistant.message", role="assistant", tool_calls=[
        {"id": "c1", "name": "view_file", "arguments": {"file_path": "hello.txt"}},
        {"id": "c2", "name": "view_file", "arguments": {"file_path": "hello.txt"}},
    ]))
    repaired = await session.log.repair_dangling_tool_calls()
    assert len(repaired) == 2
    assert not session.log.dangling_tool_calls()
    assert await session.log.repair_dangling_tool_calls() == []


def test_R18_frozen_data_recipe_includes_core_ui_manifest():
    captured = {}

    def analysis(*args, **kwargs):
        captured.update(kwargs)
        return SimpleNamespace(pure=[], scripts=[], binaries=[], datas=kwargs["datas"])

    runpy.run_path(str(ROOT / "packaging/macos/harness-engine.spec"), init_globals={
        "SPECPATH": str(ROOT / "packaging/macos"), "Analysis": analysis,
        "PYZ": lambda *args: None, "EXE": lambda *args, **kwargs: None,
    })
    assert any(Path(source) == ROOT / "harness/frontends/web/ui.toml" and destination == "harness/frontends/web" for source, destination in captured["datas"]), captured["datas"]


def test_R19_built_wheel_contains_all_builtin_skills(tmp_path):
    subprocess.run([sys.executable, "-m", "build", "--wheel", "--no-isolation", "--outdir", str(tmp_path)], cwd=ROOT, capture_output=True, text=True, timeout=60, check=True)
    wheel = next(tmp_path.glob("*.whl"))
    with zipfile.ZipFile(wheel) as archive:
        skills = [name for name in archive.namelist() if name.endswith("/SKILL.md")]
    assert len(skills) == 4, skills
