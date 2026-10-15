# Consolidation log — executing the `158c836` review plan

**Scope.** Carry out P1–P6 from `review/CODE_REVIEW_158c836.md` §6, one phase per commit on the PR #5 branch
(`arena/cb0f051a-harness-zero`). After every phase the full gate runs: the hermetic unit/integration suite,
the plugin conformance suite, and the 20-scenario *flow-of-information* suite (record/replay through the
kernel and through the real web client in jsdom). Nothing is marked done on self-report; each phase lists the
commands and the numbers they produced.

**How to read this file.** §1 is the plan with acceptance criteria. §2 is the per-phase log (what changed,
what the flow test showed, what moved in the goldens and why). §3 collects defects found while executing
(numbered on from the review's R1–R7). §4 is the gate recipe.

---

## 1. Plan

| Phase | Root cause (review §4) | Deliverable | Acceptance |
|---|---|---|---|
| **P1** | 4.1 no payload contract; 4.7 ledger/spend bugs; R8 card shape mismatch | Typed payload dataclasses for every event; `EconomicsSnapshot` as the one spend shape; `cards.js` renders the SDK card shape; generalised static contract tests; bus guard in tests | `pytest tests -q` green with `event_contract_guard` on · `harness events schema \| diff docs/events.schema.json -` empty · 20 scenarios strict · Economics: session ≠ lifetime across sessions |
| **P2** ✔ | 4.4 browser-driven sessions | Kernel owns session lifecycle: lazy create on first `submit_turn`, resume on start; web `init()` no longer creates sessions; SSE before transcript reload; `sessions.list` rows = `SessionRow`; CLI follows the kernel's session too | 3 daemon restarts ⇒ 1 session (`tests/kernel/test_session_lifecycle.py`, scenario `daemon_restart_web_dom`) · 21 scenarios strict |
| **P3** | 4.2 two styling systems; 4.3 facade remnants | One styling system (compiled Tailwind vendored, CSP-safe) · delete hand-copied utilities + dead classes · artifact cards into the artifact pane · `tool.output.delta` into the tool card · delete terminal drawer and static pills | `python additions/tools/ui_audit.py --strict` exit 0 · `grep -c 'style="' app.js` = 0 · `pytest tests/frontends -q` |
| **P4** | 4.7 provider truth | `Provider.is_configured()` / `is_reachable()`; `models.list` never raises; Ollama short-timeout + cache; remove `_is_provider_configured` | unit tests for both paths · scenarios strict |
| **P5** | 4.5 tests in four places | `additions/scenarios` → `tests/scenarios`; delete `ui_smoke` duplicates; delete vacuous S1; CI runs scenarios | `pytest tests -q` includes the 20 scenarios · CI job green |
| **P6** | 4.6 version/doc drift | CHANGELOG restructure; `/health.version` from `harness.__version__`; README counts; `docs/ARCHITECTURE.md`; research docs under `docs/research/` | `harness --version` == `/health.version` == CHANGELOG top entry |

Order is deliberate: P1 first because every later phase (UI, sessions, providers) needs a stable wire contract to
test against; P5 after P3/P4 so the test move happens once; P6 last so the docs describe the final state.

---

## 2. Phase log

### P1 — Typed payload contract — **DONE** (this commit)

**Baseline** (`1ba296c`): ruff clean · 361 pytest · 20/20 scenarios · 180 conformance.

#### What changed

*SDK*
- `harness/sdk/events.py` rewritten. `EventPayload` base + `@event_payload` registry → `EVENT_PAYLOADS`
  (29 types; `KNOWN_EVENT_TYPES = frozenset(EVENT_PAYLOADS)`). One slots-dataclass per type with the field
  catalogue below. `Event.__post_init__` serialises a payload instance and raises `ValueError` when the
  payload's `EVENT_TYPE` ≠ `event.type`. `schema()` emits the envelope plus `$defs/<Dataclass>` (`Usage`,
  `ContextBlockInfo`), `$defs/payloads/<type>` and an `allOf`/`if`/`then` dispatch. `validate_event(event|dict)
  -> list[str]` checks required fields, leaf types (`float` accepts `int`), `Literal`s, nested dataclasses,
  undeclared keys, and — for `ui.card` — `Card.from_dict` validity.
