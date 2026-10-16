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
| **P3** ✔ | 4.2 two styling systems; 4.3 facade remnants | One styling system: `styles.css` rewritten (components only) + compiled Tailwind `vendor/tw.css` (CSP-safe, committed, CI-checked) · artifact cards into the Artifacts tab · `tool.output.delta` into the tool card · terminal drawer, slash menu and every static pill removed or made live · `.hidden` finally defined (R14) | `python additions/tools/ui_audit.py --strict` exit 0 · `grep -c 'style="' app.js` = 0 · `tests/frontends/test_ui_coherence.py` (audit + jsdom cascade) · 21 scenarios strict |
| **P4** ✔ | 4.7 provider truth | `Provider.is_configured()` / `is_reachable()` in the SDK contract, implemented by all five providers + `ScriptedProvider`; `models.list` → `ModelRow` with `status` ready / needs_key / unreachable, concurrent probes, never raises, DEBUG only; Ollama 300 ms probe cached 30 s; `_is_provider_configured` deleted; registry rejects providers that skip the contract; picker groups by status | `tests/plugins/test_provider_readiness.py` (hermetic bootstrapped `models.list`) · `test_ollama.py` probe tests · contract tests · 21 scenarios strict |
| **P5** ✔ | 4.5 tests in four places | `additions/scenarios` → `tests/scenarios` (in plain `pytest -q`); `ui_audit.py` → `tests/frontends/`; both `ui_smoke.mjs` + `test_ui_smoke.py` deleted, their unique checks absorbed by the DOM lane; vacuous S1 deleted; one `package.json` (jsdom + tailwindcss, `npm ci`); CI runs everything with `SCENARIO_DOM_WAIT_MS=6000` | `pytest -q` → 406 in one invocation (incl. 21 scenarios) · `find . -name package.json -not -path '*/node_modules/*' \| wc -l` = 1 · no `ui_smoke*` |
| **P7** ✔ | WS-02 (dsh pack): generated catalogues + freshness gates | `harness catalog gen/--check/readme`, `docs/catalog/*` generated from a booted kernel, settings ownership moved into plugin `settings.schema.json` files, `Commands.settings_schema` reduced to an aggregation | `harness catalog --check` exit 0 · `tests/kernel/test_settings_declared.py` + `tests/kernel/test_catalog.py` in `pytest -q` · 21 scenarios strict |
| **P6** ✔ | 4.6 version/doc drift | Released CHANGELOG sections restored to their tags and SHA-pinned; `/health.version` = `harness.__version__`; `BUILTIN_COMMANDS` as the one command catalogue; dead `sessions_index.jsonl` + `TranscriptStore.list_sessions` removed; `docs/ARCHITECTURE.md` written from the code and pinned by `tests/test_docs_and_versions.py`; the 22 research syntheses moved to `docs/research/`; README counts replaced by the real gate; `TurnInProgress` → 409 over HTTP; path-length-dependent `request_tokens_estimate` dropped from goldens (R31) | `tests/test_docs_and_versions.py` (version = pyproject = doc title; every command/event/card documented; released sections immutable) · `pytest -q` → 411 · 21 scenarios strict |

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
### P3 — One styling system, no facade — **DONE**

**Baseline** (`330fcaa`, end of P2): ruff clean · 369 pytest · 21/21 scenarios · 180 conformance.
`ui_audit.py` at the baseline: 217 classes defined in `styles.css`, **106 used-but-undefined**, **71
defined-but-unused**, **37 inline `style=` attributes** (index.html 19, app.js 17, cards.js 1), **12 ids in
index.html nothing referenced**, stylesheet `styles.css` only.

#### What the audit had not caught (found while rewriting; evidence in `tests/frontends/test_ui_coherence.py`)
- **R14 — the page had no `.hidden` rule.** The templates were written against Tailwind (`hidden`,
  `truncate`, `uppercase`, …), the CDN was dropped for the CSP, and `styles.css` only had
  `.context-inspector.hidden`. jsdom cascade at the baseline: `#approval-banner`, `#onboarding-banner`,
  `#proceed-banner`, `#session-list`, `#economics-view` and `#btn-stop` all computed `display: flex|block` on
  load — the amber "High-Risk Action Approval Requested" bar, the indigo "AWAITING APPROVAL Architecture Plan"
  banner, the Stop button and three studio views were visible at once. The settings modal only closed because
  `closeSettings()` also set `style.display = 'none'`.
- **R15 — undefined design tokens.** The second half of `styles.css` used `var(--border-color)`,
  `var(--bg-secondary)`, `var(--text-primary)`, `var(--accent-hover)`; none existed in `:root`, so the modal
  dialog background resolved to nothing and the inspector/terminal borders never rendered.
- **R16 — two stylesheets concatenated into one file**: `.btn-send`, `.btn-stop`, `.composer-input`,
  `.modal-dialog`, `.btn-icon`, `.diff-line`, `.proceed-banner`, `.settings-tab-btn`, `.banner-actions` were
  each defined twice with different values; the later (broken, R15) copy won.
- **R17 — slash menu facade**: typing `/` offered `/plan /diff /compact /pytest /git`; no kernel command
  interprets them in web prompts (`Commands` has no `compact`), so they were sent to the model as text.
- **R18 — tool output went nowhere**: `tool.output.delta` was appended to `#terminal-body` inside a drawer
  that was `display:none` and had no code path to open it.
- **R19 — static pills**: `🪝 main`, `Autonomous`, `Sovereign Daemon Online`, `● Ready`, `UTF-8`,
  `Autonomous Heuristic`, `Cache Tier: Invariant`, a placeholder "No active artifact generated yet" and a
  hard-coded welcome turn in the timeline.

#### Decision: (a) compiled Tailwind, vendored
`cards.js` and `settings.js` are written in utilities and the card templates are the part of the UI that
works, so the smaller and safer change is to give those utilities a real definition rather than rewrite the
templates. The rule is now: **components in `styles.css`, utilities from `vendor/tw.css`, never both.**

