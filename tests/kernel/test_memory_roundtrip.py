"""
Kernel-level round-trip for the workspace memory substrate.

Regression for review/CODE_REVIEW_v0.2.1.md Addendum A.1: `remember`,
`search_memory`, the FTS5 store and the `memories` context source each used a
different directory (`.harness/tools/<tool>/`, `.harness/plugins/<plugin>/`,
`.harness/`), so a remembered fact could never be searched or injected. The
plugin unit tests did not catch it because they handed every component the same
mocked `data_dir`. This test only uses the real kernel wiring.
"""
from __future__ import annotations

from pathlib import Path

import pytest

from harness.kernel.bootstrap import bootstrap_kernel
from harness.sdk.paths import memory_db_path
from harness.sdk.testing.scripted import ScriptedProvider
from harness.sdk.types import (
    Done,
    ModelInfo,
    TextBlock,
    TextDelta,
    ToolCallEnd,
    ToolResultBlock,
    Usage,
    UsageEvent,
)

MODEL = ModelInfo(
    id="scripted-v1",
    display_name="Scripted",
    context_tokens=128000,
    input_usd_per_mtok=0.0,
    output_usd_per_mtok=0.0,
)


def _scripted() -> ScriptedProvider:
    return ScriptedProvider(models=[MODEL])


def _tool_result_texts(provider: ScriptedProvider) -> list[str]:
    """All tool_result texts the model was shown across every request."""
    out: list[str] = []
    for req in provider.requests:
        for m in req.messages:
            for b in m.content:
                if isinstance(b, ToolResultBlock):
                    for inner in b.content:
                        if isinstance(inner, TextBlock):
                            out.append(inner.text)
    return out


def _request_text(provider: ScriptedProvider, index: int) -> str:
    req = provider.requests[index]
    parts = [b.text for b in req.system]
    for m in req.messages:
        parts.extend(b.text for b in m.content if isinstance(b, TextBlock))
    return "\n".join(parts)


@pytest.mark.asyncio
async def test_remember_search_and_context_injection_share_one_database(tmp_path: Path):
    ws = tmp_path / "ws"
    ws.mkdir()
    conf = tmp_path / "conf"
    conf.mkdir()
    options = {"provider": "scripted", "model": MODEL.id}

    # ---- Process 1: the model remembers a fact --------------------------------
    rt1 = await bootstrap_kernel(workspace=ws, config_dir=conf)
    p1 = _scripted()
    p1.add_response([
        TextDelta(text="Noting that."),
        ToolCallEnd(id="c1", name="remember", arguments={"text": "Project codename is Falcon", "key": "codename"}),
        UsageEvent(usage=Usage(input_tokens=10, output_tokens=5)),
        Done(stop_reason="tool_use"),
    ])
    p1.add_response([TextDelta(text="Saved."), Done(stop_reason="end_turn")])
    rt1.registry.register_provider(p1, plugin_name="test_scripted")
    s1 = await rt1.session_manager.create_session()
    r1 = await rt1.runner.execute_turn(session=s1.info, prompt="Remember the codename is Falcon", options=options)
    assert "Saved." in r1.output
    assert any("remembered fact" in t.lower() for t in _tool_result_texts(p1)), _tool_result_texts(p1)
    await rt1.registry.unload_all()

    # Exactly one memory database, at the canonical shared path.
    dbs = sorted(p.relative_to(ws).as_posix() for p in ws.rglob("memory.db"))
    assert dbs == [".harness/memory.db"], dbs
    assert memory_db_path(ws).exists()

    # ---- Process 2 (fresh bootstrap = new daemon): search finds the fact ------
    rt2 = await bootstrap_kernel(workspace=ws, config_dir=conf)
    p2 = _scripted()
    p2.add_response([
        ToolCallEnd(id="c2", name="search_memory", arguments={"query": "Falcon"}),
        Done(stop_reason="tool_use"),
    ])
    p2.add_response([TextDelta(text="Found it."), Done(stop_reason="end_turn")])
    rt2.registry.register_provider(p2, plugin_name="test_scripted")
    s2 = await rt2.session_manager.create_session()
    r2 = await rt2.runner.execute_turn(session=s2.info, prompt="What is the codename?", options=options)
    assert "Found it." in r2.output

    results = [t for t in _tool_result_texts(p2) if "Falcon" in t]
    assert results, f"search_memory did not surface the remembered fact: {_tool_result_texts(p2)}"
    assert "fact #1" in results[0] and "codename" in results[0], results[0]
    # Transcript of process 1 is indexed in the same database too (FTS over turns).
    assert "step" in results[0]

    # ---- The `memories` context source injects the fact into a NEW session ----
    first_request = _request_text(p2, 0)
    assert "Remembered Facts" in first_request, first_request[:2000]
    assert "Project codename is Falcon" in first_request
    await rt2.registry.unload_all()
