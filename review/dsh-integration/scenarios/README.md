# Flow-of-information scenarios (record/replay)

Executable end-to-end tests that push a prompt through the **real** kernel, the **real** web
daemon and the **real** shipped browser client, with the model replaced by a recording.
This is WS-01 of the integration pack, built to run against the kernel *as it is today*
(it imports the installed `harness.*` packages and touches nothing else), so it can be moved
into `tests/` with a `git mv` once adopted.

```
scenarios/
├── conftest.py              hermetic HOME / HARNESS_CONFIG_DIR per test, strips *_API_KEY
├── test_scenarios.py        one parametrised test per scenario dir, expectation checks, goldens
├── make_recordings.py       regenerates the synthetic provider.jsonl files (run after editing)
├── _lib/
│   ├── codec.py             ModelEvent <-> JSON lines (the recording format)
│   ├── replay_provider.py   ReplayProvider (strict: fails when the kernel asks for more than recorded)
│   │                        RecordingProvider (wraps a real provider; refuses to record secrets)
│   ├── kernel_runner.py     lane 1: bootstrap_kernel -> TurnRunner directly (+ reload check)
│   ├── web_runner.py        lane 2: HarnessWebServer over HTTP + SSE, exactly the client's payloads
│   ├── sse.py               tolerant SSE reader (named frames, ids, retry)
│   ├── normalize.py         strips ids / timestamps / paths so goldens are stable
│   ├── scenario.py          scenario.yaml model, discovery, golden compare
│   └── node/                lane 3: headless_timeline.mjs runs the shipped app.js in jsdom
└── <scenario>/
    ├── scenario.yaml        prompts, settings, approvals, expectations (schema below)
    ├── provider.jsonl       the recorded model stream(s), one JSON line per ModelEvent
    ├── golden/              accepted transcript / events / requests (/ http / timeline for web)
    └── _last_run/           (git-ignored) actual output + summary.json of the last run
```

## Running

```bash
# from the repo root, inside the project venv
pytest review/dsh-integration/scenarios -q                       # replay against goldens (default)
pytest review/dsh-integration/scenarios -q -k web                # only the web/jsdom lane
HARNESS_REPLAY=update pytest review/dsh-integration/scenarios -q # accept new goldens (inspect the diff!)
SCENARIO_DOM=0 pytest review/dsh-integration/scenarios -q        # skip the jsdom lane (no node)
SCENARIO_DOM_WAIT_MS=6000 ...                                     # per-turn wait in the browser lane
```

The jsdom lane needs `node` ≥ 18 and a one-time
`npm --prefix review/dsh-integration/scenarios/_lib/node ci` (lockfile pinned, jsdom 24.1.3;
`node_modules/` is git-ignored). If node or jsdom is missing the lane is reported in `errors`
and `timeline_*` / `dom_turns_completed` expectations fail — they never silently pass.

Always run hermetically so your own `~/.harness` cannot leak into a golden; `conftest.py`
does this per test, and for ad-hoc runs:

```bash
export HOME=$(mktemp -d) HARNESS_CONFIG_DIR=$HOME/.harness
```

## Three lanes

| lane | what is real | what is replayed | catches |
|---|---|---|---|
| `kind: kernel` | bootstrap, PolicyGate, tools, transcript, memory, MCP | model | kernel logic, persistence, reload ≠ live |
| `kind: web` (API) | everything above + `HarnessWebServer`, auth ticket, `/commands/*`, `/events` SSE | model | command payload drift, SSE framing, replay buffer |
| `kind: web` + DOM | everything above + the shipped `app.js`/`events.js`/`cards.js` in jsdom, real clicks | model | client never rendering, dead buttons, duplicated text |

The DOM lane types the prompt, clicks **Send**, waits for `turn.completed`, clicks **Approve**
when `dom_approves: true`, and reports the visible timeline text, the SSE event names the
browser saw, the exact `submit_turn` bodies it posted, and the fetch log.

## `scenario.yaml`