- `harness/sdk/types.py`: `EconomicsSnapshot {turn_usd, session_usd, lifetime_usd, cap_usd, unknown_pricing, usage}`.
- `harness/sdk/cards.py`: `CARD_FIELDS` (kind → allowed `data` keys; `BUILTIN_CARD_KINDS` derives from it);
  `NoticeLevel` gains `success`; `validate_card` rejects undeclared keys for built-in kinds.
- `harness/sdk/contracts.py`: `CostLedger.record(..., session_id=None)`.
- `harness/sdk/__init__.py` exports `EventPayload`, `EVENT_PAYLOADS`, `validate_event`, `EconomicsSnapshot`,
  `CARD_FIELDS`, `BUILTIN_CARD_KINDS`.

*Kernel / plugins*
- `harness/kernel/economics.py` (new): `economics_snapshot(ledger, settings, session_id, turn_usd,
  turn_unknown_pricing)` — the single builder used by `Commands.economics` (→ `/health.spend`) and the runner's
  `cost.updated`.
- `runner.py`: `_calculate_cost` returns `None` for unknown pricing (was `0.0`); `turn_unknown_pricing` flag;
  ledger `record(session_id=…)`; `cost.updated = CostUpdated.from_snapshot(...)`; all `model.*`/`turn.*`
  emits typed.
- `gate.py` (tool.call/decision/result/output.delta, ui.card), `approvals.py`, `session.py`, `context.py`,
  `compaction.py`, `registry.py`, `inbox.py` (+ **`task.started`** now emitted on `register_task`),
  `commands.py` (`economics` → snapshot; `form.submit` emits a real `Card.notice(level="success")`;
  **`settings.changed {paths}`** emitted by `settings.set`), `plugins/bridges/shell_hooks/hook.py`
  (`ui.card` now wraps `{card: Card.notice(...).to_dict()}` — it was an unwrapped `{kind, payload}` blob),
  `plugins/stores/cost_ledger/ledger.py` (stores `session_id`; `totals(session_id)` filters),
  `plugins/tools/memory/tools.py` (`remember` no longer emits a notice card duplicating its result),
  `frontends/cli/app.py` (`/economics` panel reads the snapshot).

*Web client*
- `static/cards.js` rewritten (381 → 379 lines, but every line now reads a declared key): renders `Card{kind,id,title,data}` for the ten SDK kinds plus
  web-internal `tool` (= `tool.call` payload + `status`), `task`, `compaction`. No alias chains, no `plan` kind.
  `renderToolOutput(preview, is_error)` is the one place tool results are rendered (live and on reload).
- `static/app.js`: handlers read only contract keys (`turn.error.message`, `tool.decision.action`,
  `tool.result.preview/is_error`, `approval.*.approval_id/approved/by`, `cost.updated` snapshot keys,
  `ui.card.card`, `settings.changed.paths` → model list refresh); `appendNotice` builds a real notice card;
  `showApproval` builds an SDK `approval` card; `completeTask` replaces the running card by `data-task-id`;
  `reloadTranscript` reads `TranscriptRecord` (`role`, `tool_calls[{id,name,arguments}]`,
  `tool_results[{tool_use_id,content[],is_error}]`) and re-attaches tool outputs. `CSS.escape` avoided
  (absent in jsdom) via a `cssString()` helper.
- `static/events.js`: `log` removed; mirror checked by test.

*Tests*
- `tests/conftest.py::event_contract_guard` (autouse): wraps `EventBus.publish`; every event published from
  non-test code must pass `validate_event`. It immediately found one real mismatch (`approval.required.call_id`
  is `None` for strategy-level approvals) — contract adjusted to `str | None`.