- `tailwind.config.cjs` (repo root): `content = static/index.html + static/*.js`, `preflight: false` (the
  reset lives in `styles.css` and includes the one preflight rule utilities depend on — `border-style:
  solid; border-width: 0` on every element), compact type scale (`text-xs` 11px … `text-lg` 16px) so the
  dense layout keeps its proportions, `font-mono`/`font-sans` bound to the `--font-*` tokens.
- `package.json`: `tailwindcss@3.4.17` (exact) as a devDependency; `npm run build:css` writes
  `harness/frontends/web/static/vendor/tw.css` (560 lines, 9 KB, 91 utilities, unminified so diffs are
  readable); `npm run check:css` compares. **CI** rebuilds and `git diff --exit-code`s the file, then runs
  `ui_audit.py --strict`.
- `index.html` links `styles.css` then `vendor/tw.css` — in that order, so `.hidden` beats a component's
  `display: flex` by cascade order alone (no `!important`); the audit enforces the order.

#### What changed
*`styles.css`* — rewritten (1801 → 1294 lines, 217 → 165 classes, every one used). Sections: tokens,
reset, frame (title bar / rail / panes / status bar), conversation (turns, thinking, prose, composer),
banners, studio (tabs, artifacts, session picker, economics), cards (one block per SDK kind plus tool/task),
settings modal, context inspector. State is expressed with data attributes — `.card-badge[data-status]`,
`.notice[data-level]`, `.status-dot[data-state]`, `.stability-badge[data-stability]`,
`.tool-output[data-error]`, `.economics-stat-value[data-warn]`, `.harness-card-task[data-status]` — instead
of generated class names (`notice-${level}`, `task-${status}`, `stability-${stab}` are gone), which is what
makes the static audit exact.

*`index.html`* (289 → 246 lines, 0 inline styles, 44 ids all referenced): title bar pills are
`#workspace-pill` (from `/health.workspace`, new), `#economics-indicator`, context, settings and the
connection `#status-dot[data-state]`/`#status-text`; the agent header shows `#permission-mode-badge`
(`kernel.security.permission_mode`, refreshed on `settings.changed`); the status bar shows
`#active-session-display` (SessionRow title), `#active-model-display`, `#version-display`
(`/health.version`). Removed: terminal drawer, slash menu, the git/UTF-8/Ready pills, the `theme-dark`
class, the welcome turn (the P2 empty-timeline hint replaces it) and the ids `act-*`, `tab-*`,
`studio-viewport` that only decorated `data-*` hooks. The proceed banner's title/description are now filled
from the artifact card; the Artifacts tab starts with `.artifact-empty` and receives real cards.

*`app.js`* — `tool.output.delta` → `appendToolOutput(call_id, text, stream)` streams into the owning tool
card's `.tool-output` (created on the first chunk; `tool.result` then only sets the verdict instead of
duplicating the preview); `appendCard()` also renders `artifact` cards into `#studio-content-area` via
`showArtifact()` and switches the tab; `showProceedBanner(card)` fills name/summary; `updateDaemonInfo()`,
`updatePermissionBadge()`, `updateSessionDisplay()` feed the live pills; `setStatusIndicator()` and
`updateEconomics()` set `data-state`/`data-warn` instead of swapping utility classes; the Economics tab,
turn blocks and inspector templates use classes only; the settings search toggles `hidden`; `openSettings()`
/`closeSettings()` no longer touch `style.display`; terminal and slash handlers deleted.
`grep -c 'style="' app.js` = **0** (was 17).

*`cards.js`* — progress cards render a native `<progress class="progress-bar" value max>` (indeterminate when
`value` is null) — the last inline `style="width:…%"` is gone; notice/task cards carry state in data
attributes; `renderToolOutput()` documented as the shared tool-output markup for streaming and results.

*`server.py`* — `/health` gains `workspace` (the only source the title bar had for it).

*`additions/tools/ui_audit.py`* — reads every linked stylesheet (classes may live in either file; only
`styles.css` is checked for dead rules, the compiled file by construction contains only names found in the
templates), counts ids referenced by `for`/`aria-*`/`href="#"` as referenced, follows template prefixes
(`harness-card-${kind}`), and `--strict` now fails on *any* finding: missing or unreferenced ids,
undefined or unused classes, inline styles, a stylesheet set other than `[styles.css, vendor/tw.css]` in
that order, a script not served from `/static`.

*Tests* — `tests/frontends/test_ui_coherence.py` (3): strict audit clean; `vendor/tw.css` is compiled
output containing the utilities the markup relies on and no preflight; jsdom cascade — the eight elements
that must be hidden on load compute `display: none`, the four that must show do not, and un-hiding the
modal / Stop button yields `flex`.

*Flow test* — `daemon_restart_web_dom/golden/timeline.txt` loses the hard-coded welcome sentence (the only
golden delta in the suite; the other DOM scenarios cleared the timeline on reload anyway).

#### Gate (hermetic)

| Check | Result |
|---|---|
| `ruff check .` | clean |
| `harness events schema \| diff docs/events.schema.json -` | no diff |
| `npm run build:css && git diff --exit-code -- …/vendor/tw.css` | fresh |
| `python additions/tools/ui_audit.py --strict` | exit 0 — 0 missing ids, 0 unreferenced ids, 0 undefined classes, 0 unused classes, 0 inline styles |
| `grep -c 'style="' harness/frontends/web/static/app.js` | 0 |
| `pytest tests -q` | **372 passed** (369 + 3) |
| `harness plugin test --all` | **180 passed** |
| `pytest additions/scenarios -q` (strict, DOM lane on) | **21 passed** |

#### Not done in P3 (deliberately)
- The CSP still allows `style-src 'unsafe-inline'`: nothing in the client needs it any more, but
  `marked`/`prism` output was not audited for inline styles and jsdom cannot tell; tighten in a follow-up
  with a browser check.
