# WS-01 — Record/replay golden tests (the real kernel, keyless, in CI)

| | |
|---|---|
| Status | specified |
| Depends on | §8 critical path merged (UI completes a turn) |
| Effort | 2–3 days |
| Principles | P5 (primary), P1 |
| dsh reference (@86a40220) | `packages/testing/llm-replay/` (keyless boot from recorded JSONL, `DSH_SNAPSHOT=replay`), `packages/testing/session-snapshot/` (normalised goldens across headless/SDK/ACP/Web), `client/testing/` web interaction snapshots |
| Harness-Zero touchpoints | `harness/sdk/testing/scripted.py:18` (`ScriptedProvider`), `harness/sdk/contracts.py:44` (`Provider` protocol), `harness/kernel/bootstrap.py:41` (`bootstrap_kernel`), `harness/kernel/runner.py:299-304` (`ModelRequest` built from `records_to_messages`), `harness/kernel/session.py` (transcript store), `harness/frontends/web/server.py:92` (`HarnessWebServer`), `tests/frontends/test_web_turn.py` (HTTP/SSE turn via `httpx`), `tests/e2e/test_scenarios_s1_s6.py`, `review/tools/ui_smoke.mjs` (jsdom harness), `.github/workflows/ci.yml` |

## 1. Outcome

A developer (or CI) can run `pytest tests/scenarios -q` with no API key and get a pass/fail verdict for every user-visible capability, where each scenario:

1. boots the **real kernel** (`bootstrap_kernel`) — real plugins, real gate, real transcript store, real session manager — with the provider replaced by a `ReplayProvider` that streams events recorded from a real model;
2. drives it through the **same entry points users use** (`TurnRunner.execute_turn`, and for `kind: web` through `HarnessWebServer` over HTTP/SSE);
3. compares normalised outputs against committed goldens: transcript records, SSE event sequence, and (web) rendered timeline text.

Recording is explicit and local: `HARNESS_REPLAY=record pytest tests/scenarios -k <name>` with a real provider configured rewrites that scenario's `provider.jsonl`. CI runs with `HARNESS_REPLAY=replay` (default when the file exists) and fails on a missing or exhausted recording instead of silently falling back.

This is the gate that would have caught "UI never completed a turn" (v0.2.1), `Invalid session_id: None` (`app.js:35`), and the memory-path split (Addendum A.1).

## 2. Current state (verified at `f43c272`)

- `ScriptedProvider` (`sdk/testing/scripted.py`) replays in-memory event lists and records `ModelRequest`s. It is only constructible from Python; there is no on-disk format, no recorder, and **it yields `Done(end_turn)` when exhausted** (`generate`, L63-66), so an over-long loop passes silently.
- `tests/e2e/test_scenarios_s1_s6.py` and `tests/frontends/test_web_turn.py` build scripted responses by hand per test; no goldens, no event-sequence assertions beyond a few `in` checks.
- There is no headless DOM lane in CI; `review/tools/ui_smoke.mjs` proved jsdom can load `app.js` (with `Object.defineProperty(globalThis,'navigator',…)` and manual `DOMContentLoaded` re-dispatch — see review v0.2.1 §3).
- The CI workflow runs ruff, import boundaries, hermetic suite, plugin doctor, plugin tests, events-schema drift, clean-tree check. No scenario lane.

## 3. Design

### 3.1 On-disk scenario

```
tests/scenarios/
├── conftest.py                     # fixtures: scenario loader, kernel boot, web boot, normalisers
├── _lib/
│   ├── replay_provider.py          # ReplayProvider / RecordingProvider (see §4)
│   ├── normalize.py                # strip timestamps, ids, durations, paths, costs
│   ├── sse.py                      # SSE reader (httpx stream → list[dict])
│   └── dom.py                      # runs tools/headless_timeline.mjs via node, returns text
├── test_scenarios.py               # parametrised over every scenario dir
└── <scenario_name>/
    ├── scenario.yaml               # inputs + kind + settings overrides + what to assert
    ├── provider.jsonl              # recorded model stream(s), one JSON object per ModelEvent, blank line = next request
    ├── workspace/                  # optional seed files copied into the tmp workspace
    └── golden/
        ├── transcript.json         # normalised TranscriptRecords (all steps)
        ├── events.json             # normalised bus/SSE event sequence (type + durable fields)
        └── timeline.txt            # kind: web only — headless DOM text of the conversation pane
```