```yaml
name: approval_allow_web_dom
kind: web                      # kernel | web
summary: one line, shows up in failures
settings:                      # applied through SettingsStore before bootstrap
  kernel.security.permission_mode: ask
workspace_files:               # seeded into the temp workspace
  app.py: "print('x')\n"
mcp_servers: {}                # kernel lane only; launches the stdio echo server
turns:
  - prompt: "Create notes.txt"
    model: replay-1            # optional, defaults to the replay model
approvals: [approve, deny]     # scripted approver (kernel lane / API lane), in order
xfail: "F-11 not fixed yet"    # documents a known bug; strict xfail until it is fixed
expect:
  tools_called: [write_to_file]            # exact ordered list
  tools_called_subset: [view_file]         # must appear
  files: {notes.txt: {contains: "approved"}, gone.txt: {exists: false}}
  no_errors: true                          # default; set false for scenarios that must error
  event_types_include: [approval.required, turn.completed]
  event_types_exclude: [turn.error, turn.cancelled]
  stop_reasons: [max_iterations]           # kernel lane: TurnResult.stop_reason per turn
  replay_consumed: true                    # default; every recorded response was requested
  model_saw: ["hello harness"]             # substring of any model request
  model_never_saw: ["sk-"]                 # must never appear in a model request
  reload_equals_live: true                 # sessions.get after restart == live history
  dom_lane: true                           # web: run the jsdom lane (false = API lane only)
  dom_approves: true                       # web: the browser clicks Approve instead of the scripted approver
  dom_turns_completed: true                # web: the client itself saw turn.completed for every turn
  timeline_contains: ["notes.txt written"] # visible text in the rendered timeline
  timeline_not_contains: ["undefined"]
```

Golden files are compared after the expectations: `transcript.json`, `events.json`,
`requests.json` (what the model was shown), and for web `http.json` and `timeline.txt`.
A new scenario's first run creates the goldens and fails once on purpose; use
`HARNESS_REPLAY=update` to accept in one pass — and read the diff, a golden accepted while
a bug is present just freezes the bug (`thinking_then_answer_web` was first accepted with a
`turn.error` inside; `event_types_exclude` now prevents that).

## Recording from a real provider

`_lib/replay_provider.py::RecordingProvider` wraps any registered provider and writes the
stream to `provider.jsonl` while the scenario runs; it refuses to write if any `*_API_KEY` /
`*_TOKEN` value appears in the text. The built-in recordings are synthetic
(`make_recordings.py`) because this sandbox has no model access; the codec is identical, so
the kernel cannot tell the difference. When the recorder is wired into a CLI flag
(`HARNESS_REPLAY=record`, WS-01 acceptance), replace `recorded_from: synthetic` with the
provider/model id.

## Scenario inventory

| scenario | lane | exercises |
|---|---|---|
| plain_answer_kernel | kernel | text-only turn, usage, transcript |
| read_then_edit_kernel | kernel | view_file → edit_file, file content, model saw file |
| approval_allow_kernel | kernel | `permission_mode: ask`, scripted approve, write lands |
| approval_deny_no_approver_kernel | kernel | nobody answers → fail closed, no file written |
| memory_remember_search_kernel | kernel | memory plugin write → search in the same turn |
| mcp_echo_kernel | kernel | stdio MCP server tool `mcp__echo__echo` through PolicyGate |
| runaway_tool_loop_kernel | kernel | iteration cap → `stop_reason: max_iterations`, replay strictness |
| plain_answer_web | web + DOM | ticket → SSE → submit from the real client → text rendered once |
| thinking_then_answer_web | web + DOM | thinking deltas rendered as "Reasoning Process", turn completes |
| approval_allow_web_dom | web + DOM | approval card rendered, **Approve** clicked in jsdom, write lands |
| session_resume_web | web (API) | two turns, `sessions.get` equals what the page would re-render |

Findings produced by running these live in `../FLOW_TEST_FINDINGS.md`.

## Moving into the build

- `git mv review/dsh-integration/scenarios tests/scenarios` (keep `conftest.py`); add
  `tests/scenarios` to `testpaths`; CI needs `node` for the DOM lane or `SCENARIO_DOM=0`.
- `_lib/replay_provider.py` is the seed of `harness/sdk/testing/replay.py` (WS-01).
- `_lib/normalize.py` rules are the place to add new volatile fields when goldens flap.