- `tests/frontends/test_static_contract.py`: `test_event_handlers_read_only_contract_fields` (parses every
  `this.events.on(type, (data) => …)` handler, follows `this.helper(data)` one level, asserts `data.<key>` ⊆
  `$defs/payloads[type].properties`, nested `b.<key>` ⊆ `ContextBlockInfo`), `test_card_renderers_read_only_contract_fields`
  (each `render<Kind>(card, data)` reads `data.<key>` ⊆ `CARD_FIELDS[kind]`, `card.<key>` ⊆ `{id,title,kind}`,
  every SDK kind has a `case`, `events.js` list == `EVENT_PAYLOADS ∪ {heartbeat}`),
  `test_transcript_reload_reads_transcript_record_fields`, and the economics test now pins the snapshot shape
  on the command, the event and both renderers.
- `tests/e2e/test_scenarios_s1_s6.py` S2: snapshot keys; session totals isolated from an unknown session id;
  `lifetime_usd ≥ session_usd`. Shell-hooks S13 and card-validation tests updated to the new wire shape.
- Scenario suite (`additions/scenarios/test_scenarios.py`): every observed event (bus subscriber in the kernel
  lane, SSE frame in the web lane) is validated with `validate_event`. `read_then_edit_web_dom` now also
  expects tool outputs to be visible after a page refresh.

#### Payload catalogue (as implemented)

| Event | Payload class | Fields |
|---|---|---|
| session.started | SessionStarted | session_id, workspace, strategy, provider, model |
| session.switched | SessionSwitched | session_id, workspace |
| plugin.loaded | PluginLoaded | name, version, provides[], capabilities[], trust |
| plugin.error | PluginError | name, error |
| turn.started | TurnStarted | prompt_preview, strategy, provider, model, options{} |
| turn.completed | TurnCompleted | stop_reason, usage: Usage, duration_ms, tool_calls |
| turn.cancelled | TurnCancelled | reason |
| turn.error | TurnError | message, kind, retryable |
| context.assembled | ContextAssembled | blocks[ContextBlockInfo{id,stability,source,tokens}], total_tokens, history_messages |
| model.started | ModelStarted | provider, model, request_tokens_estimate |
| model.text.delta / model.thinking.delta | ModelTextDelta / ModelThinkingDelta | text |
| model.tool_call | ModelToolCall | call_id, name, arguments{} |
| model.notice | ModelNotice | message |
| model.completed | ModelCompleted | stop_reason, usage: Usage |
| tool.call | ToolCallStarted | call_id, name, arguments{}, risk, plugin |
| tool.decision | ToolDecision | call_id, action ∈ {allow,deny,ask}, policy, risk, reason |
| approval.required | ApprovalRequired | approval_id, call_id?, kind, summary, details{}, timeout_s?, options[] |
| approval.resolved | ApprovalResolved | approval_id, approved, by, reason? |
| tool.output.delta | ToolOutputDelta | call_id, stream, text |
| tool.result | ToolCallResult | call_id, name, is_error, duration_ms, preview, metadata{} |
| task.started | TaskStarted | task_id, label |
| task.completed | TaskCompleted | task_id, label, exit_code?, tail |
| compaction.started | CompactionStarted | estimated_tokens |
| compaction.completed | CompactionCompleted | summary_tokens, duration_ms |
| cost.updated | CostUpdated (= EconomicsSnapshot) | turn_usd, session_usd, lifetime_usd, cap_usd?, unknown_pricing, usage: Usage |
| ui.card | UiCard | card: Card.to_dict() |
| hook.fired | HookFired | hook, event, command, outcome{}, duration_ms |
| settings.changed | SettingsChanged | paths[] |

Dropped: `log` (never emitted anywhere). Added to the emitted set: `task.started`, `settings.changed`.

#### Flow-of-information test — what moved and why

Strict run against the `1ba296c` goldens failed on `events.json` in 17 scenarios. Diff summary (all 20 events
files, golden vs actual, by event type/key):

| Count | Event | Change | Verdict |
|---|---|---|---|
| 62 | `cost.updated` | `+lifetime_usd, +usage` | intended (snapshot shape) |
| 26 | `tool.call` | `arguments.file_path`: `<ws>/app.py` → `app.py` | **latent bug fixed** (R9 below): the event aliased the gate's mutable `current_args`, so the recorded value depended on when the subscriber drained the queue |
| 1 | `ui.card` (memory_remember_search_kernel) | notice card removed | intended (duplicate of the tool result) |

