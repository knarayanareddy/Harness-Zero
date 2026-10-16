# `additions/` — what the flow-of-information work added, in one place

Everything produced while taking Harness-Zero from "the kernel works in unit tests" to "the shipped
web client completes real turns, and we can prove it without an API key". Saved here so it can be
picked up later by a person or a build agent without re-reading the conversation that produced it.

Branch `arena/99a86436-harness-zero`, PR #4 onto `main @ f32faf1` (v0.2.1 + review). Runtime fixes are
already applied in `harness/` and `tests/`; this folder holds the evidence, the test suite that found
the bugs, and a portable patch of the fixes.

**Update after `main @ 158c836`** (the build agent's remediation commit): the 20 scenarios still pass their DOM
expectations; the seven web goldens were re-baselined once because the client now auto-creates a session on a
fresh daemon (a leading `session.started` event). The review of that commit is `review/CODE_REVIEW_158c836.md`;
its plan P5 moves this suite into `tests/` and CI.

**Update after consolidation P2** (`review/CONSOLIDATION_LOG.md`): that leading `session.started` was the defect
(an empty session per page load). The kernel now owns the lifecycle — resume on start, lazy create on the first
prompt — so the same seven goldens lost it again, and a 21st scenario, `daemon_restart_web_dom`, pins the new
behaviour: two prompts with no session, daemon restart, page reload → one session row, full history, no click.

**Update after consolidation P4**: `Provider.is_configured()` / `is_reachable()` joined the SDK contract and the
registry now rejects providers that lack them, so `scenarios/_lib/replay_provider.py` implements both (the strict
web lanes caught the gap: without it the picker lost `replay-v1` and the DOM lane sent prompts with no model).
`tools/ui_audit.py --strict` became the P3 gate and runs in CI together with `npm run build:css` freshness.

**Update after consolidation P5 — the runnable parts moved into the build.** `additions/scenarios/` is now
`tests/scenarios/` (part of plain `pytest -q`, CI runs it with the DOM lane) and `additions/tools/ui_audit.py` is
`tests/frontends/ui_audit.py` (next to `test_ui_coherence.py`, still a CLI). This folder keeps the documents
(`FLOW_TEST_FINDINGS.md`) and the portable patch; the paths below are historical.

**Update after P11b — WS-01 is closed.** The suite is 25 scenarios in three lanes with
`HARNESS_REPLAY=record|replay|update`, and the replay machinery moved up into the SDK
(`harness/sdk/testing/replay.py`: `ReplayProvider`, `RecordingProvider`, the JSONL codec,
`ScriptedProvider(strict_exhaustion=…)`), so a plugin can record and replay without importing the suite. The
contributor how-to is `docs/testing/SCENARIOS.md`; `tests/scenarios/README.md` keeps the schema and inventory.

## Contents

| path | what it is | use it for |
|---|---|---|
| `FLOW_TEST_FINDINGS.md` | The executed test's findings F-1 … F-12: symptom, evidence, root cause, fix location, regression test, status. §3 lists every `harness/` file touched. §4 corrects the integration pack. **§5 is the per-subsystem matrix** (plugins, skills, memory, diff/checkpoints, auth, guardrails, sessions, MCP, providers) of what is proven and what is not. | Knowing what is fixed, what is left, and how much to trust each subsystem. |
| `scenarios/` → **now `tests/scenarios/`** | 25 record/replay scenarios: real kernel → real web daemon → the shipped `app.js`/`events.js`/`cards.js` in jsdom, model replayed from `provider.jsonl`, goldens per scenario. `scenarios/README.md` documents the schema, the three lanes, how to record from a real provider, and how to move it into `tests/`. | Regression gate for every later change (`pytest tests/scenarios -q`). WS-01 of the integration pack, executed in full at P11b. |
| `tools/ui_audit.py` → **now `tests/frontends/ui_audit.py`** | Static coherence audit of the shipped web UI: ids referenced by JS vs `index.html`, CSS classes used but undefined / defined but unused, inline `style=` counts, loaded scripts. `--json` for machines, `--strict` as an acceptance gate (0 missing ids, 0 undefined classes). Added by the `158c836` review (`review/CODE_REVIEW_158c836.md` §4.2, plan P3). | Measuring the styling/facade consolidation; run it before and after UI changes. |
| `patches/runtime-fixes-vs-main-f32faf1.patch` | `git diff f32faf1 -- harness tests`: all runtime changes of PR #4 (memory/MCP fixes of Addendum A + flow-test fixes F-1…F-12 + the 4 new kernel tests). 22 files, +677/−96. | Porting the fixes onto another checkout: `git apply additions/patches/runtime-fixes-vs-main-f32faf1.patch`. Redundant once PR #4 is merged — kept for reuse elsewhere. |

Related material that stays where the review convention puts it:

| path | what it is |
|---|---|
| `review/dsh-integration/` | The integration pack for the build agent: `00_PRINCIPLES.md`, `plan.yaml` (P0 landed; WS-01 … WS-04 executed), `templates/PLUGIN_TEMPLATE.md`, specs WS-01 … WS-11. |
| `review/RESEARCH_DEEPSEEK_HARNESS.md` | Source-level comparison with DeepSeek Harness (`dsh`) and the ranked adoption list the pack is derived from. |
| `review/CODE_REVIEW_v0.2.1.md` (Addendum A) | Memory round-trip and MCP-through-the-kernel findings and fixes (first part of PR #4). |
| `tests/kernel/test_bus_delta_fanout.py`, `tests/kernel/test_thinking_block_roundtrip.py`, `tests/kernel/test_memory_roundtrip.py`, `tests/kernel/test_mcp_tool_through_kernel.py` | Kernel-level regression tests that fail on `f32faf1` and pass now. |

## Status in one screen (as of this PR)

**Fixed and proven (12 findings, details in `FLOW_TEST_FINDINGS.md`):**

| | sev | what a user hit | where fixed |
|---|---|---|---|
| F-2 | P0 | every Send → HTTP 500 (`submit_turn` rejected the client's payload) | `kernel/commands.py` |
| F-4 | P0 | nothing ever rendered (client listened for unnamed SSE frames only) | `web/static/events.js` |
| F-6 | P0 | streamed text repeated once per browser tab (`EventBus` mutated the shared event) | `kernel/bus.py` |
| F-7 | P0 | any thinking delta ended the turn in `turn.error` (`ThinkingBlock` field mismatch) | `kernel/runner.py`, strategies, anthropic provider |
| F-8 | P0 | Approve/Deny buttons did nothing (`approval_id` vs `request_id`) | `web/static/app.js`, `cards.js` |
| F-11 | P0 | page refresh showed the welcome screen, conversation gone | `web/static/app.js` |
| F-1 | P1 | runaway tool loop ended as a normal `end_turn` | `single_loop`, legacy loop → `max_iterations` |
| F-3 | P1 | client sent `session_id: null`, no provider | `web/static/app.js` |
| F-12 | P1 | tool cards said `ALLOWED` on denied calls, empty output, text out of order | `web/static/app.js`, `cards.js` |
| A.1 | P0 | memory never round-tripped (four databases) | `sdk/paths.py`, memory/search plugins |
| A.2 | P0 | no MCP tool could execute (handler signature) | `plugins/bridges/mcp_client` |

**Documented, deliberately not changed here (each belongs to a workstream):** F-5 phantom Ollama
models in the picker (WS-02), F-9 unsigned thinking re-send to Anthropic + F-10 thinking never persisted
(WS-04), diff cards (`ui.card`) not surviving a refresh (WS-04), `TurnResult.changed_files` never filled
by the default strategy (WS-07), no OS confinement for `run_command` (WS-06), the v0.2.1 placeholder
inventory in the settings UI (WS-05).

**Not integrated — only planned:** Obsidian (an MCP server configuration away, needs a real vault to
prove; the bridge was verified with a stdio server over a folder of `.md` notes) and every DeepSeek
Harness feature except WS-01 (catalogs, loop hygiene, session log as single source, feature-owned UI,
presets/sandbox, deliverables, `ask_user`, MCP-http, jobs/goals, subagents).

**Limits of the evidence:** every scenario replays a recorded model — no live provider has been
exercised; cancel-mid-turn, compaction, CLI, ACP bridge and the settings UI are outside the suite.

## Run it

```bash
python3 -m venv .venv && . .venv/bin/activate && pip install -q -e ".[dev]"
npm ci                                                                # jsdom lane, once (node >= 18; single root package.json)
export HOME=$(mktemp -d); export HARNESS_CONFIG_DIR=$HOME/.harness    # hermetic: never your real ~/.harness (two statements!)
pytest tests/scenarios -q                                             # 21 passed (~3 min, DOM lane included)
SCENARIO_DOM=0 pytest tests/scenarios -q                              # without node
pytest -q                                                             # everything: kernel, plugins, frontends, e2e, scenarios
```

`HARNESS_REPLAY=update` rewrites goldens — read the diff before committing one; a golden accepted
with a bug inside just freezes the bug.

## Use it later

1. **Adopt the suite:** done in consolidation P5 (`tests/scenarios`, in `pytest -q` and CI).
2. **Record from a real model:** wrap the configured provider in `scenarios/_lib/replay_provider.py::RecordingProvider`
   (refuses to write if a key value appears in the stream), replace `recorded_from: synthetic`.
3. **Start the workstreams** in `review/dsh-integration/plan.yaml` order; each spec's acceptance
   section names the scenarios it must add to this suite.
4. **Port the fixes elsewhere:** `git apply additions/patches/runtime-fixes-vs-main-f32faf1.patch`
   onto a checkout of `f32faf1`.
