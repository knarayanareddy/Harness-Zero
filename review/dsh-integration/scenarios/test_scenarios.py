"""Parametrised golden tests: one case per ``<scenario>/scenario.yaml``.

Run from the repository root::

    . .venv/bin/activate
    pytest review/dsh-integration/scenarios -q                 # replay (default)
    HARNESS_REPLAY=update pytest review/dsh-integration/scenarios -q -k name   # accept new goldens
    SCENARIO_DOM=0 pytest …                                    # skip the jsdom lane

A scenario with ``xfail: <reason>`` documents a known bug: the test is expected
to FAIL and turns red the day the bug is fixed (strict), so the golden can be
re-recorded and the ``xfail`` removed in the same PR.
"""
from __future__ import annotations

from pathlib import Path

import pytest
from _lib.kernel_runner import run_kernel_scenario
from _lib.scenario import compare_golden, discover, load_scenario
from _lib.web_runner import run_web_scenario

ROOT = Path(__file__).parent
SCENARIOS = discover(ROOT)


def _check_expectations(sc, result) -> None:
    exp = sc.expect
    if "tools_called" in exp:
        assert result.tools_called == exp["tools_called"], f"tools called: {result.tools_called} != {exp['tools_called']}"
    if "tools_called_subset" in exp:
        missing = [t for t in exp["tools_called_subset"] if t not in result.tools_called]
        assert not missing, f"tools never called: {missing} (called: {result.tools_called})"
    for rel, cond in (exp.get("files") or {}).items():
        p = result.workspace / rel
        if cond.get("exists", True):
            assert p.exists(), f"{rel} missing"
            if "contains" in cond:
                assert cond["contains"] in p.read_text(), f"{rel} does not contain {cond['contains']!r}"
            if "not_contains" in cond:
                assert cond["not_contains"] not in p.read_text(), f"{rel} still contains {cond['not_contains']!r}"
        else:
            assert not p.exists(), f"{rel} should not exist"
    if exp.get("no_errors", True):
        assert not result.errors, f"errors during run: {result.errors}"
    if "event_types_include" in exp:
        seen = {e.get("type") for e in result.events}
        missing = [t for t in exp["event_types_include"] if t not in seen]
        assert not missing, f"events never observed: {missing}; seen={sorted(seen)}"
    if "event_types_exclude" in exp:
        seen = {e.get("type") for e in result.events}
        present = [t for t in exp["event_types_exclude"] if t in seen]
        assert not present, f"events that must not occur: {present}"
    if exp.get("replay_consumed", True):
        assert result.extra.get("replay_remaining") == 0, f"{result.extra.get('replay_remaining')} recorded model responses were never requested"
    for needle in exp.get("model_saw") or []:
        blob = "\n".join(str(r) for r in result.provider_requests)
        assert needle in blob, f"model never saw {needle!r}"
    for needle in exp.get("model_never_saw") or []:
        blob = "\n".join(str(r) for r in result.provider_requests)
        assert needle not in blob, f"model saw forbidden text {needle!r}"
    if "stop_reasons" in exp:
        got = [tr.get("stop_reason") for tr in (getattr(result, "turn_results", None) or [])]
        assert got == exp["stop_reasons"], f"stop reasons {got} != {exp['stop_reasons']}"
    if exp.get("reload_equals_live", False):
        assert result.extra.get("live_vs_reloaded_equal") is True, "sessions.get after restart differs from the live history"
    if "timeline_contains" in exp:
        assert result.dom is not None, f"dom lane did not run: {result.errors}"
        text = result.dom.get("timeline_text") or ""
        for needle in exp["timeline_contains"]:
            assert needle in text, f"timeline does not show {needle!r}; timeline={text[:400]!r}; console={result.dom.get('console_tail')}"
    if "timeline_not_contains" in exp and result.dom is not None:
        text = result.dom.get("timeline_text") or ""
        for needle in exp["timeline_not_contains"]:
            assert needle not in text, f"timeline shows {needle!r}"
    if exp.get("dom_turns_completed") and result.dom is not None:
        incomplete = [t for t in result.dom.get("turns", []) if not t.get("completed")]
        assert not incomplete, f"client never saw the turn finish: {incomplete}; sse={result.dom.get('sse_events')[:20]}; submit={result.dom.get('submit_bodies')}; fetch={result.dom.get('fetch_log')[-6:]}"


@pytest.mark.parametrize("scenario_dir", SCENARIOS, ids=lambda p: p.name)
async def test_scenario(scenario_dir: Path, tmp_path: Path, hermetic_env: Path, request: pytest.FixtureRequest):
    sc = load_scenario(scenario_dir)
    if sc.xfail:
        request.node.add_marker(pytest.mark.xfail(reason=sc.xfail, strict=True))

    if sc.kind == "kernel":
        result = await run_kernel_scenario(sc, tmp_path, env_conf=hermetic_env)
    elif sc.kind == "web":
        result = await run_web_scenario(sc, tmp_path, env_conf=hermetic_env, dom=bool(sc.expect.get("dom_lane", True)))
    else:
        pytest.fail(f"unknown scenario kind {sc.kind!r}")

    norm = result.normalized()
    (sc.dir / "_last_run").mkdir(exist_ok=True)
    import json

    (sc.dir / "_last_run" / "summary.json").write_text(
        json.dumps({"errors": result.errors, "tools_called": result.tools_called, "extra": result.extra, "dom": result.dom}, indent=2, default=str)
    )

    _check_expectations(sc, result)

    golden = sc.golden_dir
    if sc.expect.get("transcript_golden", True):
        compare_golden(golden / "transcript.json", norm["transcript"])
    if sc.expect.get("events_golden", True):
        compare_golden(golden / "events.json", [{"type": e["type"], "data": e.get("data", {})} for e in norm["events"]])
    if sc.expect.get("requests_golden", True):
        compare_golden(golden / "requests.json", norm["requests"])
    if sc.kind == "web" and sc.expect.get("http_golden", True):
        compare_golden(golden / "http.json", norm["http"])
    if sc.kind == "web" and result.dom is not None and sc.expect.get("timeline_golden", True):
        compare_golden(golden / "timeline.txt", norm.get("timeline", ""))
