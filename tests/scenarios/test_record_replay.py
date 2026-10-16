"""The record path itself (WS-01): ``HARNESS_REPLAY=record`` writes a scenario's fixture from a live provider.

Recording is what makes this suite evidence rather than hand-written event lists: ``provider.jsonl`` comes
from a real model stream. CI has no key and no network, so the path is tested against the built-in ``mock``
provider — a real registered plugin with a real stream, the same code path minus the network. What matters is
the handoff: whatever record mode writes, replay mode must read back and reproduce exactly, in both lanes.

The failure modes this guards are the ones that make a fixture suite rot quietly:

* a recorder registered under a name the *client* does not ask for (the model chip sends the provider of the
  selected model), so a run records nothing and looks entirely normal;
* a recording that replays under different names than it was made, so its own goldens never match again;
* a recorder flushed after the kernel was unloaded, i.e. a fixture that exists only if the process wins a race.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest
from _lib import record
from _lib.kernel_runner import run_kernel_scenario
from _lib.scenario import Scenario, compare_golden, load_scenario
from _lib.web_runner import dom_lane_available, run_web_scenario

pytestmark = [pytest.mark.scenario]

SCENARIO_YAML = """name: recorded_from_a_live_provider
kind: {kind}
summary: >-
  Written by the record-path test: the fixture and the goldens came out of one boot of the real kernel, so
  replaying them exercises exactly the handoff a contributor performs with `HARNESS_REPLAY=record`.
turns:
  - prompt: "Say hello."
