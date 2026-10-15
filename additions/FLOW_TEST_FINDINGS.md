# Flow-of-information test — findings and fixes

Executed against branch `arena/99a86436-harness-zero` (base `main @ f32faf1`, v0.2.1 + PR #4 Part A).
Method: the record/replay scenarios in [`scenarios/`](scenarios/README.md) push a prompt through the
real kernel → real web daemon → the real shipped browser client (jsdom), with the model replaced by
a recording. Every number below is reproducible with the commands in §5.

**Headline:** before this pass the shipped web client could not complete a single turn end to end
(F-2, F-4), rendered streamed text duplicated once per connected subscriber (F-6), crashed any turn
whose model emitted thinking (F-7) and could not grant an approval from the UI (F-8). A second pass
over the remaining subsystems (skills, memory, diff/checkpoints, auth, guardrails) found that a page
refresh lost the conversation (F-11) and that tool cards reported every denied call as `ALLOWED` with
an empty output (F-12). All seven are fixed with minimal, enumerated changes to `harness/` (§3) and
each has a regression scenario or test. Three lower-severity findings are documented but
intentionally left for the owning workstream (§2, F-5, F-9, F-10). §6 maps each subsystem the
harness is meant to provide to what the suite actually exercised.

## 1. Result

| scenario | lane | before | after |
|---|---|---|---|
| plain_answer_kernel | kernel | pass | pass |
| read_then_edit_kernel | kernel | pass | pass |
| approval_allow_kernel | kernel | pass | pass |
| approval_deny_no_approver_kernel | kernel | pass | pass |
| memory_remember_search_kernel | kernel | pass | pass |
| mcp_echo_kernel | kernel | pass | pass |
| runaway_tool_loop_kernel | kernel | pass, but `stop_reason=end_turn` after 20 forced tool calls (F-1) | pass, `stop_reason=max_iterations` |
| plain_answer_web | web + DOM | **fail** — HTTP 500 on submit (F-2); after F-2: nothing rendered (F-4); after F-4: text rendered 3× (F-6) | pass — timeline `What is the capital of the Netherlands? The capital of the Netherlands is Amsterdam.` |
| thinking_then_answer_web | web + DOM | **fail** — `turn.error ThinkingBlock.__init__() got an unexpected keyword argument 'thinking'` (F-7) | pass — `Reasoning Process … Forty-two.`, `turn.completed` |
| approval_allow_web_dom | web + DOM | **fail** — approval card rendered, Approve click a no-op, `notes.txt` never written (F-8) | pass — `POST /commands/resolve_approval -> 200`, file written, card shows `Resolved: approved` |
| session_resume_web | web (API) | pass (after F-2) | pass |
| skills_view_kernel | kernel | pass | pass — catalog in system prompt, `view_skill` returns the playbook |
| jail_blocks_traversal_kernel | kernel | pass | pass — read/write outside the workspace denied by `workspace_jail` |
| shell_exec_denied_without_approver_kernel | kernel | pass | pass — `run_command` in `ask` mode fails closed |
| secret_redacted_from_model_kernel | kernel | pass | pass — stored secret and `sk-…` pattern never reach the model |
| tirith_blocks_ssrf_kernel | kernel | pass | pass — metadata-endpoint curl denied by `tirith` |
| tirith_blocks_ssrf_web_dom | web + DOM | **fail** — card showed `ALLOWED` for the denied call (F-12) | pass — `DENIED` + reason |
| memory_remember_web_dom | web + DOM | pass, but text of both model calls merged above the cards (F-12) | pass — `Noting that. ⚡ remember … Saved.` in order |
| read_then_edit_web_dom | web + DOM | pass, but `Output:` empty, order wrong (F-12) | pass — outputs, `📝 File Diff`, correct order |
| session_reload_web_dom | web + DOM | **fail** — after refresh the welcome screen, history gone (F-11) | pass — both turns visible with no click |

Full run: `20 passed in 151 s`. Repo suite after the fixes: `pytest tests -q` → **357 passed** (353 existing + 4 new).
Every web scenario also proves auth fails closed: `POST /commands/*`, `POST /auth/ticket`, `GET /events` and
`GET /events?ticket=bogus` without credentials → `401` (`http.json` golden, entry `unauthenticated`).

## 2. Findings

Severity: **P0** = a user-visible flow cannot complete; **P1** = wrong/misleading output; **P2** = quality.

### F-2 · P0 · `submit_turn` rejects the client's own payload — FIXED
- **Symptom.** The shipped client posts `{prompt, session_id, model}` (`api.js:107`). `Commands.submit_turn(session_id, prompt, media, options)` (`kernel/commands.py:63`) has no `model` parameter → `TypeError` → HTTP 500 on every send. On a fresh daemon the client also sends `session_id: null` → `Invalid session_id: None`.
- **Evidence.** `scenarios/plain_answer_web/_last_run/summary.json` before the fix: `fetch_log: ['POST /commands/submit_turn -> 500']`.
- **Fix.** `kernel/commands.py`: `session_id` optional (falls back to the active session or creates one), `model`/`provider`/`strategy` accepted top-level and folded into `options`, empty prompt → `HarnessError` (400) instead of a 500.
- **Test.** `scenarios/plain_answer_web`, `session_resume_web` (`http.json` golden pins the payload shape).

### F-4 · P0 · SSE listener only handles unnamed frames — nothing ever renders — FIXED
- **Symptom.** The daemon sends named frames (`event: turn.started` …). `events.js:61` registered only `eventSource.onmessage`, which per the EventSource spec never fires for named events. The client connected, the kernel ran the turn, and the timeline stayed empty forever.
- **Evidence.** DOM lane before the fix: `sse_events: []`, `turns[0].completed: false` while the API lane saw all 8 frames (`extra.named_sse_frames: 8`).
- **Fix.** `events.js`: `_attachNamedListener(type)` adds `addEventListener(type, …)` for every subscribed type at connect time and in `on()`; shared `_onFrame(e, namedType)` parser; `onmessage` kept for unnamed frames.
- **Test.** `dom_turns_completed: true` in all three DOM scenarios.

### F-6 · P0 · EventBus delta coalescing mutates the shared Event → text duplicated once per subscriber — FIXED
- **Symptom.** `EventBus.publish` (`kernel/bus.py:118`) merged consecutive deltas by writing into `sub._last_delta.data["text"]`, but `_last_delta` *was the same Event object* queued to every subscriber. With N subscribers (the web server's replay consumer `server.py:508` + each browser tab) every later chunk is appended N times. One browser tab ⇒ `…is Amsterdam.is Amsterdam.is Amsterdam.` Kernel-only tests never saw it because they have one subscriber.
- **Evidence.** `plain_answer_web` timeline after F-4: `The capital of the Netherlands is Amsterdam.is Amsterdam.is Amsterdam.`; a single `model.text.delta` frame on the wire already carried the duplicated text, proving it was upstream of SSE.
- **Fix.** `kernel/bus.py`: each subscriber gets its own copy of a delta event (`Event(..., data=dict(event.data))`), the loop re-reads the original per subscriber, and coalescing additionally requires the same `turn_id`.
- **Test.** `tests/kernel/test_bus_delta_fanout.py` (3 tests; fails on the old code with the exact duplicated string).

### F-7 · P0 · Any thinking delta crashes the turn — FIXED
- **Symptom.** `TurnRunner` built `ThinkingBlock(thinking=…)` (`kernel/runner.py:510`) but the dataclass field is `text` (`sdk/types.py:61`); every consumer (`runner.py:87`, `strategies/single_loop/strategy.py:68`, `strategies/architect_editor/strategy.py:92`, `providers/anthropic/provider.py:204`) read `block.thinking`. Net: any provider that streams reasoning (Anthropic extended thinking, DeepSeek-R1 via Ollama, o-series) ends every turn in `turn.error`.
- **Evidence.** `thinking_then_answer_web` before: SSE `… model.thinking.delta, model.text.delta, cost.updated, model.completed, turn.error`; timeline ends with `Turn execution error`.
- **Fix.** Constructor and the four readers use `.text`. (The golden for this scenario had been accepted with the `turn.error` inside — now `event_types_exclude: [turn.error]` makes that impossible; see README.)
- **Test.** `tests/kernel/test_thinking_block_roundtrip.py` (turn completes, `TurnResult.thinking` populated, reasoning never shown back to the model as assistant prose).

### F-8 · P0 · Approve/Deny buttons are no-ops — FIXED
- **Symptom.** Kernel approval events carry `approval_id` (`kernel/approvals.py:92,117,152`); the client read `request_id` (`app.js:659,664`, `cards.js:279`). The card rendered with `data-request-id=""`, and the click handlers are guarded by `if (reqId)` (`app.js:284,293`) → no request was ever sent. In `permission_mode: ask` **no tool with risk ≥ write can ever run from the web UI**.
- **Evidence.** `approval_allow_web_dom` before: `approved: 1` (click happened), `fetch_log` without any `resolve_approval`, `notes.txt missing`, timeline stuck on `Approval Required`.
- **Fix.** `app.js` `showApproval`/`resolveApprovalUI` and `cards.js` `renderApproval` read `approval_id || request_id || id`; the resolved badge understands the kernel's `{approved: bool}` payload (it has no `decision` field); the card title prefers the kernel's `summary`. The server side (`commands.resolve_approval`, accepts `request_id`/`decision`) needed no change.
- **Test.** `approval_allow_web_dom` (`dom_approves: true`: jsdom clicks the real button).

### F-1 · P1 · Iteration cap exhausted silently — FIXED
- **Symptom.** `single_loop` and the legacy loop in `runner.py` stop after `max_iterations` (20) with `stop_reason="end_turn"`; a runaway tool loop looks like a normal answer in the event log, the API response and the UI.
- **Fix.** `for … else:` sets `stop_reason="max_iterations"` in both loops; no other behaviour changed. The client does not yet surface `stop_reason` — that belongs with WS-03 (loop hygiene), which should also add the `system.notice`.
- **Test.** `runaway_tool_loop_kernel` expects `stop_reasons: [max_iterations]`; its `events.json` golden pins the value.

### F-3 · P1 · Client sends `session_id: null` and no `provider` — FIXED (client side)
- `app.js` submit now creates a session first when none is active (so the sidebar and `/health` agree with the kernel) and passes the selected model's `provider` (from `data-provider` set in `populateModels`) so two providers advertising the same model id cannot be confused. With F-2 the server tolerates the old payload as well.

### F-11 · P0 · A page refresh loses the conversation — FIXED
- **Symptom.** `init()` (`app.js:28-60`) sets `activeSessionId` from `/health` and lists sessions, but never calls `reloadTranscript()`; the timeline shows the static welcome screen. Clicking the active session in the sidebar does nothing either (`switchSession` returns early for the current id, `app.js:890`). The only way to see the history again was to create another session and switch back.
- **Evidence.** `session_reload_web_dom` before: `active_session_on_load=session_…`, `timeline_text_on_load='Welcome to Harness-Zero…'`, `timeline_text_after_click` identical, while `reloadTranscript()` invoked directly rendered both turns — wiring, not rendering, was missing.
- **Fix.** `app.js`: `init()` re-renders the active session; `switchSession` on the active id re-renders; `reloadTranscript()` skips tool-result-only user records (they rendered as empty bubbles) and renders assistant `tool_calls` as tool cards so a refreshed page shows what happened. (`ui.card` payloads such as the diff card are still not persisted — WS-04, unchanged.)
- **Test.** `session_reload_web_dom`, `read_then_edit_web_dom`, `approval_allow_web_dom` (`dom_reload: true`, `reload_timeline_contains`, zero empty bubbles).

### F-12 · P1 · Tool cards contradict the kernel: `ALLOWED` on denied calls, empty output, text out of order — FIXED
- **Symptom.** (a) `updateToolDecision` read `data.decision` with uppercase values, but `tool.decision` carries `{action: allow|deny|ask, policy, reason, risk}` (`kernel/gate.py:395,483`) → the badge said `ALLOWED` for every call, including ones the jail or Tirith denied. (b) `updateToolResult` read `data.result`, but `tool.result` carries `{preview, is_error, metadata}` (`gate.py:313-346`) → `Output:` was always empty; and the badge only advanced from `RUNNING`, which it never was. (c) Text from the model call *after* a tool went into the block opened at `turn.started`, so the timeline read `Noting that.Saved. ⚡ remember` instead of `Noting that. ⚡ remember Saved.`
- **Evidence.** `tirith_blocks_ssrf_web_dom` timeline before: `⚡ run_command c1 ALLOWED … Output:` (empty) although `tool.decision.action=deny`; `memory_remember_web_dom` / `read_then_edit_web_dom` live timelines vs their reload timelines (order correct only after reload).
- **Fix.** `app.js`: decision badge from `action` (DENIED red with the reason as tooltip, APPROVAL REQUIRED amber, ALLOWED); result from `preview`, badge ends in COMPLETED/ERROR; on `tool.call` the open text block is finalised so the next model text starts below the card. `cards.js`: `tool-output` marker class so the result block is detected reliably.
- **Test.** `tirith_blocks_ssrf_web_dom` (`DENIED`, `timeline_not_contains: [ALLOWED]`), `read_then_edit_web_dom` / `memory_remember_web_dom` (`timeline_order`, `COMPLETED`, output text).

### F-5 · P2 · `models.list` advertises models that cannot run — NOT FIXED (WS-02 / provider onboarding)
- `OllamaProvider.list_models` falls back to the static `DEFAULT_OLLAMA_MODELS` when `/api/tags` is unreachable (`plugins/providers/ollama/provider.py:104-138`), and `Commands.models_list` (`kernel/commands.py:271`) has no availability flag. A fresh install with no Ollama and no keys shows a populated picker; the first send fails late with a network error. Recommended: `available: bool, reason: str` per entry, decided by the provider (reachable / key present), consumed by `populateModels`.

### F-9 · P2 · Anthropic re-send of thinking blocks lacks `signature` — NOT VERIFIED (no network)
- `providers/anthropic/provider.py:201-205` echoes `{"type": "thinking", "thinking": …}` without the `signature` the API requires for assistant thinking blocks. Today unreachable because of F-10; becomes live the moment thinking is persisted. Fix together with F-10: include `signature` when present, drop unsigned blocks.

### F-10 · P2 · Thinking is never persisted — NOT FIXED (WS-04 session log)
- `KernelServicesImpl.record` (`kernel/runner.py:561`) writes `content=msg.text()` and never fills `TranscriptRecord.thinking`, so `records_to_messages` (`kernel/context.py:128`) can never rebuild a `ThinkingBlock`, and a reloaded session loses the "Reasoning Process" card the live view showed (reload ≠ live, principle P1). Deliberately not changed here because persisting it would start re-sending unsigned thinking to Anthropic (F-9); do both in WS-04.

## 3. Files changed in `harness/` and `tests/` (keep this list in the PR description)

| file | change | finding |
|---|---|---|
| `harness/kernel/commands.py` | `submit_turn`: optional `session_id`, top-level `model`/`provider`/`strategy` → options, empty prompt → `HarnessError` | F-2 |
| `harness/kernel/bus.py` | per-subscriber delta copies; `same_turn` guard | F-6 |
| `harness/kernel/runner.py` | `ThinkingBlock(text=…)`; `.text` reader; `for…else → max_iterations` (legacy loop) | F-7, F-1 |
| `harness/plugins/strategies/single_loop/strategy.py` | `.text` reader; `for…else → max_iterations` | F-7, F-1 |
| `harness/plugins/strategies/architect_editor/strategy.py` | `.text` reader | F-7 |
| `harness/plugins/providers/anthropic/provider.py` | `.text` reader | F-7 |
| `harness/frontends/web/static/events.js` | named SSE listeners | F-4 |
| `harness/frontends/web/static/app.js` | create session before send, send `provider`; approval id/outcome fields; re-render history on load / active-session click, skip empty tool-result records, render recorded tool calls; decision badge from `action`, result from `preview`, finalise text block on `tool.call` | F-3, F-8, F-11, F-12 |
| `harness/frontends/web/static/cards.js` | approval id/summary fields; `tool-output` marker | F-8, F-12 |
| `tests/kernel/test_bus_delta_fanout.py` | new | F-6 |
| `tests/kernel/test_thinking_block_roundtrip.py` | new | F-7 |

`git diff --stat harness/` against `8c4e0d9`: 9 files, ≈ +170 / −45. No public signature was narrowed; `submit_turn` only became more permissive. No kernel event shape was changed — the client was aligned to the kernel, not the other way round.

## 4. Corrections to the integration pack

- **WS-01** is no longer "specified": `scenarios/` is a working implementation of its §1 outcome (replay provider, three lanes, goldens, strict exhaustion, hermetic env). Remaining for the workstream: `HARNESS_REPLAY=record` CLI wiring, `git mv` into `tests/scenarios`, CI job with node.
- **WS-01 §2** says ScriptedProvider silently pads exhausted recordings — confirmed again (`sdk/testing/scripted.py` `generate`); `ReplayProvider(strict=True)` is the replacement.
- **WS-07 §3.2** asks for a *new* `ctx.register_command`. The kernel half already exists: `Commands.register_command(name, handler)` (`kernel/commands.py:59`, used by the command dispatcher). Only the `PluginContext` wrapper (namespacing, catalog entry) is new; the spec should delegate to it rather than add a second registry.
- **plan.yaml** dependency `§8 critical path merged (UI completes a turn)` is satisfied by F-2/F-4/F-6/F-8 on this branch — the "UI never completed a turn" blocker from the v0.2.1 review is closed with a test that proves it (`plain_answer_web`, DOM lane).

## 5. What the suite does and does not prove (per subsystem)

| subsystem | exercised by | verified | **not** verified (and where it belongs) |
|---|---|---|---|
| Plugin system | every scenario boots the real registry from `harness/plugins` (12 tools visible to the model in `requests.json`: `view_file … view_skill … checkpoint_restore`; 6 context sources, 4 policies, 1 hook, 4 stores, 2 strategies, MCP bridge) | manifests load, tools reach the gate, context sources reach the prompt, hooks fire | third-party plugin loading from `~/.harness/plugins`, `plugin doctor`, settings schemas per plugin (WS-02/WS-05), plugin teardown on daemon stop |
| Skills (progressive disclosure) | `skills_view_kernel` | catalog block in the system prompt (`system_sections` in the golden), `view_skill` returns `SKILL.md` to the model + a card | workspace-level `skills/` override precedence, `builtin/skills` location when installed as a wheel (`parents[4]` path, `skills_catalog/source.py:56`) |
| Memory | `memory_remember_search_kernel`, `memory_remember_web_dom`, `tests/kernel/test_memory_roundtrip.py` | remember → search in the same session and across a restart, context injection, one `memory.db`, cards in the UI | FTS ranking quality, memory pruning, compaction interplay |
| Code diff / checkpoints | `read_then_edit_kernel` (`.harness/checkpoints/.git/HEAD` exists after `edit_file`), `read_then_edit_web_dom` (`📝 File Diff` card renders) | shadow-repo snapshot before a mutating tool, diff card live | `checkpoint_restore` round-trip, diff card after refresh (`ui.card` not persisted — WS-04), per-turn changed-files summary (`TurnResult.changed_files` is only filled by `architect_editor`, never by `single_loop` — WS-07) |
| Auth | every web scenario | bearer token on `/commands/*`, ticket flow for SSE, four unauthenticated paths → 401 | CSP/headers, token rotation, CORS from a foreign origin (covered by `tests/frontends` and the v0.2.1 review, not re-run here) |
| Guardrails | `approval_*`, `shell_exec_denied_without_approver_kernel`, `jail_blocks_traversal_kernel`, `tirith_blocks_ssrf_*`, `secret_redacted_from_model_kernel`, `runaway_tool_loop_kernel` | permission modes allow/ask, approve/deny from the UI, fail-closed without approver, path jail (read + write), Tirith SSRF block, secret redaction (store value + pattern), iteration cap | `run_command` confinement beyond pattern matching (no OS sandbox — WS-06), cost/budget limits, tool timeout partial output (WS-03), Tirith on non-shell tools |
| Session / transcript | `session_resume_web`, `session_reload_web_dom`, `reload_equals_live` on kernel scenarios | history identical live vs reloaded, page refresh renders it, tool calls restored | cancel/stop mid-turn (replay streams instantly; needs a paced recording), compaction summaries, concurrent sessions |
| MCP | `mcp_echo_kernel`, `tests/kernel/test_mcp_tool_through_kernel.py` | stdio server tool through the gate with hints → risk | streamable-http transport, reconnect, cancel (WS-09); a real Obsidian MCP server was **not** run — only a stdio server over a folder of `.md` notes |
| Providers | replay provider only | kernel/provider contract (`Provider` protocol, streaming events) | any real provider; Anthropic thinking re-send (F-9), Ollama phantom models (F-5) |

Not part of the suite at all: CLI frontend, ACP bridge, settings UI, artifacts preview sandbox, background tasks/`manage_task`, `search_codebase`, `take_screenshot`.

## 6. Reproduce

```bash
python3 -m venv .venv && . .venv/bin/activate && pip install -q -e ".[dev]"
npm --prefix additions/scenarios/_lib/node ci          # jsdom lane, once
export HOME=$(mktemp -d) HARNESS_CONFIG_DIR=$HOME/.harness           # hermetic
pytest additions/scenarios -q                            # 20 passed
pytest tests -q                                                       # 357 passed
```

To see any P0 again, revert one file and run its scenario — e.g. `git stash push -- harness/kernel/bus.py`
then `pytest additions/scenarios -q -k plain_answer_web` reproduces the triplicated text.