`scenario.yaml`:

```yaml
name: edit_file_with_diff_card
kind: kernel            # kernel | web
summary: model reads a file, edits it, the diff card is emitted and the file changes on disk
settings:
  kernel.permission_mode: auto          # avoid approvals in replay unless the scenario is about approvals
  plugins.hooks-checkpoints.enabled: true
turns:
  - prompt: "Rename greet() to hello() in app.py"
approvals:                              # optional: scripted approver decisions in order
  - allow
expect:
  tools_called: [read_file, edit_file]
  files:
    app.py: {contains: "def hello("}
  transcript_golden: true
  events_golden: true
  timeline_golden: false                # kernel kind has no DOM
```

`provider.jsonl` — one `ModelEvent` per line, tagged by class name (`{"t":"TextDelta","text":"…"}`, `{"t":"ToolCallEnd","id":"c1","name":"read_file","arguments":{…}}`, `{"t":"Done","stop_reason":"tool_use"}`), requests separated by an empty line. Recording also writes the normalised **request** the model saw (`provider.requests.json`) so a drift in prompt assembly is a visible diff, not a mystery (P1).

### 3.2 Provider

`ReplayProvider` implements `harness.sdk.contracts.Provider` and is registered via `registry.register_provider(p, plugin_name="scenario")` exactly like `ScriptedProvider`. Differences from `ScriptedProvider`:

- loads from `provider.jsonl`;
- **exhaustion is an error** (`ReplayExhausted`) — the kernel's `turn_error` path is then visible in the golden; a scenario can opt into `on_exhausted: end_turn` explicitly;
- optional `match: strict` verifies that the normalised request matches `provider.requests.json` (tool names offered, last user message, message count) and raises `ReplayMismatch` with a unified diff.

`RecordingProvider` wraps a real provider (whichever `options.provider` names) and tees events + requests to the scenario dir. Only activated by `HARNESS_REPLAY=record`.

### 3.3 Normalisation (`_lib/normalize.py`)

Strip or replace: timestamps, `step_index` kept, record/call ids → `id#n` by first-seen order, tmp paths → `<ws>`, durations/costs/token counts → `<n>`, session ids → `<session>`, card ids → `<card#n>`. Normaliser is shared by transcript and events so the same rules apply.

### 3.4 Web kind

Boot `HarnessWebServer` on an ephemeral port in-process (pattern in `tests/frontends/test_web_turn.py`), open `/events` SSE with `httpx`, `POST /command {"command":"sessions.create"}` then `submit_turn` **with the real client payload shape** (parse it from `static/api.js` in the test so client and test cannot drift), collect events until `turn.complete`/`turn.error`. Then run `tools/headless_timeline.mjs` (moved from `review/tools/ui_smoke.mjs`, fixed up) which loads `index.html` + `app.js` in jsdom, feeds the recorded SSE events through the client's dispatcher, and prints the conversation pane's text. Goldens: `events.json` and `timeline.txt`.

Node is already required by CI for `tests/frontends/test_cards.mjs`; add `npm ci` in `tests/frontends/package.json` for jsdom (pinned).

### 3.5 Scenario set for the first PR (minimum)

| name | kind | covers |
|---|---|---|
| `plain_answer` | kernel + web | text streaming, `turn.complete`, cost event |
| `read_then_edit_with_diff_card` | kernel + web | tools, diff card, checkpoints hook |
| `approval_required_allow` | web | `approval.required` → `resolve_approval` → tool runs |
| `approval_required_deny` | kernel | fail-closed path, tool result shows denial |
| `memory_remember_search` | kernel | A.1 regression as a scenario |
| `mcp_echo_tool` | kernel | A.2 regression as a scenario (uses `tests/fixtures/mcp_echo_server.py`) |
| `compaction_triggers` | kernel | `pre_compact` hook, summary record, `derive_messages` after compaction |
| `session_resume` | web | create → turn → `sessions.switch` → history is identical |

Each later WS adds its own rows (listed in each WS §5).

## 4. Files

### ADD