expect:
  no_errors: true
{extra}
"""


def _scenario_dir(tmp_path: Path, *, kind: str, extra: str = "") -> Path:
    d = tmp_path / f"scenario-{kind}"
    d.mkdir(parents=True)
    (d / "scenario.yaml").write_text(SCENARIO_YAML.format(kind=kind, extra=extra), encoding="utf-8")
    return d


def _write_or_check_goldens(scenario_dir: Path, result, *, timeline: bool = False) -> None:
    """In record mode this writes the goldens; in replay mode it *is* the assertion.

    The runners deliberately do not compare anything — ``test_scenarios.py`` owns that — so a test of the
    record path has to play that role itself. Same helper in both phases is what makes the comparison mean
    something: identical call, identical normalisation, only the mode differs.
    """
    norm = result.normalized()
    for name in ("transcript", "events", "requests"):
        if name in norm:
            compare_golden(scenario_dir / "golden" / f"{name}.json", norm[name])
    if timeline and norm.get("timeline"):
        compare_golden(scenario_dir / "golden" / "timeline.txt", norm["timeline"])


@pytest.fixture
def recording(monkeypatch: pytest.MonkeyPatch):
    """Put the suite in record mode against the built-in mock provider, and hand back a switch."""
    from _lib import scenario as scenario_mod

    def _set(*, mode: str = "record") -> None:
        monkeypatch.setenv("HARNESS_REPLAY", mode)
        monkeypatch.setenv("HARNESS_RECORD_PROVIDER", "mock")
        monkeypatch.setenv("HARNESS_RECORD_MODEL", "mock-v1")
        monkeypatch.setattr(scenario_mod, "REPLAY_MODE", mode, raising=True)

    _set()
    yield _set


@pytest.mark.asyncio
async def test_record_then_replay_in_the_kernel_lane(tmp_path: Path, hermetic_env: Path, recording):
    d = _scenario_dir(tmp_path, kind="kernel")
    assert not (d / "provider.jsonl").exists(), "a recording must not pre-exist for this test"

    recorded = await run_kernel_scenario(load_scenario(d), tmp_path / "run1", env_conf=hermetic_env)
    assert not recorded.errors, f"errors during the recording run: {recorded.errors}"

    # The fixture is a stream, not a summary of one.
    lines = [line for line in (d / "provider.jsonl").read_text(encoding="utf-8").splitlines() if line.strip()]
    assert len(lines) >= 2, lines
    assert "[mock] Say hello." in "\n".join(lines), lines
    sidecar = json.loads((d / "provider.requests.json").read_text(encoding="utf-8"))
    assert sidecar["provider"] == "mock" and sidecar["model"] == "mock-v1", sidecar
    assert sidecar["requests"], "the recorder must also save what the model was asked"

    # The goldens came out of the same run, so they describe a real flow rather than an expectation.
    _write_or_check_goldens(d, recorded)
    for name in ("transcript.json", "events.json", "requests.json"):
        assert json.loads((d / "golden" / name).read_text(encoding="utf-8")), name

    # Replay what was just written.
    recording(mode="replay")
    again = await run_kernel_scenario(load_scenario(d), tmp_path / "run2", env_conf=hermetic_env)
    assert not again.errors, f"errors during the replay run: {again.errors}"
    assert again.extra["replay_remaining"] == 0, "the replay did not consume the recording the kernel wrote"
    _write_or_check_goldens(d, again)  # raises GoldenMismatch if the two phases disagree
    assert again.normalized()["transcript"] == recorded.normalized()["transcript"]


@pytest.mark.asyncio
@pytest.mark.web
async def test_record_then_replay_in_the_web_lane(tmp_path: Path, hermetic_env: Path, recording):
    """The web lane is where recording breaks quietly: the client names a provider, not just a model."""
    d = _scenario_dir(tmp_path, kind="web", extra="  reload_equals_live: true")
    dom = dom_lane_available()[0]

    recorded = await run_web_scenario(load_scenario(d), tmp_path / "run1", env_conf=hermetic_env, dom=dom)
    assert not recorded.errors, f"errors during the recording run: {recorded.errors}"
    assert (d / "provider.jsonl").read_text(encoding="utf-8").strip(), "the web lane recorded an empty stream"
    if dom:
        # The headless client picks `HARNESS_RECORD_MODEL` in the model chip and sends its provider with it;
        # that is the request shape that a recorder registered under another name would never see.
        assert recorded.dom.get("timeline_text"), "the DOM lane painted nothing"
    _write_or_check_goldens(d, recorded, timeline=dom)

    recording(mode="replay")
    # Same lanes in both phases — the API lane and, when available, the browser. A web scenario's events
    # differ by *who* submitted the turn (the client sends the provider of the selected model, the API lane
    # does not), so replaying with a different lane mix would compare two different flows.
    again = await run_web_scenario(load_scenario(d), tmp_path / "run3", env_conf=hermetic_env, dom=dom)
    assert not again.errors, f"errors during the replay run: {again.errors}"
    assert again.extra.get("live_vs_reloaded_equal") is True, "a reload after replaying the recording lost records"
    # With the DOM lane on, this also compares what the browser painted in each phase: a recorded fixture has
    # to reproduce the pixels, not only the records.
    _write_or_check_goldens(d, again, timeline=dom)


@pytest.mark.asyncio
async def test_record_mode_names_the_providers_when_one_is_missing(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    """A typo in `HARNESS_RECORD_PROVIDER` is an error that lists what *is* loaded, never a silent fallback.

    The silent version is the dangerous one: quietly recording from the scripted provider would write a
    fixture that looks recorded and replays forever.
    """
    from harness.kernel.bootstrap import bootstrap_kernel

    ws = tmp_path / "ws"
    ws.mkdir()
    conf = tmp_path / "conf"
    conf.mkdir()
    monkeypatch.setenv("HARNESS_REPLAY", "record")
    monkeypatch.setenv("HARNESS_RECORD_PROVIDER", "definitely-not-a-provider")
    rt = await bootstrap_kernel(workspace=ws, config_dir=conf, resume_session=False)
    try:
        sc = Scenario(name="x", dir=tmp_path, kind="kernel")
        with pytest.raises(RuntimeError) as excinfo:
            record.install_provider(sc, rt)
        message = str(excinfo.value)
        assert "definitely-not-a-provider" in message, message
        assert "mock" in message and "anthropic" in message, f"the error should list what can be recorded: {message}"
    finally:
        await rt.registry.unload_all()


def test_a_boot_selects_the_provider_the_recording_was_made_from(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    """Names follow the recording, in both directions.

    A replay answers under the provider and model its recording came from, because both appear in the
    goldens (`turn.started`, `context.assembled`) and in the UI's model chip. A scenario with no sidecar —
    every synthetic fixture in this repo — keeps `replay` / `replay-v1`, which is why re-baselining was not
    needed for the 25 that exist.
    """
    from _lib.kernel_runner import base_overrides

    plain = Scenario(name="x", dir=tmp_path, kind="kernel")
    replay = dict(base_overrides(plain))
    assert replay["kernel.provider"] == "replay" and replay["kernel.model"] == "replay-v1"

    monkeypatch.setenv("HARNESS_REPLAY", "record")
    monkeypatch.setenv("HARNESS_RECORD_PROVIDER", "anthropic")
    monkeypatch.setenv("HARNESS_RECORD_MODEL", "claude-something")
    recording = dict(base_overrides(plain))
    assert recording["kernel.provider"] == "replay", "the tee keeps the fixture key; only the model follows the source"
    assert recording["kernel.model"] == "claude-something"

    # A scenario's own settings cannot pin a model or provider behind a recorder's back…
    pinned = Scenario(
        name="y",
        dir=tmp_path,
        kind="kernel",
        settings={"kernel.model": "scenario-pinned", "kernel.provider": "scenario-pinned"},
    )
    assert dict(base_overrides(pinned))["kernel.model"] == "claude-something"
    monkeypatch.setenv("HARNESS_REPLAY", "replay")
    # …but in replay mode the scenario is in charge again.
    assert dict(base_overrides(pinned))["kernel.model"] == "scenario-pinned"

    # And once a recording exists, replay reads the model id out of the sidecar — while the provider key stays
    # `replay`, so the tee is what a client resolves to rather than the built-in `mock` it recorded from.
    (tmp_path / "provider.requests.json").write_text(
        json.dumps({"provider": "mock", "model": "mock-v1", "requests": []}), encoding="utf-8"
    )
    back = dict(base_overrides(plain))
    assert back["kernel.provider"] == "replay" and back["kernel.model"] == "mock-v1"