- The "Unconfigured (Requires API Key)" optgroup in `populateModels()` stays until P4 changes what
  `models.list` reports.
- No visual regression tooling (no browser in this environment); the cascade test covers display state
  only, not pixels.
### P4 — Provider truth lives in the provider — **DONE**

**Baseline** (`b92c90e`, end of P3): ruff clean · 372 pytest · 21/21 scenarios · 180 conformance.
Hermetic daemon, no keys, no Ollama — `POST /commands/models.list` returned **4 rows**: three Ollama
catalogue entries marked `configured: true` (Ollama was not running) and `mock`. The five key-based
providers were *absent* (their `list_models()` raised `ProviderError(kind="auth")`, which the command
swallowed) and the daemon logged **five `WARNING  Error querying models from provider …` lines per page
load**. So the "Unconfigured (Requires API Key)" optgroup was unreachable by construction, and the one
provider that was listed as usable was the one that was down.

#### Defects
- **R20** `OllamaProvider.list_models()` fell back to the built-in catalogue on connection failure and
  `_is_provider_configured()` hard-coded `ollama → True`; the picker offered `llama3.2:latest` as a working
  model with nothing listening. The probe also used the 10 s connect timeout of the generation client.
- **R21** Hosted providers raised `auth` from `list_models()` when the key was missing, so they vanished
  from `models.list` instead of being listed as "needs key"; every call logged a WARNING.
- **R22** `Commands._is_provider_configured()` guessed readiness from secret *names*
  (`{name}_api_key`, `{NAME}_API_KEY`, a hard-coded list for `openai_compat`) — a second, drifting copy of
  what each plugin's `setup()` already knows.
- **R23** (found by the flow test, see below) nothing checked that a registered provider implements the
  contract; a provider missing a method disappeared from the picker silently.

#### What changed
*SDK* — `harness/sdk/contracts.py::Provider` gains `is_configured() -> bool` (pure: credentials present or
none needed) and `async is_reachable() -> bool | None` (short-timeout liveness probe, `None` = not probed,
never raises); `list_models()` is documented as "the catalogue the provider knows, with or without a key;
network only when configured; `auth` only when real credentials were rejected".
`harness/sdk/types.py::ModelRow` = `ModelInfo` fields + `provider`, `configured`, `reachable` and derived
`status` (`needs_key` → `unreachable` → `ready`). `ProviderKit.test_readiness()` added;
`test_list_models()` now also asserts that an unconfigured provider returns its catalogue rather than
raising. Scaffold template and `docs/PORTING_RECIPES.md` show both methods.

*Providers* — Ollama: one `GET /api/tags` probe with `PROBE_TIMEOUT_S = 0.3`, cached `PROBE_TTL_S = 30`
(positive *and* negative), feeds both `is_reachable()` and `list_models()` (installed models when up,
catalogue when down — flagged `unreachable` upstream). Anthropic / Gemini / OpenAI-compatible:
`is_configured()` = key present (OpenAI-compatible: or a self-hosted base URL — vLLM/LM Studio are
keyless by design, `requires_api_key()`); `is_reachable()` = `None`; `list_models()` returns the catalogue
without touching the network when unconfigured. Mock and `ScriptedProvider` (with `configured` /
`reachable` knobs for tests) implement both.

*Kernel* — `Commands.models_list()` asks each provider (`asyncio.gather`), builds `ModelRow`s, logs a
failing provider at DEBUG and never raises; `_is_provider_configured()` deleted.
`PluginRegistry._register_provider()` rejects a provider missing any contract member with
`PluginLoadError("… missing: is_configured, is_reachable …")` — a contract gap now fails the plugin load
(visible as `plugin.error`) instead of a silent hole in the picker.

*Clients* — web `populateModels()` renders three optgroups from `status` — **Ready** / **Needs API key
(Settings → Secrets)** / **Not running** — the last two disabled; it reads `ModelRow` keys only (contract
test). CLI `/models` gains a Status column.

#### Flow test found R23
After the provider changes the strict web lanes failed with `turn.started.options` missing
`provider: replay / model: replay-v1`: the scenario `ReplayProvider` (`additions/scenarios/_lib/replay_provider.py`)
had no `is_configured()`, so `models.list` skipped it, the picker had no `replay-v1` option, and the DOM
lane sent the prompt without a model. Fix: `ReplayProvider` / `RecordingProvider` implement the contract,
and the registry check above makes the next such gap loud. No golden changed.

#### After
Same hermetic daemon: **17 rows in 74 ms** (25 ms cached): `mock → ready`, `ollama ×3 → unreachable
(reachable: false)`, `anthropic ×3 / gemini ×3 / openai ×3 / openrouter ×2 / deepseek ×2 → needs_key`;
**0 WARNING lines**. The DOM lane of `plain_answer_web` now sees 19 options with `replay-v1` selected.

#### Gate (hermetic)

| Check | Result |
|---|---|
| `ruff check .` | clean |
| `harness events schema \| diff docs/events.schema.json -` | no diff |
| `npm run build:css && git diff --exit-code -- …/vendor/tw.css` | fresh |
| `python additions/tools/ui_audit.py --strict` | exit 0 |
| `pytest tests -q` | **387 passed** (372 + 15; one transient failure in the first full run was not reproduced in three further full runs — name not captured, reported as is) |
| `harness plugin test --all` | **180 passed** |
| `pytest additions/scenarios -q` (strict, DOM lane on) | **21 passed** |

#### Not done in P4
- The onboarding banner still keys off `kernel.provider` being empty; it does not yet say "your configured
  provider has no ready model" — that needs the same `ModelRow` data and belongs with the settings UI.
- Hosted providers are never probed (`reachable: null`); a bad key still surfaces at call time as
  `turn.error{kind: auth}`, which the client already renders.
### P5 — One test estate — **DONE**