```
tests/scenarios/conftest.py
tests/scenarios/_lib/__init__.py
tests/scenarios/_lib/replay_provider.py
tests/scenarios/_lib/normalize.py
tests/scenarios/_lib/sse.py
tests/scenarios/_lib/dom.py
tests/scenarios/test_scenarios.py
tests/scenarios/<8 scenario dirs>/…
tests/frontends/package.json                 # jsdom pinned
tools/headless_timeline.mjs                  # from review/tools/ui_smoke.mjs
docs/testing/SCENARIOS.md                    # how to add/record/update a scenario
```

### MODIFY

```
harness/sdk/testing/__init__.py              # export ReplayProvider, RecordingProvider
harness/sdk/testing/scripted.py              # NEW ctor flag strict_exhaustion=False (default keeps old behaviour); ReplayProvider sets True
.github/workflows/ci.yml                     # + "Scenarios (replay)" step, + node setup/npm ci for jsdom
pyproject.toml                               # pytest markers: scenario, web
.gitignore                                   # tests/scenarios/**/_last_run/ (actuals written on failure)
```

### Skeletons

`tests/scenarios/_lib/replay_provider.py`

```python
from __future__ import annotations
import json
from collections.abc import AsyncIterator
from dataclasses import asdict
from pathlib import Path
from harness.sdk.types import (Done, ModelEvent, ModelInfo, ModelRequest, ProviderCaps, ProviderNotice,
                               TextDelta, ThinkingDelta, ToolCallDelta, ToolCallEnd, ToolCallStart, Usage, UsageEvent)

_TYPES = {c.__name__: c for c in (TextDelta, ThinkingDelta, ToolCallStart, ToolCallDelta, ToolCallEnd,
                                  UsageEvent, ProviderNotice, Done)}

class ReplayExhausted(RuntimeError): ...
class ReplayMismatch(AssertionError): ...

def _decode(line: str) -> ModelEvent:
    d = json.loads(line); t = d.pop("t")
    if t == "UsageEvent":
        return UsageEvent(usage=Usage(**d["usage"]))
    return _TYPES[t](**d)

def _encode(ev: ModelEvent) -> str:
    d = asdict(ev); d["t"] = type(ev).__name__
    return json.dumps(d, sort_keys=True)

def load_streams(path: Path) -> list[list[ModelEvent]]:
    streams, cur = [], []
    for line in path.read_text().splitlines():
        if not line.strip():
            if cur: streams.append(cur); cur = []
            continue
        cur.append(_decode(line))
    if cur: streams.append(cur)
    return streams

class ReplayProvider:
    name = "scenario"
    caps = ProviderCaps(native_tools=True, streaming=True, vision=False, cache_hints=False, thinking=True, json_output=True)

    def __init__(self, scenario_dir: Path, *, model: ModelInfo, strict: bool = False) -> None:
        self._streams = load_streams(scenario_dir / "provider.jsonl")
        self._expected = json.loads((scenario_dir / "provider.requests.json").read_text()) if strict else None
        self._model = model; self._i = 0
        self.requests: list[ModelRequest] = []

    async def list_models(self) -> list[ModelInfo]: return [self._model]

    async def generate(self, req: ModelRequest) -> AsyncIterator[ModelEvent]:
        self.requests.append(req)
        if self._i >= len(self._streams):
            raise ReplayExhausted(f"request #{self._i + 1} has no recording")
        if self._expected is not None:
            _check(req, self._expected[self._i])  # raises ReplayMismatch with a unified diff
        for ev in self._streams[self._i]:
            yield ev
        self._i += 1

    async def aclose(self) -> None: ...

class RecordingProvider:
    """Tees a real provider's stream into provider.jsonl + provider.requests.json."""
    def __init__(self, inner, scenario_dir: Path) -> None: ...
    async def generate(self, req: ModelRequest) -> AsyncIterator[ModelEvent]:
        # write normalised request; for ev in inner.generate(req): append _encode(ev); yield ev; then blank line
        ...
```

`tests/scenarios/test_scenarios.py`

