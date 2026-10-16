# Scenario tests — record, replay, update

Scenario tests push a prompt through the **real** kernel, the **real** web daemon and the **real**
shipped browser client, with the model replaced by a recording. They exist to answer one question that
unit tests cannot: does information actually *flow* from the user's keystroke to the model request, back
through the event bus, into the transcript, and onto the screen — and does the same session read the same
way after a reload or a daemon restart? The spec is
`review/dsh-integration/WS-01_record_replay_tests.md`; the in-suite reference, including the full
`scenario.yaml` expectations schema and the scenario inventory, is
[`tests/scenarios/README.md`](../../tests/scenarios/README.md). This page is about the loop: how to run a
scenario, how to record one, and how to update the goldens without freezing a bug.

## Running

```bash
pytest tests/scenarios -q                          # replay against the committed goldens (default)
pytest tests/scenarios -q -k web                   # only the scenarios with a web lane
SCENARIO_DOM=0 pytest tests/scenarios -q           # skip the jsdom lane (no node on this machine)
SCENARIO_DOM_WAIT_MS=6000 pytest tests/scenarios   # per-turn wait inside the browser lane
HARNESS_REPLAY=update pytest tests/scenarios -q    # re-record the goldens (read the diff)
HARNESS_REPLAY=record  pytest tests/scenarios -q -k <name>   # record a new provider stream
```

Always run hermetically, so your own `~/.harness` cannot leak into a golden. `tests/scenarios/conftest.py`
does this per test and strips `*_API_KEY` from the environment; for ad-hoc runs:

```bash
export HOME=$(mktemp -d) HARNESS_CONFIG_DIR=$HOME/.harness
```

The DOM lane needs `node` ≥ 18 and a one-time `npm ci` at the repository root (jsdom is pinned in
`package-lock.json`). If either is missing the lane is reported in `errors` and the `timeline_*` /
`dom_turns_completed` expectations fail — a missing browser never turns into a silent pass.

## What one run asserts

| lane | what is real | what is replayed | what it catches |
|---|---|---|---|
| `kind: kernel` | bootstrap, PolicyGate, tools, transcript, memory, MCP | the model | kernel logic, persistence, reload ≠ live |
| `kind: web` (API) | the above + `HarnessWebServer`, auth ticket, `/commands/*`, `/events` SSE | the model | command payload drift, SSE framing, replay buffer |
| `kind: web` + DOM | the above + the shipped `app.js`/`events.js`/`cards.js` in jsdom, real clicks | the model | the client never rendering, dead buttons, duplicated text |

After the `expect:` block in `scenario.yaml`, the run compares goldens in `<scenario>/golden/`:
`transcript.json`, `events.json`, `requests.json` (what the model was actually shown) and, for web
scenarios, `http.json` (the request/response pairs) and `timeline.txt` (the visible browser text).
Everything in them is normalised first — ids, timestamps, workspace paths — so the same flow produces the
same bytes on any machine; new volatile fields go in `_lib/normalize.py`, never in a scenario.

## Adding a scenario

1. `mkdir tests/scenarios/<name>` and write `scenario.yaml` (prompts, `settings`, `workspace_files`,
   `approvals`, `expect`) — copy the closest existing scenario and edit.
2. Write `provider.jsonl`: one JSON line per `ModelEvent`, blank line between requests — and add the same
   stream to `RECORDINGS` in `tests/scenarios/make_recordings.py` if the scenario is synthetic. That file is
   the corpus's source: `test_make_recordings.py` regenerates every fixture from it and compares bytes, so a
   hand-written recording nobody can reproduce fails the suite. Streams recorded from a real provider are the
   exception, and the generator refuses to touch them.
3. Set `recorded_from: synthetic` until a real stream replaces it.
4. Run it: `pytest tests/scenarios -q -k <name>`. The **first run creates `golden/` and fails on purpose** —
   that failure is the review step, not a bug.
5. Read what was written, then accept it with `HARNESS_REPLAY=update`. A golden accepted while a bug is
   present freezes the bug, so assert the shape too: `event_types_exclude: [turn.error, turn.cancelled]`
   exists because `thinking_then_answer_web` was first accepted with a `turn.error` inside it.
6. Add a row to the inventory in `tests/scenarios/README.md`.

## Recording a real provider's stream

The same codec reads and writes either kind of fixture, so the kernel cannot tell a recording from a
scripted stream. Recording is a mode of the same tests, not a separate tool:

```bash
export HARNESS_REPLAY=record              # write instead of replay
export HARNESS_RECORD_PROVIDER=anthropic  # required; a name that is loaded in this workspace
export HARNESS_RECORD_MODEL=claude-sonnet-4-5   # optional; the provider's default model otherwise
export ANTHROPIC_API_KEY=…                # your own key, in your own shell
pytest tests/scenarios -q -k <name>
```

That produces two files in the scenario directory: `provider.jsonl` (the streams, exactly what the kernel
consumed) and `provider.requests.json`, the sidecar the fixture is replayed *through*:

```json
{ "provider": "anthropic", "model": "claude-sonnet-4-5", "requests": [ … ] }
```

`model` is not decoration: the id appears in `turn.started`, `context.assembled` and the client's model
chip, so replay pins whatever the recording names (`_lib.replay_provider.scenario_model`) instead of a
constant that would make a recorded scenario unrepeatable by anyone who was not in the room when it was
recorded. `provider` says where the stream came from, for whoever reviews the fixture. Then replay, accept
goldens, and set `recorded_from: <provider>/<model>` in the yaml so a reader knows the fixture is real.

A record run refuses to overwrite a `provider.jsonl` that already exists — the 25 committed fixtures are the
corpus every golden was derived from, and `pytest -k` selects a scenario, not an intention to re-record it.
`HARNESS_RECORD_OVERWRITE=1` says you mean it; `git diff` is then the review of both the stream and the
goldens. Recording into a scratch copy needs no flag, which is how the record path is tested.

`RecordingProvider` refuses to write a stream containing the value of any `*_API_KEY` / `*_TOKEN` in the
environment, and the recorder is flushed **before** any restart or reload lane runs, so a second boot
replays the recording that was just written rather than discovering a missing file.

### Where the code lives

`harness/sdk/testing/replay.py` holds the providers and the format — `ReplayProvider`, `RecordingProvider`,
`encode_event` / `decode_event` / `load_streams` / `write_streams` / `request_summary` — exported from
`harness.sdk.testing`, so a plugin's own tests can record and replay without importing the suite.
`ScriptedProvider(strict_exhaustion=True)` is the same strictness for a hand-written script: the default
(`False`) keeps unit tests polite, a golden test is not one. `tests/scenarios/_lib/replay_provider.py` is the
thin scenario-side view of it — the fixture's provider key, the recorded-model lookup and the golden
projection — and `_lib/codec.py` is a re-export, kept so the suite reads as one thing.

## The contract behind it, and the failure it prevents

The recorder is registered under the fixture provider's own name (`replay`) and the provider it wraps is
**removed from the registry for the duration of the run**. Both halves matter, and getting them the other
way round is the trap:

* Key the tee as something else and `kernel.provider` (which scenarios pin to `replay`) never reaches it.
* Leave the wrapped provider registered and a client request can resolve *past* the tee — which is exactly
  how a recording session ended up with an empty `provider.jsonl` while every expectation still went green,
  because the built-in `mock` answered with text that looked plausible.

Symptoms of that class of mistake, and what they mean:

| symptom | cause |
|---|---|
| `ReplayExhausted: request #1 has no recording` right after a `record` run | the tee answered nothing: the request resolved past it (see above) |
| `provider.jsonl` of 1 byte / `streams: 0` in `_last_run/summary.json` | same, seen from the writing side |
| goldens that replay green but record-and-replay mismatch on `provider` | the record and replay phases ran different lanes, or the chip selected a different provider; run both phases with the same `dom=` and compare `timeline` too |
| a DOM-lane turn whose `turn.started` says `provider: "mock"` while the fixture is `replay` | a recorded model id is also served by a built-in, and the chip selected by model id alone — the headless script scopes the selection by the option's `data-provider` (`MODEL_PROVIDER`) for that reason |
| an empty reload/restart report | raise `SCENARIO_DOM_WAIT_MS`; the page needs time to render before it is read |

`tests/scenarios/test_record_replay.py` is the executable version of that table: it records into a scratch
copy of a scenario, replays it, and asserts the two phases agree in both lanes.

## In CI

`.github/workflows/ci.yml` runs `pytest -q` (which includes `tests/scenarios`) with `SCENARIO_DOM_WAIT_MS=6000`
after installing node and `npm ci`. `HARNESS_REPLAY` is deliberately left at its default, so CI only ever
replays: a real recording can only be made by a human with credentials, and goldens are reviewed as a diff.