`timeline.txt` (jsdom DOM text) moved in the four `*_web_dom` scenarios. Before/after, abbreviated:

- `read_then_edit_web_dom`: `📝 File Diff` (empty body) → `📝 <ws>/app.py +2 -2 <<<<<<< SEARCH … >>>>>>> REPLACE` — the diff card now renders (R8).
- `memory_remember_web_dom`: table card previously absent → `▦ Memory Search: Falcon Step Source Snippet …`; duplicate "Permanently remembered…" gone.
- `approval_allow_web_dom`: `Approval Required: … Risk: HIGH` → `Approval Required: tool_execution · 300s to respond … Resolved: approved by user` (kind/timeout from the payload; resolution rendered — it was silently failing under `CSS.escape` in jsdom before the helper).
- `tirith_blocks_ssrf_web_dom`: `Output: Action denied…` → `Error: Action denied…` (`is_error` is now rendered as such).
- `read_then_edit_web_dom` after F5: tool cards come back **with outputs** (`view_file COMPLETED … Output: 1 | def greet(name): …`), previously bare names.

Goldens re-baselined once (`HARNESS_REPLAY=update`), then the strict run repeated.

#### Gate at end of P1

| Check | Result |
|---|---|
| `ruff check .` | clean |
| `pytest tests -q` (with `event_contract_guard`) | **364 passed** (361 + 3 new) |
| `harness plugin test --all` | **180 passed** |
| `pytest additions/scenarios -q` (strict, DOM lane on, every event validated) | **20 passed** |
| `harness events schema \| diff docs/events.schema.json -` | no diff |
| Session ≠ lifetime | `test_scenario_s2_configure_converse_usage`: foreign session id → `session_usd == 0`, `lifetime_usd` unchanged |

#### Not done in P1 (deliberately)
- `api.js` command-result typing beyond `sessions.get`/`economics`/`health.spend` — the remaining command
  results (`sessions.list` rows, `models.list`) change in P2/P4 and will be pinned there.
- Mixed known/unknown pricing in one session: `unknown_pricing` is true when the *current turn* lacked pricing or
  when the session has tokens but no known cost at all; a session mixing priced and unpriced models reports the
  priced sum without the flag. `CostLedger.totals()` returns a `Usage`, which has no room for the flag; revisit if
  a second provider without pricing is added.

### P2 — Kernel-side session lifecycle — **DONE**

**Baseline** (`ba74b5d`, end of P1): ruff clean · 364 pytest · 20/20 scenarios · 180 conformance.

#### The defect, as the flow test recorded it
Every web page load created a session (`init()` → `loadSessions()` → `createNewSession()` when nothing was
active) and the `/sessions` picker showed bare ids with no title, so the user's own conversation was
unreachable after a refresh or daemon restart. The scenario goldens had faithfully recorded this: each web
lane golden began with **two** `session.started` events — `session#1`, an empty boot session nobody ever
used, then `<session>`, the real one. The CLI had the same root cause from the other side: `CliApp` minted
its own `session_<uuid>` per process, so `harness run` / `harness interactive` never resumed anything and
the kernel's `active_session` was irrelevant to it.

#### What changed

*Kernel / SDK*
- `harness/sdk/types.py`: **`SessionRow`** — the one row shape of `sessions.list`: `{session_id, workspace,
  created_at, updated_at, title, turns, message_count, active, busy}`. Exported from `harness.sdk`.
- `harness/kernel/session.py`: `_summarize_transcript()` (one cheap pass over `transcript.jsonl`: record
  count, user-prompt count, first prompt as `title`, 60 chars + `…`); `list_sessions()` builds
  `SessionRow(...).to_dict()` sorted by `updated_at` desc (creation counts as activity, so a just-created
  empty session sorts first — intended, that is the one the user is looking at); **`resume_last()`** makes the
  most recently updated session *with at least one turn* active and publishes `session.switched` (a resume is
  not a start: no `SESSION_START` hooks, no `session.started`). Empty sessions are never resumed.