**Baseline** (`a02698b`, end of P4): tests in four places — `tests/{kernel,plugins,frontends,e2e}` (387, in
CI), `additions/scenarios` (21, *not* in CI, own `conftest.py` and own `_lib/node/package.json`),
`tests/frontends/ui_smoke.mjs` + `test_ui_smoke.py` (a second jsdom bootstrap on fixed ports 8791/8792),
`review/tools/ui_smoke.mjs` (a third, from the v0.2.1 review); two `package.json`; `additions/tools/ui_audit.py`
used by CI but living in a "review material" folder; `tests/e2e/test_scenarios_s1_s6.py::test_scenario_s1_safe_daemon`
asserting 401/415/421 against a throw-away aiohttp app it built itself.

#### What changed
- `git mv additions/scenarios tests/scenarios`. Nothing in the suite depended on its location except the
  jsdom lookup: `web_runner.dom_lane_available()` now checks `<repo>/node_modules/jsdom` (Node resolves
  modules by walking up from `tests/scenarios/_lib/node/headless_timeline.mjs`, so the root install is the
  only one needed). `_lib/node/package.json` + lock + `node_modules` deleted. The scenario `conftest.py`
  keeps its `hermetic_env` fixture (it shadows the root autouse fixture of the same name inside
  `tests/scenarios`, with the same guarantees plus the returned config path the runners need).
- `pyproject.toml` already had `testpaths = ["tests"]`, so **plain `pytest -q` now runs kernel + plugins +
  frontends + e2e + scenarios in one invocation**; the per-scenario `_last_run/` stays git-ignored via
  `tests/scenarios/.gitignore`.
- `git mv additions/tools/ui_audit.py tests/frontends/ui_audit.py` — next to `test_ui_coherence.py`, which
  now imports it by sibling path; still a CLI (`python tests/frontends/ui_audit.py --strict                 # web UI coherent (ids, classes, inline styles, stylesheets)`). README and CI
  updated.
- `tests/frontends/ui_smoke.mjs`, `tests/frontends/test_ui_smoke.py`, `review/tools/ui_smoke.mjs` deleted.
  Their only checks the scenario DOM lane did not already make — the settings modal opens and its group
  tabs are schema-driven, and the page logs no console errors — moved into the lane: `headless_timeline.mjs`
  takes `SETTINGS_PROBE=1`, reports `settings_groups`, `settings_modal_hidden`, `console_errors`; new
  expectation keys `settings_groups_include` and `no_console_errors` (default on); `plain_answer_web`
  asserts `["Models & Providers", "Secrets"]` and a clean console (the lane sees all five groups:
  Models & Providers, Security & Sandboxing, Execution & Strategies, Economics & Spend, Secrets).
- `test_scenario_s1_safe_daemon` deleted; the module docstring points at the real checks
  (`test_web_server.py::test_health_and_host_guard` → 421, `test_auth_guard_and_commands_dispatch` → 401 + 415).
- One `package.json`: `jsdom` pinned to the version the lane was validated with (`24.1.3`, previously
  `^24.0.0` at the root and `24.1.3` in the nested file), `tailwindcss 3.4.17`, `test:ui` script removed;
  CI uses `npm ci` (lockfile) and runs the hermetic `pytest -q` with `SCENARIO_DOM_WAIT_MS=6000`.

#### Gate (hermetic)

| Check | Result |
|---|---|
| `ruff check .` | clean |
| `harness events schema \| diff docs/events.schema.json -` | no diff |
| `npm run build:css && git diff --exit-code -- …/vendor/tw.css` | fresh |
| `python tests/frontends/ui_audit.py --strict                 # web UI coherent (ids, classes, inline styles, stylesheets)` | exit 0 |
| `SCENARIO_DOM_WAIT_MS=6000 pytest -q` (one invocation) | **406 passed in 5 min 02 s** = 387 − `test_ui_smoke` − S1 + 21 scenarios |
| `harness plugin test --all` | **180 passed** |
| `find . -name package.json -not -path '*/node_modules/*' \| wc -l` | 1 |
| `find . -name 'ui_smoke*'` | nothing |

#### Not done in P5
- The DOM lane does not fetch stylesheets (jsdom without `resources: "usable"`), so it asserts class state
  (`settings_modal_hidden`), not computed display; the cascade is covered by `test_ui_coherence.py`.
- `tests/experimental/` and `tests/fixtures/` were not touched.
- CI still runs `harness plugin test --all` (= `pytest tests/plugins`) after `pytest -q` — redundant but it is
  the documented user command; drop it in P6 if the README stops advertising it.
### P6 — Version and document hygiene — **DONE**

*Baseline (review §4.6 and §5)*: `158c836` rewrote the released `[0.2.1]` CHANGELOG section after the tag
(3 lines became 11 lines of claims about work done later); `/health` reported `"version": "2.0.0"` while
`harness --version` said `0.2.1`; the README advertised "56+ test files, 340+ tests" and a 22-document "blueprint
archive" at `docs/` root next to the three documents that describe the product; there was no end-to-end
description of the system as built (process model, commands, events, session lifecycle); `jsonl_transcript`
wrote a `sessions_index.jsonl` on every append that nothing read; the command dispatcher resolved names by
`getattr` with no catalogue to document or test against.

*Changes*

