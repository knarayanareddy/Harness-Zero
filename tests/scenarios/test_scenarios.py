"""Parametrised golden tests: one case per ``<scenario>/scenario.yaml``.

Run from the repository root::

    . .venv/bin/activate
    pytest tests/scenarios -q                     # replay (default; plain `pytest -q` includes them)
    HARNESS_REPLAY=update pytest tests/scenarios -q -k name       # accept new goldens
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

from harness.sdk.events import validate_event

ROOT = Path(__file__).parent
SCENARIOS = discover(ROOT)


def _subset(want, have) -> bool:
    if isinstance(want, dict):
        return isinstance(have, dict) and all(k in have and _subset(v, have[k]) for k, v in want.items())
    if isinstance(want, list):
        return isinstance(have, list) and all(any(_subset(w, h) for h in have) for w in want)
    return want == have


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
    # Flow-of-information contract: every event that reached the observer (bus subscriber in the kernel
    # lane, SSE frames in the web lane) must satisfy its payload dataclass in harness/sdk/events.py.
    violations = []
    for e in result.events:
        if e.get("type") == "heartbeat":
            continue
        errs = validate_event({k: v for k, v in e.items() if not k.startswith("_")})
        if errs:
            violations.append(f"{e.get('type')}: {errs}")
    assert not violations, "event payload contract violations:\n" + "\n".join(violations[:20])
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
    if "events_match" in exp:
        # Each entry is a JSON subset that at least one event must match, e.g. {type: tool.decision, data: {action: deny}}.
        for want in exp["events_match"]:
            assert any(_subset(want, e) for e in result.events), f"no event matches {want}; seen={[e.get('type') for e in result.events]}"
    if sc.kind == "web" and exp.get("auth_required", True):
        statuses = result.extra.get("unauthenticated") or {}
        leaking = {k: v for k, v in statuses.items() if v not in (401, 403)}
        assert statuses and not leaking, f"unauthenticated requests not rejected: {statuses}"
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
    if "timeline_order" in exp:
        assert result.dom is not None, f"dom lane did not run: {result.errors}"
        text = result.dom.get("timeline_text") or ""
        positions = [(needle, text.find(needle)) for needle in exp["timeline_order"]]
        missing = [n for n, i in positions if i < 0]
        assert not missing, f"timeline lacks {missing}; timeline={text[:400]!r}"
        idx = [i for _, i in positions]
        assert idx == sorted(idx), f"timeline order is {[n for n, _ in sorted(positions, key=lambda x: x[1])]}, expected {exp['timeline_order']}"
    if "settings_groups_include" in exp:
        assert result.dom is not None, f"dom lane did not run: {result.errors}"
        groups = result.dom.get("settings_groups") or []
        missing = [g for g in exp["settings_groups_include"] if g not in groups]
        assert not missing, f"settings panel lacks group tabs {missing}; has {groups}; console={result.dom.get('console_tail')}"
        assert result.dom.get("settings_modal_hidden") is False, "settings modal did not open"
    if exp.get("no_console_errors", True) and result.dom is not None and "console_errors" in result.dom:
        assert result.dom["console_errors"] == [], f"browser console errors: {result.dom['console_errors']}"
    if "timeline_not_contains" in exp and result.dom is not None:
        text = result.dom.get("timeline_text") or ""
        for needle in exp["timeline_not_contains"]:
            assert needle not in text, f"timeline shows {needle!r}"
    if "reload_timeline_contains" in exp:
        rep = (result.dom or {}).get("reload") or {}
        text = rep.get("timeline_text_on_load") or ""
        for needle in exp["reload_timeline_contains"]:
            assert needle in text, (
                f"after a page refresh the timeline does not show {needle!r}; on_load={text[:300]!r}; "
                f"after_click={rep.get('timeline_text_after_click', '')[:300]!r}; "
                f"after_reloadTranscript={rep.get('timeline_text_after_reload_transcript', '')[:300]!r}; "
                f"active_session_on_load={rep.get('active_session_on_load')}; console={rep.get('console_tail')}"
            )
        assert not rep.get("empty_user_bubbles"), f"page refresh rendered {rep.get('empty_user_bubbles')} empty user bubbles (tool-result records)"
    if "reload_timeline_not_contains" in exp:
        rep = (result.dom or {}).get("reload") or {}
        text = rep.get("timeline_text_on_load") or ""
        for needle in exp["reload_timeline_not_contains"]:
            assert needle not in text, f"after a page refresh the timeline shows {needle!r}"
    if exp.get("dom_reload_contains_live_lines"):
        # Live-only chrome is allowed to differ; facts are not.
        live_only = ("streaming", "Assistant turn completed", "idle", "·", "plugin:")
        missing = [line for line in ((result.dom or {}).get("reload_missing_lines") or []) if not any(t in line for t in live_only)]
        assert not missing, "a page refresh lost what the live session painted:\n" + "\n".join(missing[:12])
    if exp.get("dom_restart"):
        rep = (result.dom or {}).get("restart") or {}
        info = result.extra.get("restart") or {}
        assert rep, f"restart lane did not run: {result.errors}"
        text = rep.get("timeline_text_on_load") or ""
        for needle in exp.get("restart_timeline_contains", []):
            assert needle in text, (
                f"after a daemon restart the timeline does not show {needle!r} without a click; on_load={text[:300]!r}; "
                f"active_session_on_load={rep.get('active_session_on_load')}; health.active_session={info.get('active_session_after_restart')}; "
                f"rows={info.get('session_rows')}; console={rep.get('console_tail')}"
            )
        if "sessions_after_restart" in exp:
            assert info.get("sessions_after_restart") == exp["sessions_after_restart"], (
                f"{info.get('sessions_after_restart')} sessions after restart, expected {exp['sessions_after_restart']}: {info.get('session_rows')}"
            )
        assert rep.get("active_session_on_load") == result.session_id, "the client did not land on the resumed session"
        assert not rep.get("empty_user_bubbles")
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
        # WS-04b: a durable event's record pointer is part of the observable contract, so the golden keeps
        # `record` when the kernel attached one (it is the stored record: `kind`, `step_index`, content).
        # Transient and kernel-only events omit it rather than carrying a null, so the golden says which
        # is which without restating the schema.
        rows = []
        for e in norm["events"]:
            row = {"type": e["type"], "data": e.get("data", {})}
            if e.get("record") is not None:
                row["record"] = e["record"]
            rows.append(row)
        compare_golden(golden / "events.json", rows)
    if sc.expect.get("requests_golden", True):
        compare_golden(golden / "requests.json", norm["requests"])
    if sc.kind == "web" and sc.expect.get("http_golden", True):
        compare_golden(golden / "http.json", norm["http"])
    if sc.kind == "web" and result.dom is not None and sc.expect.get("timeline_golden", True):
        compare_golden(golden / "timeline.txt", norm.get("timeline", ""))