- `harness/kernel/bootstrap.py`: `bootstrap_kernel(resume_session=True)` calls `resume_last()`; pass `False`
  for an explicitly fresh start.
- `Commands.submit_turn` without `session_id` already used-or-created the active session (F-series fix); it is
  now the *only* way a frontend obtains a session implicitly, and the kernel announces it with `session.started`.

*Web client* (`static/app.js`)
- `init()` order is now health → settings → models/strategies → **SSE connect** → `loadSessions()` →
  `reloadTranscript()` if the kernel marked a session active, else an empty-timeline hint
  (`.timeline-empty`). The client never creates a session at boot. SSE is connected *before* the transcript is
  fetched so nothing emitted in between is lost (previously the stream was attached last).
- Composer submit sends `session_id: this.activeSessionId || undefined`; a new `session.started` handler adopts
  the id the kernel chose (when the client has none) and refreshes the picker; `session.switched` ignores
  same-id notifications. `#btn-new-session` remains the only explicit `sessions.create`.
- Picker rows render `SessionRow`: `.session-title` (title or "New session"), `.session-meta` with
  `.session-id`, `.session-turns`, `.session-when` (`updated_at`), `.session-busy`; `.session-empty` when
  there are none. (Styling of these classes lands in P3 with the rest of the stylesheet work.)

*CLI frontend* (`harness/frontends/cli/app.py`, `harness/cli.py`)
- `CliApp.session_id` starts as `None` and resolves to the kernel's active session (`_resolve_session_id()`);
  a turn submitted without an id adopts the session the kernel created. The banner says `(resumed)` or
  "none yet — created on your first prompt"; `/sessions` prints Title / Turns / Last activity and marks the
  kernel's `active` row; `/cancel` is a no-op without a session.
- `harness run` starts a **new** session per invocation (one-shot semantics) unless `--continue/-c`;
  `harness interactive` **resumes** unless `--new/-n`. Both are `bootstrap_kernel(resume_session=…)`.

*Flow test* (`additions/scenarios`)
- New scenario **`daemon_restart_web_dom`**: DOM lane sends two prompts with no session (first `submit_turn`
  carries `session_id: null`, the second carries the kernel-chosen id), then `web_runner.py`
  `_restart_daemon_and_reload()` stops the daemon, bootstraps a new kernel + server on the same workspace,
  reloads the page and asserts: `/health.active_session` is that session, `sessions.list` is one row
  `{title: "Question one", turns: 2, active: true}`, and the timeline shows all four texts on load without a
  click. Expectation keys `dom_restart`, `restart_timeline_contains`, `sessions_after_restart`.
- Goldens re-baselined for the seven web scenarios whose events began with the phantom boot session
  (`approval_allow_web_dom`, `memory_remember_web_dom`, `plain_answer_web`, `read_then_edit_web_dom`,
  `session_reload_web_dom`, `thinking_then_answer_web`, `tirith_blocks_ssrf_web_dom`): each diff is exactly one
  `session.started` (`session#1`) removed, nothing else (checked per scenario with a type-count diff against
  `HEAD`). `tirith_blocks_ssrf_web_dom` was order-dependent before (passed in one strict run, failed the next);
  with no boot session the capture is deterministic (3/3 strict re-runs).

*Tests*
- `tests/kernel/test_session_lifecycle.py` (4): three bootstraps on one workspace ⇒ one session, lazily created
  with `session.started`, resumed twice with `session.switched` and the transcript intact; resume skips empty
  sessions and `resume_session=False` resumes nothing; `sessions.list` keys == `fields(SessionRow)` and title
  truncation; CLI frontend across two "processes" ⇒ one session and the second model call sees the first
  exchange (`ScriptedProvider.assert_requested(contains_text=…)`).
- `tests/frontends/test_static_contract.py::test_session_picker_reads_session_row_fields_and_boot_creates_no_session`:
  `loadSessions()` reads only `SessionRow` fields; `init()` contains no `createNewSession`; the
  `session.started` handler exists and adopts `session_id`.