| Area | Change |
| :-- | :-- |
| `CHANGELOG.md` | `[0.2.1]`, `[0.2.0]`, `[0.1.0]` restored byte-identical to tags `v0.2.1` / `v0.2.0` / `v0.1.0`; the post-tag claims from `158c836` moved to `[Unreleased]` as a "`main @ 158c836` remediation" block; P6 block added. `tests/test_docs_and_versions.py` pins the SHA-256 of every released section — editing a released section fails CI with the instruction to write an erratum under `[Unreleased]` instead. |
| `harness/frontends/web/server.py` | `/health` returns `harness.__version__` (was the literal `"2.0.0"`); `test_health_and_host_guard` asserts equality with the package. `_handle_command` honours `status_code` on `HarnessError`s → `TurnInProgress` is 409 over HTTP as its docstring and `test_krn_09` always claimed (was flattened to 400); new `test_turn_in_progress_is_409_over_http`. |
| `harness/kernel/commands.py` | `BUILTIN_COMMANDS` tuple (21 names, `form_submit` is the alias of `form.submit`) is the single source for the command catalogue; `dispatch` resolves through it instead of `getattr(self, anything)`. |
| `harness/sdk/contracts.py`, `sdk/scaffold.py`, `docs/architecture/CONTRACTS.md`, `plugins/stores/jsonl_transcript/store.py` | `TranscriptStore` is `append` / `read` / `last_compaction` — a store holds one session; listing is kernel-owned (`SessionManager.list_sessions`, P2). The store's `list_sessions`, its `index_path` and the per-append `sessions_index.jsonl` write are gone (R27, R28). Store, scaffold and contract tests updated. |
| `docs/ARCHITECTURE.md` (new, 225 lines) | Generated from the code, then edited: §1 process model (CLI → `bootstrap_kernel` → `Commands`/`EventBus`/`PolicyGate`/`SessionManager`; `harness ui` two-origin layout), §2 kernel modules, the 35 plugins by family, gate steps 1–9 with policy orders 10/50/60/900, §3 command table = `BUILTIN_COMMANDS`, §4 event table = `EVENT_PAYLOADS` with payload keys per row + `CARD_FIELDS`, §5 session lifecycle (lazy create, resume, switch, restart), §6 web client, authentication and error mapping, §7 test map. |
| `tests/test_docs_and_versions.py` (new, 4 tests) | `harness.__version__` == `pyproject` == doc title; every built-in command, every event type (with every dataclass field on its row) and every card kind appears in `docs/ARCHITECTURE.md`, and nothing is documented that does not exist; the 22 research documents are in `docs/research/` and none remain at `docs/` root; released CHANGELOG digests. |
| `docs/research/` | `git mv docs/00_*.md … docs/21_*.md docs/research/` (their relative links to each other still resolve; `00_README_AND_MANIFEST.md` is their own index). The one external reference (`review/dsh-integration/WS-06`) updated. |
| `README.md` | §4: counts removed, the real gate shown (two-statement `HOME` export, `SCENARIO_DOM_WAIT_MS`, the four CI gates); §5: documents that describe the system in reading order (`ARCHITECTURE` → `CONTRACTS` → `PLUGIN_CATALOG` → `PORTING_RECIPES` → schema/CHANGELOG), then history (`docs/research/`, `review/`, `additions/`). |
| `tests/scenarios/_lib/normalize.py` + 22 goldens | R31: `model.started.request_tokens_estimate` (prompt chars ÷ 4) depends on the length of the tmp workspace path — the suite started failing deterministically when `/tmp/pytest-of-user/pytest-NN` reached three digits (this is the "transient failure" noted in P3). The key joins the dropped volatile fields; the golden diff is only those lines (verified with `git diff -U0 … \| grep -v request_tokens_estimate`). |

*Not done, on purpose*

- Version stays `0.2.1`; this PR does not cut a release — the top CHANGELOG entry is `[Unreleased]`, and the
  acceptance "`harness --version` == `/health.version`" holds (both read `harness.__version__`).
- CSP keeps `style-src 'self' 'unsafe-inline'`: `app.js`/`cards.js`/`settings.js` no longer set inline styles
  (audit), but `cards.js` progress bars and `tw.css` are the only consumers checked; tightening to `'self'` is a
  one-line change once a jsdom-with-stylesheets lane exists to prove nothing regresses.
- `harness plugin test --all` stays in CI (redundant with `pytest -q`) because the README still documents it as the
  user-facing conformance command.
- `tests/experimental/` and `tests/fixtures/` untouched (as in P5).

*Flow test and full gate after P6* (one `pytest -q` invocation, hermetic, node present)

| Gate | Result |
| :-- | :-- |
| `ruff check .` | clean |
| `harness events schema \| diff docs/events.schema.json -` | empty |
| `npm run build:css` + `git diff --quiet vendor/tw.css` | fresh |
| `python tests/frontends/ui_audit.py --strict` | exit 0 |
| `harness plugin doctor --all` | clean |
| `SCENARIO_DOM_WAIT_MS=6000 pytest -q` | **411 passed in 5:04** (406 + 4 doc/version tests + 1 HTTP 409 test) — first run showed R31 (`jail_blocks_traversal_kernel`, estimate 73 → 74), fixed, then 21/21 strict |
| `harness plugin test --all` | 180 passed |

### P7 — WS-02: generated catalogues and freshness gates — **DONE**

*Why it is in this plan*: the pack's WS-02 is the mechanism that would have caught P6's and P4's
whole class of bug — a reference document, a UI list or a settings panel keeping its own copy of a
fact the kernel owns. The consolidation fixed the individual copies (P1 events, P3 live pills, P6 docs
test); this phase removes the possibility of new ones by generating the reference docs from a booted
kernel and gating them.

*What was built*