```python
SCENARIOS = sorted(p for p in Path(__file__).parent.iterdir() if (p / "scenario.yaml").exists())

@pytest.mark.parametrize("scenario_dir", SCENARIOS, ids=lambda p: p.name)
@pytest.mark.asyncio
async def test_scenario(scenario_dir: Path, tmp_path: Path, replay_mode: str, scenario_runner):
    sc = load_scenario(scenario_dir)
    actual = await scenario_runner.run(sc, tmp_path)   # dispatches on sc.kind
    assert_expectations(sc, actual)                     # tools_called, files, approvals consumed
    compare_golden(scenario_dir / "golden" / "transcript.json", normalize(actual.transcript), update=replay_mode == "record")
    compare_golden(scenario_dir / "golden" / "events.json", normalize(actual.events), update=replay_mode == "record")
    if sc.kind == "web":
        compare_golden(scenario_dir / "golden" / "timeline.txt", actual.timeline_text, update=replay_mode == "record")
```

`compare_golden` writes the actual next to the golden under `_last_run/` and raises with a unified diff; `HARNESS_REPLAY=update` rewrites goldens **without** re-recording the provider (for intentional output changes — the PR diff then shows exactly what changed for the user).

CI step:

```yaml
- name: Scenarios (replay, keyless)
  env: { HARNESS_REPLAY: replay, HOME: ${{ runner.temp }}/home }
  run: |
    npm --prefix tests/frontends ci
    pytest tests/scenarios -q -p no:cacheprovider
```

## 5. Tests (fail-first)

| test | fails on main because |
|---|---|
| `tests/scenarios/test_scenarios.py::test_scenario[plain_answer-web]` | no scenario infra; once infra exists and the client payload is parsed from `api.js`, it fails until the §8 client fix is in |
| `tests/kernel/test_replay_provider.py::test_exhaustion_is_an_error` | `ScriptedProvider` returns `Done` when exhausted |
| `tests/kernel/test_replay_provider.py::test_roundtrip_encode_decode` | new |
| `tests/scenarios/_lib/test_normalize.py` | new — ids/timestamps/paths replaced deterministically |
| `tests/scenarios/test_scenarios.py::test_scenario[session_resume-web]` | `sessions.switch` history vs transcript equality (currently unasserted anywhere) |

## 6. Acceptance

```bash
. .venv/bin/activate
export HOME=$(mktemp -d) HARNESS_CONFIG_DIR=$HOME/.harness
pytest tests/scenarios -q                              # all pass, no network, no keys
HARNESS_REPLAY=replay pytest tests/scenarios -q -k mcp_echo
# teeth check: revert Addendum A.1 fix (git stash the sdk/paths change) → memory_remember_search must FAIL
# teeth check: break app.js submit payload → plain_answer-web must FAIL with a diff of events.json
ruff check . && pytest -q && harness plugin doctor --all
```

## 7. Non-goals / cautions

- Not a load or latency test; `timeout_s` paths are covered in WS-03.
- Do not record with secrets in the workspace; `RecordingProvider` refuses to write if any `HARNESS_*_KEY` value appears in the stream.
- Keep recordings small: one scenario ≈ 2–6 requests. Large multi-turn sagas belong in WS-10 (goals).
- Headless DOM uses jsdom, not a browser: CSS, focus and layout are out of scope; `timeline.txt` is text content only.
- Do not couple goldens to vendor bundles under `static/vendor/`.

## 8. Agent brief (paste to the build agent)

> Implement WS-01 from `review/dsh-integration/WS-01_record_replay_tests.md`. Create `tests/scenarios/` with `_lib/replay_provider.py` (`ReplayProvider`, `RecordingProvider`, JSONL codec in §3.1/§4), `_lib/normalize.py`, `_lib/sse.py`, `_lib/dom.py`, `conftest.py`, and the parametrised `test_scenarios.py`. Move `review/tools/ui_smoke.mjs` to `tools/headless_timeline.mjs` and make it a CLI that reads SSE events JSON on stdin and prints the conversation pane text. Add the eight scenarios in §3.5 with recordings produced by hand-written event lists converted through the codec (no real provider needed for the first PR; mark them `recorded_from: synthetic` in `scenario.yaml`). Add the CI step. Fail-first: show `test_exhaustion_is_an_error` failing against `ScriptedProvider` before adding `strict_exhaustion`. Do not modify kernel behaviour in this PR; if a scenario exposes a kernel bug, record it as `xfail(strict=True, reason=…)` with an issue link and fix it in its own PR. Write `docs/testing/SCENARIOS.md`. Open PR `WS-01: record/replay scenario tests`.