#### Gate (all hermetic, `HOME`/`HARNESS_CONFIG_DIR` in a temp dir)

| Check | Result |
|---|---|
| `ruff check .` | clean |
| `harness events schema \| diff docs/events.schema.json -` | no diff |
| `pytest tests -q` (with `event_contract_guard`) | **369 passed** (364 + 4 lifecycle + 1 contract) |
| `harness plugin test --all` | **180 passed** |
| `pytest additions/scenarios -q` (strict, DOM lane on) | **21 passed** (20 + `daemon_restart_web_dom`) |
| Acceptance "3 restarts ⇒ 1 session" | `test_three_restarts_leave_one_session_and_resume_it`; manually: `harness run` ×2 → 2 sessions, `harness run -c` → joins newest (turns 2), next start resumes it, `interactive --new` starts clean |

#### Not done in P2 (deliberately)
- The picker classes (`.session-title/.session-meta/.session-empty/.timeline-empty`) are unstyled until P3,
  which replaces the stylesheet wholesale; adding rules to the current `styles.css` would be deleted a phase later.
- No "resume a specific session" CLI flag: `/switch <id>` and the web picker already cover it.
- `sessions_index.jsonl` (written by `Session`) is not read by `list_sessions()`; the directory scan is the
  source of truth. Dropping or using the index is a P6 hygiene item.
### P3 — One styling system, no facade — pending
### P4 — Provider truth — pending
### P5 — One test estate — pending
### P6 — Version / doc hygiene — pending

---

## 3. Defects found while executing (continuing the review's numbering)

| # | Where | What | Status |
|---|---|---|---|
| R8 | `static/cards.js` vs `sdk/cards.py` | Renderer read `{kind, payload}` with keys `file/diff/headers/message/filename/…`; the SDK emits `{kind, id, title, data}` with `path/unified/columns/body/name/…`. Every plugin card (diff, table, notice, markdown, artifact) rendered empty or as the fallback. | fixed in P1 |
| R9 | `kernel/gate.py` → bus | `tool.call.data.arguments` was the live `current_args` dict; hooks/policies mutate it afterwards, so subscribers saw different values depending on drain timing (`<ws>/app.py` vs `app.py` in the recorded goldens). | fixed in P1 (payload serialisation snapshots) |
| R10 | `approvals.py` | `approval.required.call_id` is `None` for strategy-level approvals; the UI assumed a string. | contract now `str \| None` |
| R11 | `plugins/tools/memory` | `remember` returned the confirmation both as result text and as a notice card → shown twice once cards rendered. | card removed |
| R12 | `static/app.js` | `CSS.escape` is not available in jsdom; `resolveApprovalUI`/`completeTask` threw there, so the DOM lane could never see "Resolved: …". | `cssString()` helper |
| R13 | `bridge-shell-hooks` | `ui.card` emitted `{kind, payload}` directly as `data` (no `card` wrapper), so the client's `data.card` was undefined. | fixed |

---

## 4. Gate recipe (run after every phase)

```bash
python3 -m venv .venv && . .venv/bin/activate && pip install -q -e ".[dev]"
export HOME=$(mktemp -d); export HARNESS_CONFIG_DIR=$HOME/.harness; mkdir -p "$HARNESS_CONFIG_DIR"   # two statements: $HOME must be re-evaluated
ruff check .
python -m pytest tests -q                                   # hermetic suite (+ event_contract_guard)
python -m harness.cli plugin test --all                     # plugin conformance
python -m harness.cli events schema | diff docs/events.schema.json -
npm --prefix additions/scenarios/_lib/node ci --silent      # jsdom lane deps (once per checkout)
SCENARIO_DOM_WAIT_MS=6000 python -m pytest additions/scenarios -q          # flow-of-information, strict
# only when a change is *meant* to move the wire: re-baseline once, inspect `git diff additions/scenarios/*/golden`, then strict again
HARNESS_REPLAY=update SCENARIO_DOM_WAIT_MS=6000 python -m pytest additions/scenarios -q
```