| Piece | Detail |
| :-- | :-- |
| `harness/kernel/catalog.py` | `build_catalog()` boots the kernel and reads `registry.discovered`, the tool table, the registered settings schemas, `EVENT_PAYLOADS`, `CARD_FIELDS`, `BUILTIN_COMMANDS`, a static parse of the shipped client (subscribed events, `command('…')` calls), the daemon's route table and `tests/scenarios/*/golden/events.json`. Renders the six markdown catalogues + `catalog.json`; every renderer is a pure function of that dict, so `--check drift` compares in memory without touching the tree. |
| `harness/cli_catalog.py` | `harness catalog gen` · `harness catalog --check [--section drift\|settings\|readmes\|ui\|scenarios\|refs] [--strict] [--format json]` · `harness catalog readme <plugin>`. Wired into `harness --help`. |
| Settings ownership | Nine plugins ship `settings.schema.json` with `x-namespace` (the plugin's own `plugins.<name>` by default), `x-alias-namespaces` (a compatibility spelling may rename the leaf: legacy `plugins.mcp-client.servers`) and `x-secrets` (the secret keys it reads). `PluginRegistry._register_settings_schema` registers them *before* `setup()` so a plugin's own default resolves during boot; `_register_secrets_schema` merges the secret keys into the `secrets` UI namespace. |
| `Commands.settings_schema` | The hand-coded 11-property dict is gone; the response is the aggregation of registered schemas (kernel properties that carry `x-ui`, plus every plugin property), with the provider and strategy enums taken from the components that actually loaded. Same `{groups, properties}` shape, so `settings.js` renders it unchanged. |
| `settings.js` | Its second, invented copy of the settings schema (a fallback used when the fetch failed, containing keys that never existed) is deleted; the panel now shows an explicit "Settings schema unavailable" state instead of a plausible-looking wrong form. |
| Boundary | `tests/kernel/test_import_boundaries.py::test_plugins_do_not_import_sibling_plugins` — plugins compose through the registry, never by importing each other. |

*Flow test and gate after P7*

| Gate | Result |
| :-- | :-- |
| `harness catalog --check` | `drift` ok · `settings` ok (40 declared, all reads declared, none dead) · `ui` ok (client calls 10 registered commands) · `readmes` warn 34/35 · `scenarios` warn 7/12 tools · `refs` ok after `docs/catalog/refs.allow` |
| `SCENARIO_DOM_WAIT_MS=6000 pytest tests/scenarios -q` | **21 passed** (goldens re-baselined for `turn.started.options.max_iterations`, see R38; the diff is that one key and nothing else) |
| `pytest -q` | **430 passed** — one invocation, includes the 16 new catalog/settings/provider tests |
| `ruff check .` / `ui_audit.py --strict` / `tw.css` diff | clean |

*Deferred on purpose* (recorded in `review/dsh-integration/WS-02_generated_catalogs_gates.md`)

- `readmes` and `scenarios` are warnings; `--strict` promotes both to failures. Closing them means writing
  the five-section README for 34 plugins and covering 5 tools + 11 commands — real work that belongs with
  the features that make those tools interesting (WS-03/WS-07/WS-08), not in a docs sweep. `bridge-mcp-client`
  is the first README converted to the template, so the shape is proven.
- `refs` is scoped to namespaced setting keys (`kernel.*`, `plugins.*`, …) plus commands and event types; a
  general "every backticked identifier must exist" rule produced mostly noise on prose.
- `harness catalog gen --out`, `verify-package-readmes`-style per-package README linting and the
  `scaffold --kind tool/policy/hook/store` templates were not built; the scaffold still emits `context` only.
- `docs/events.schema.json` stays where it is and is still produced by `harness events schema`; the drift
  check now covers it, replacing the single-file `diff` step in CI.

---

### P8 — WS-03: loop hygiene — **DONE**

*Why it is in this plan*: WS-02 removed parallel *descriptions* of the kernel; WS-03 removes the
parallel *behaviour* the model has to work around. Three failures were visible in the replay goldens:
a 200 KB test log arriving with its failure summary cut off (head-only truncation), the model re-running
an identical command with nobody pointing that out, and `edit_file` guesses against files never read.
Plus one that only showed up under load: a timed-out `run_command` left its child process running.

*What was built*

| Piece | Detail |
| :-- | :-- |
| `HookEvent.TOOL_RESULT_FINALIZE` + `HookOutcome.result` | Gate step 8b, after the handler and before sanitisation/truncation; ordered by the new `Hook.order` (spill 800, pruner 900). `POST_TOOL_USE` was the wrong place: appends there were truncated away. |
| Cooperative timeout | `ToolContext.deadline` / `cancel_event`, `kernel.limits.tool_grace_s`, `track_process()` + `terminate_tracked()`, streamed output reused as the partial result. |
| `HookContext.turn_state` | Per-turn scratch seeded with `records` (the live transcript list): repeat counters and the read ledger need cross-call memory with no module globals. |
| Four hook plugins | `tool_result_spill`, `tool_result_pruner`, `repeat_call_reminder`, `read_before_edit` under `harness/plugins/hooks/`, each with manifest + `settings.schema.json` + 5-section README; 17 new declared settings. |
| Read ledger | Derived from transcript records in `read_before_edit` (a call counts only once a non-error result exists), so it survives a restart and ignores failed reads; `fs` tools publish `metadata.path`/`read_sha256` to make that derivation possible. |
| Tests | `tests/kernel/test_tool_result_finalize_hook.py` (5), `test_cooperative_timeout.py` (6), `test_loop_hygiene_through_kernel.py` (5), `tests/plugins/test_loop_hygiene_hooks.py` (25); scenarios `huge_tool_output_pruned_and_spilled`, `repeat_call_reminder`, `edit_without_read_blocked`. |

*Deviations from the spec (deliberate)*

* Setting namespaces follow the repo's `<kind>-<name>` plugin names — `plugins.hook-tool-result-pruner.*`,
  not `plugins.hooks-tool-result-pruner.*`; the spec's teeth test is `…enabled: false` either way.
* Conformance/unit tests live in `tests/plugins/test_loop_hygiene_hooks.py` (one test estate, P5) instead
  of `tests/test_conformance.py` inside each plugin directory; `harness plugin test --all` runs them.
* No scenario for the timeout: `run_command`'s `ToolSpec.timeout_s` is 600 s and not configurable, so a
  scenario would either wait ten minutes or fake the tool. The behaviour is pinned by
  `tests/kernel/test_cooperative_timeout.py` and by `test_a_stuck_tool_returns_partial_output_instead_of_nothing`
  through a booted kernel with a 0.3 s tool. Making the timeout operator-tunable is left to WS-06.
* `read_before_edit` uses one switch (`mode: block|warn|off`) rather than `enabled` plus `mode`, and the
  other three hooks have `enabled` because they have no mode.


## 3. Defects found while executing (continuing the review's numbering)

| # | Where | What | Status |
|---|---|---|---|
| R8 | `static/cards.js` vs `sdk/cards.py` | Renderer read `{kind, payload}` with keys `file/diff/headers/message/filename/…`; the SDK emits `{kind, id, title, data}` with `path/unified/columns/body/name/…`. Every plugin card (diff, table, notice, markdown, artifact) rendered empty or as the fallback. | fixed in P1 |
| R9 | `kernel/gate.py` → bus | `tool.call.data.arguments` was the live `current_args` dict; hooks/policies mutate it afterwards, so subscribers saw different values depending on drain timing (`<ws>/app.py` vs `app.py` in the recorded goldens). | fixed in P1 (payload serialisation snapshots) |
| R10 | `approvals.py` | `approval.required.call_id` is `None` for strategy-level approvals; the UI assumed a string. | contract now `str \| None` |
| R11 | `plugins/tools/memory` | `remember` returned the confirmation both as result text and as a notice card → shown twice once cards rendered. | card removed |
| R12 | `static/app.js` | `CSS.escape` is not available in jsdom; `resolveApprovalUI`/`completeTask` threw there, so the DOM lane could never see "Resolved: …". | `cssString()` helper |
| R13 | `bridge-shell-hooks` | `ui.card` emitted `{kind, payload}` directly as `data` (no `card` wrapper), so the client's `data.card` was undefined. | fixed |
| R14 | `static/styles.css` | No `.hidden` rule existed (only `.context-inspector.hidden`): approval banner, proceed banner, onboarding banner, Stop button, session list and economics view were all visible on load. | fixed in P3 (`vendor/tw.css`; cascade test) |
| R15 | `static/styles.css` | Second half used four undefined custom properties (`--border-color`, `--bg-secondary`, `--text-primary`, `--accent-hover`) → transparent modal dialog, missing borders. | fixed in P3 (one token set) |
| R16 | `static/styles.css` | Nine selectors defined twice with conflicting values (two stylesheets concatenated). | fixed in P3 (rewrite) |
| R17 | `index.html` / `app.js` | Slash menu inserted `/plan /diff /compact /pytest /git`; no kernel command interprets them in web prompts. | removed in P3 |
| R18 | `app.js` | `tool.output.delta` appended to a terminal drawer that was `display:none` with no code path to show it. | P3: streams into the tool card |
| R19 | `index.html` | Static pills and placeholders (`🪝 main`, `Autonomous`, `Sovereign Daemon Online`, `UTF-8`, `Autonomous Heuristic`, `Cache Tier: Invariant`, welcome turn). | P3: live values or removed; `/health.workspace` added |
| R20 | `providers/ollama` + `commands.py` | Ollama listed as configured/usable while not running (catalogue fallback + hard-coded `ollama → True`); probe used the 10 s generation timeout. | P4: `is_reachable()` 300 ms probe, 30 s cache, rows `unreachable` |
| R21 | hosted providers | Missing key → `list_models()` raised `auth` → provider absent from `models.list` + WARNING per page load. | P4: catalogue returned, `status: needs_key`, DEBUG |
| R22 | `commands.py` | `_is_provider_configured()` guessed readiness from secret names. | P4: deleted; `Provider.is_configured()` |
| R23 | `kernel/registry.py` | No contract check at provider registration; a provider missing a method vanished from the picker silently (hit by the scenario `ReplayProvider`). | P4: `PluginLoadError` naming the missing members |
| R24 | `CHANGELOG.md` | `158c836` rewrote the released `[0.2.1]` section after the tag (3 → 11 lines claiming later work). | P6: restored from tag `v0.2.1`; claims moved to `[Unreleased]`; released sections SHA-pinned by `tests/test_docs_and_versions.py` |
| R25 | `frontends/web/server.py` | `/health` returned the literal `"version": "2.0.0"`; the package is `0.2.1`. | P6: `harness.__version__`; test asserts equality |
| R26 | `frontends/web/server.py` | `TurnInProgress` (`status_code = 409`, documented as 409 in `commands.py`, `session.py` and KRN-09) was returned as HTTP 400. | P6: `HarnessError.status_code` honoured; `test_turn_in_progress_is_409_over_http` |
| R27 | `plugins/stores/jsonl_transcript/store.py` | `sessions_index.jsonl` appended on every transcript append; nothing read it (the kernel lists sessions from `session.json`, P2). | P6: removed |
| R28 | `sdk/contracts.py` | `TranscriptStore.list_sessions` in the Protocol, scaffold and CONTRACTS doc with no caller; contradicted the kernel-owned lifecycle. | P6: dropped from contract, scaffold, doc |
| R29 | `kernel/commands.py` | `dispatch` resolved any `getattr(self, name)` — no catalogue existed to document or test the command surface. | P6: `BUILTIN_COMMANDS`; doc test |
| R30 | `README.md`, `docs/` | Hard-coded "56+ test files, 340+ tests" (stale by 70); 22 research syntheses at `docs/` root beside the three product documents; no end-to-end architecture document; `review/`, `additions/`, `docs/` with no stated relationship. | P6: counts removed; `docs/research/`; `docs/ARCHITECTURE.md`; README §5 reading order + history section |
| R32 | `frontends/web/static/settings.js`, `kernel/commands.py` | The settings surface existed twice: the kernel hand-coded 11 properties (including `kernel.security.jail_enabled`, which no code reads) and the client hand-coded a *different* fallback list with keys that never existed (`kernel.model.default`, `kernel.security.allow_shell_execution`), which silently replaced the kernel's schema whenever the fetch failed. | P7: both deleted; the panel is the aggregation of registered schemas; a failed fetch is an explicit error state |
| R33 | `sdk/manifest.py`, `kernel/settings.py` | `PluginManifest.settings_schema` and `SettingsStore.register_schema()` existed with **zero** users: every plugin setting (`servers`, `hooks`, `expose`, `context.repo_map.*`, provider base URLs, `script_file`) was read undeclared — no default, no validation, no UI row. | P7: nine plugins ship `settings.schema.json`; the loader registers them; `harness catalog --check settings` fails on any undeclared read |
| R34 | `kernel/settings.py` | `kernel.request_timeout_s` (three providers) and `kernel.plugins.capabilities.<name>` (registry) were read but absent from `KERNEL_SCHEMA`. | P7: declared, with the capability container left open for its dynamic suffix |
| R35 | `kernel/commands.py` | `plugins_info` read `dp.path`; `DiscoveredPlugin` has no such attribute, so `harness plugin info <name>` raised `AttributeError`. No test touched the command. | P7: `plugin_dir()` helper (`harness/kernel/plugin_paths.py`); covered by `test_catalog.py` |
| R36 | `plugins/providers/*`, `harness/cli.py` | Providers read absolute keys (`providers.anthropic.base_url`, `providers.mock.script_file`) through `ctx.settings`, which is a *namespaced view*: the read became `plugins.provider-anthropic.providers.anthropic.base_url`, so the documented key — and `harness run --mock-script` — could never reach the provider. | P7: plugins read their own relative keys, schemas declare `plugins.provider-<name>.*`, the CLI writes that key; `tests/plugins/test_provider_settings.py` proves a configured value lands on the provider |
| R37 | `plugins/bridges/mcp_client` | A third fallback, `settings.get("extensions.mcp_servers")`, was dead for the same reason (the view re-prefixed it to a key that does not exist). | P7: removed rather than declared — nothing could ever have been using it |
| R38 | `kernel/runner.py` | `kernel.max_tool_iterations` was in the settings panel and in `KERNEL_SCHEMA`, but the strategies used their own literal `20`, so the control changed nothing. | P7: `TurnRunner.execute_turn` seeds `max_iterations` from the setting when the caller did not pass one (a per-turn override still wins); 21 goldens gained the key in `turn.started.options`, which is the point — the recorded turn now shows the cap that applied |
| R39 | `plugins/providers/gemini` | The panel offered `secrets.GEMINI_API_KEY` only, while the provider also accepts `GOOGLE_API_KEY`. | P7: both declared via `x-secrets`, so the list is generated |
| R40 | `kernel/gate.py`, `plugins/tools/shell` | `asyncio.timeout(spec.timeout_s)` cancelled the *handler coroutine* only: the subprocess it had started kept running (pipe still open, work still burning CPU), any output it had streamed was thrown away, and the model got `Tool 'run_command' timed out after 600s` with nothing else. | P8: `cancel_event` + `kernel.limits.tool_grace_s` grace period, `ToolContext.track_process()`/`terminate_tracked()`, streamed deltas become the partial result; `run_command` registers its child and returns early when signalled |
| R41 | `plugins/hooks/repeat_call_reminder` | The new plugin declared `enabled` in its schema but never read it — a settings row that changes nothing (the exact bug class WS-02 was built to catch, reintroduced by new code). | P8: caught by `test_settings_are_declared_under_the_manifest_namespace`, which every hook plugin now pins; the flag is read and honoured |
| R31 | `tests/scenarios/_lib/normalize.py` | Goldens pinned `model.started.request_tokens_estimate` = prompt chars ÷ 4, which includes the tmp workspace path — flipped 73 → 74 when the pytest tmp counter reached three digits (the unexplained transient failure of P3). | P6: key dropped from goldens (22 files, only those lines) |

---

## 4. Review §7 definition of done — verified after P6

| Check (review §7) | Result |
| :-- | :-- |
| `ruff check .` | clean |
| `pytest -q` — kernel + frontends + scenarios, one suite | 411 passed, one invocation (`SCENARIO_DOM_WAIT_MS=6000`) |
| `harness plugin test --all` | 180 passed |
| `harness events schema \| diff -u docs/events.schema.json -` | empty |
| `ui_audit.py --strict` (now `tests/frontends/ui_audit.py`) | exit 0 — 0 missing ids, 0 undefined classes, 0 inline styles |
| one `package.json` outside `node_modules` | 1 |
| `grep -c 'style="' app.js` | 0 |
| three daemon restarts leave one session (manual probe) | automated: `tests/kernel/test_session_lifecycle.py` + scenario `daemon_restart_web_dom` (strict) |
| one mock turn: Economics tab = header indicator = `POST /commands/economics` (manual probe) | automated: `tests/frontends/test_static_contract.py::test_economics_payload_keys_match_app_js` + `plain_answer_web` DOM lane (`cost.updated` → header/tab from the same `EconomicsSnapshot`) |
| P6 acceptance: `harness --version` == `/health.version` == package | `harness-zero, version 0.2.1` · `/health` → `harness.__version__` (tested) · pyproject `0.2.1` (tested); CHANGELOG top entry is `[Unreleased]` because no release is cut in this PR |

## 5. Gate recipe (run after every phase)

```bash
python3 -m venv .venv && . .venv/bin/activate && pip install -q -e ".[dev]"
export HOME=$(mktemp -d); export HARNESS_CONFIG_DIR=$HOME/.harness; mkdir -p "$HARNESS_CONFIG_DIR"   # two statements: $HOME must be re-evaluated
ruff check .
npm ci --silent && npm run build:css && git diff --exit-code -- harness/frontends/web/static/vendor/tw.css   # jsdom + tailwind; compiled utilities fresh
SCENARIO_DOM_WAIT_MS=6000 python -m pytest -q               # everything, one invocation (since P5: kernel, plugins, frontends, e2e, 21 scenarios; event_contract_guard)
python -m harness.cli plugin test --all                     # plugin conformance (= pytest tests/plugins)
python -m harness.cli events schema | diff docs/events.schema.json -
python tests/frontends/ui_audit.py --strict                 # web UI coherent (ids, classes, inline styles, stylesheets)
# only when a change is *meant* to move the wire: re-baseline once, inspect `git diff tests/scenarios/*/golden`, then strict again
HARNESS_REPLAY=update SCENARIO_DOM_WAIT_MS=6000 python -m pytest tests/scenarios -q -k <scenario>
```
