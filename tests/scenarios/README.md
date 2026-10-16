# Flow-of-information scenarios (record/replay)

Executable end-to-end tests that push a prompt through the **real** kernel, the **real** web
daemon and the **real** shipped browser client, with the model replaced by a recording.
This is WS-01 of the integration pack (`review/dsh-integration/WS-01_record_replay_tests.md`), built to
run against the kernel *as it is today* (it imports the installed `harness.*` packages and touches
nothing else), so it can be moved into `tests/` with a `git mv` once adopted. Nothing in here depends
on its own location: the repo root is found by walking up to `pyproject.toml` + `harness/`.

The contributor-facing how-to (add / record / update, and the failure playbook) is
[`docs/testing/SCENARIOS.md`](../../docs/testing/SCENARIOS.md). This file is the in-suite reference: the
`scenario.yaml` schema, the lanes and the inventory.

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
pytest tests/scenarios -q                           # replay against goldens (default; `pytest -q` includes them)
pytest tests/scenarios -q -k web                    # only the web/jsdom lane
HARNESS_REPLAY=update pytest tests/scenarios -q     # accept new goldens (inspect the diff!)
SCENARIO_DOM=0 pytest tests/scenarios -q            # skip the jsdom lane (no node)
SCENARIO_DOM_WAIT_MS=6000 ...                                     # per-turn wait in the browser lane
```

The jsdom lane needs `node` ≥ 18 and a one-time `npm ci` at the repository root (the single
`package.json`; jsdom pinned in `package-lock.json`, `node_modules/` git-ignored — the headless script
resolves it by walking up). If node or jsdom is missing the lane is reported in `errors`
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
secrets:                       # seeded into the kernel's Secrets store (config dir) before the run
  INTERNAL_TOKEN: hunter2-super-secret-value
xfail: "F-13 not fixed yet"    # documents a known bug; strict xfail until it is fixed
expect:
  tools_called: [write_to_file]            # exact ordered list
  tools_called_subset: [view_file]         # must appear
  files: {notes.txt: {contains: "approved"}, gone.txt: {exists: false}}
  no_errors: true                          # default; set false for scenarios that must error
  event_types_include: [approval.required, turn.completed]
  event_types_exclude: [turn.error, turn.cancelled]
  events_match:                            # JSON subset at least one event must match
    - {type: tool.decision, data: {action: deny, policy: workspace_jail}}
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
  timeline_order: ["Noting that.", "remember", "Saved."]  # these must appear in this order
  dom_reload: true                         # web: boot the client again on the same session (= page refresh)
  reload_timeline_contains: ["Question one", "Answer one"]  # what the refreshed page must show, with no click
  dom_new_session: false                   # web: do not click "New session" — the first prompt must create one (session_id: null)
  dom_restart: true                        # web: stop the daemon, boot a new kernel+server on the same workspace, reload the page
  restart_timeline_contains: ["Question one", "Answer two"]  # what the page shows after the restart, with no click
  sessions_after_restart: 1                # sessions.list row count after the restart (the resumed one is `active`)
  auth_required: true                      # web, default: unauthenticated command/ticket/events -> 401
```

Golden files are compared after the expectations: `transcript.json`, `events.json`,
`requests.json` (what the model was shown), and for web `http.json` and `timeline.txt`.
A new scenario's first run creates the goldens and fails once on purpose; use
`HARNESS_REPLAY=update` to accept in one pass — and read the diff, a golden accepted while
a bug is present just freezes the bug (`thinking_then_answer_web` was first accepted with a
`turn.error` inside; `event_types_exclude` now prevents that).

## Recording from a real provider

`HARNESS_REPLAY=record` swaps the fixture provider for a tee: `_lib/record.py` takes the provider named by
`HARNESS_RECORD_PROVIDER` out of the registry, registers `RecordingProvider` under the fixture's own key
(`replay`) in its place, and writes `provider.jsonl` plus a `provider.requests.json` sidecar
(`{"provider", "model", "requests"}`) as the scenario runs. `HARNESS_RECORD_MODEL` picks the model; the
replay side reads the id back out of the sidecar (`scenario_model`), which is what keeps a recording made
against `claude-…` replayable by someone else. The tee keeps the name `replay` and the wrapped provider
steps aside deliberately: key it differently and `kernel.provider` never reaches it, leave the wrapped one
registered and the request resolves past it — a run then records nothing and still looks green.
`RecordingProvider` refuses to write a stream containing the value of any `*_API_KEY` / `*_TOKEN`, and
`ProviderHandle.finish()` runs before any restart or reload lane.

The built-in recordings are synthetic (`make_recordings.py`), because this sandbox has no model access; the
codec is identical, so the kernel cannot tell the difference, and `recorded_from` says which is which.
`test_record_replay.py` records into a scratch copy and asserts record and replay agree — in the kernel
lane and through the browser.

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
| session_reload_web_dom | web + DOM | two turns in the client, then a page refresh: history visible without a click |
| daemon_restart_web_dom | web + DOM | no session at boot: first prompt creates one (`session_id: null`), daemon restart + reload resumes it — one row, full history, no click |
| read_then_edit_web_dom | web + DOM | tool cards with outputs in order, `📝 File Diff` card, refresh keeps the tool calls |
| memory_remember_web_dom | web + DOM | remember / search_memory cards in order, found fact shown |
| tirith_blocks_ssrf_web_dom | web + DOM | a policy denial renders as `DENIED` + reason, never `ALLOWED` |
| skills_view_kernel | kernel | skills catalog in the system prompt, `view_skill` returns the playbook |
| jail_blocks_traversal_kernel | kernel | read and write outside the workspace denied by `workspace_jail` |
| shell_exec_denied_without_approver_kernel | kernel | `run_command` in `ask` mode with nobody approving → fails closed |
| secret_redacted_from_model_kernel | kernel | stored secret + `sk-…` pattern redacted before the model sees them |
| tirith_blocks_ssrf_kernel | kernel | metadata-endpoint `curl` denied by Tirith in allow mode |
| edit_without_read_blocked_kernel | kernel | WS-03 loop hygiene: an `edit_file` on a file never read is denied, with the reason in the tool result |
| repeat_call_reminder_kernel | kernel | WS-03 loop hygiene: the same call twice in a row gets a reminder instead of a second execution |
| huge_tool_output_pruned_and_spilled_kernel | kernel | WS-03 loop hygiene: oversized output is truncated in the model's view and the full text spills to a file |
| reload_equals_live_web_dom | web + DOM | WS-04c: the live DOM timeline and the reloaded one are the same set of lines, approvals and cards included |

Every web scenario additionally records that unauthenticated `/commands/*`, `/auth/ticket`, `/events` and a
bogus ticket are rejected with 401 (`http.json`, entry `unauthenticated`).

Findings produced by running these live in `../FLOW_TEST_FINDINGS.md`.

## In the build (consolidation P5)

- The suite lives at `tests/scenarios` and runs with the rest of `pytest -q` (`testpaths = ["tests"]`);
  CI installs node + `npm ci` for the DOM lane and sets `SCENARIO_DOM_WAIT_MS=6000`.
- `plain_answer_web` also opens the settings modal and asserts the schema-driven group tabs and a clean
  browser console (`settings_groups_include`, `no_console_errors`) — the former `ui_smoke` checks.
- the providers live in `harness/sdk/testing/replay.py` (WS-01) and `_lib/replay_provider.py` is a thin
  view of them; they implement the full `Provider` contract (`is_configured` / `is_reachable`) because the
  registry enforces it.
- `_lib/normalize.py` rules are the place to add new volatile fields when goldens flap.
