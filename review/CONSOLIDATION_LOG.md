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
| R42 | `kernel/approvals.py` → bus | `approval.resolved` omitted `session_id`, so a client that filters events by session left the approval card pending until the page was reloaded. | P10b: the payload carries it; `approval_allow_web_dom` pins the resolved card |
| R43 | `frontends/web/static/app.js` | The reload path rendered `sessions.get` history by **role**, and the payload it re-derived contained only user/assistant text and tool results. Approvals, policy decisions, tool cards and the compaction summary were therefore invisible after a refresh — the log held them, the client had no case for them. A `plugin · risk` subtitle also existed only on the live `tool.call` event, so even a card that survived reloaded thinner. | P11: `renderRecord(rec)` switches on `rec.kind` and reuses the live painters; `tool.decision` gained `plugin` on the event and in the record; scenario `reload_equals_live_web_dom` asserts every live line survives a refresh |
| R45 | `tests/scenarios/_lib/` (record path) | In record mode the tee was registered *alongside* the provider it wrapped, under the fixture's key. `app.js` sends the provider of the selected model, so a browser-lane request resolved to the real `mock` plugin, the recorder saw nothing, and `provider.jsonl` came out 1 byte with the run still green — the emptiness only surfaced later, as `ReplayExhausted: … recorded 0 requests` in the replay phase. Keying the tee as `mock` is refused outright (`_register_provider`: duplicate name). | P11b: the tee keeps the fixture key and the wrapped provider is popped from `runtime.registry.providers` for the run; `test_record_then_replay_in_the_web_lane` asserts the recording is non-empty and that both lanes agree, so a silent no-op recording fails the same test that wrote it |
| R46 | `tests/scenarios/_lib/node/headless_timeline.mjs` | The model chip was selected by **id** alone. A recorded fixture's id (`mock-v1`) is also served by the built-in mock, so the browser lane replayed against the plugin rather than the fixture: `turn.started` said `provider: "mock"` where the kernel lane said `"replay"`, and the two runs' goldens could never agree. Selecting by `sel.value = id` compounds it — jsdom takes the first option with that value, i.e. option order decides what a test means. | P11b: `MODEL_PROVIDER` scopes the selection to the fixture's provider (only when several providers list the id), the chosen option's provider is reported as `model_selected_provider`, and the sidecar's model id is what replay pins (`scenario_model`) |
| R47 | `kernel/commands.py` → `settings_schema` | `groups = [g for g in SETTINGS_GROUPS if g in present]` filtered the settings surface through a hand-kept list of tab names, so a plugin that declared `x-ui.group: Obsidian` in its own `settings.schema.json` got its properties into the response but **no tab** — the one mechanism WS-05 needs (disable a feature, its tab goes) was unreachable without editing kernel code, and no test noticed because every built-in plugin reused a kernel label. | P12a: `order_groups()` derives membership from the aggregated properties and order from `x-ui.order` (built-in sequence as fallback), pinned by `test_a_plugin_settings_group_is_a_tab_the_kernel_never_mentioned` |
| R48 | `kernel/catalog.py` (the new static parse) | Reading `data-tab`/`data-command` attributes out of JS *and* HTML counted `document.querySelector('.studio-tab[data-tab="${tabName}"]')` as a declared tab named `${tabName}` — a static check that invents requirements teaches people to ignore it, and its "fix" is a fake declaration in the source. | P12a: `_static_values()` filters template-literal placeholders in both the catalogue and `test_ui_contract.py`, and the same rule is stated once in each place it is needed |
| R49 | `tests/frontends/ui_audit.py`, `tests/frontends/test_static_contract.py` | Both graders of the client read **one file**: the audit scanned `static/*.js` (not subdirectories) and `_js_function_body` matched only a 2-space-indented class method. The moment a body moved into `static/panels/…`, the id/class checks silently covered less, and the assertions that read `renderEconomicsTab` scored an empty string — a `_member_refs(body) <= keys` check on `""` always passes. The refactor looked *safer* than the code was. | P12b: both scan `static/**/*.js` (excluding `vendor/`), `_js_function_body` finds a class method, a module function or an object member and cuts at the brace on the definition's own indent, and the row-shape assertions were repointed at the files that now own the markup — where they keep their teeth (verified by mutation, not by trust) |
| R50 | `kernel/catalog.py` (the UI section) | The static parse read `data-*` attributes out of the whole file text, so a doc comment *giving an example* (`data-command="sessions.create"`) was catalogued as a real binding — and, because `sessions.create` happens to exist, nothing failed. A generated table that reports documentation as wiring trains reviewers to distrust the table. | P12b: `_code_only()` drops whole-line comments before the binding/tab/case scan (trailing comments and `//` inside strings survive), the `data-*` scan became recursive like the audit's, and a `renderers/_name.js` helper is no longer counted as a painted kind |
| R51 | `tests/scenarios/test_scenarios.py` | When the reload DOM lane crashed (node exited, no JSON), `(result.dom or {}).get("reload") or {}` read as *an empty timeline*: `reload_timeline_contains` blamed the client, and `reload_timeline_not_contains` passed vacuously — the failure mode was a wrong diagnosis for a real bug and a silent pass for its mirror image. | P12b: `_reload_report()` fails on the lane's own `error`/`stderr` and requires `timeline_text_on_load` to be present, so a missing run is never read as an empty page |
| R52 | `harness/frontends/web/static/app.js` (during P12b) | The new `handlePanelSelect` called `this.selectSession(...)` — a method this client has never had (`switchSession` is the real name). Nothing static caught it; jsdom threw on the session-row click, i.e. a user clicking a session in the reloaded page would have gotten a broken pane and a console error. The class of bug the registry indirection creates, and no existing test reached it because no other test clicks a session row. | P12b: `test_the_client_only_calls_methods_it_defines` checks every `app.x()` a module calls and every `this.x()` in the app against the methods and constructor members `app.js` actually defines (shown to fail by re-introducing `selectSession`, then reverted) |
| R53 | `harness/frontends/web/static/settings.js` | The panel's constructor seeded the open tab with the literal `'Models & Providers'` — a client deciding which of the kernel's tabs to show. A daemon with no provider plugin loaded (fresh install, all providers disabled) opened the settings modal on an empty pane reading "No settings configured in this group" while 44 settings existed; and the same literal made the client's *default* depend on a tab name the catalogue had already stopped owning. | P13a: the open tab comes from `settings.schema`'s `groups` (`groups[0]`), a remembered group is validated against the list, and `setGroup` ignores a tab that does not exist; `test_settings_js_renders_the_owner_heading_and_hardcodes_no_tab_names` bans the literal form (`activeGroup = "…"`) in the client |
| R54 | `harness/kernel/catalog.py` (`SETTINGS.md`) | The renderer walked a literal tuple of group names when deciding which sections to emit, so the moment properties moved to tabs the tuple had not heard of (`Hooks`, `Bridges & Integrations`, `Context`) their rows vanished from the document — while `harness catalog --check` stayed green, because drift checks compare the file against a fresh build of the *same* renderer. A generator with its own copy of a fact, graded by its own copy. | P13a: sections are derived from the rows plus `ui.settings_groups` (client order first, anything unexpected after), and `test_settings_doc_has_a_section_for_every_tab` checks both directions against the committed pair — proven by putting the literal list back, which fails naming the three tabs |
| R55 | `harness/frontends/web/static/registry.js` (`CommandBinder`) | A *successful* command could be announced as a failure: `api.command()` and the `harness:command-done` `dispatchEvent` shared one `try`, so any throw on the announce path — a listener repainting the pane and detaching the control first — landed in `catch`, which appended "`plugins.disable` failed: …" to the timeline. The kernel had already written the setting; the user was told the opposite. Found by clicking the real button in the DOM lane, which no static contract can see. | P13b: the dispatch moved outside the command's `try`, retargets to `document` when the control is no longer connected, and has its own `catch` that only logs; the lane now records the click round trip (`plugin_toggle`) and shows `Disable → off next start → Enable → loaded` |
| R56 | `tests/scenarios/_lib/node/headless_timeline.mjs` | The lane copied window globals into `globalThis` only `if (g[k] === undefined)` — and Node 19+ ships its own `Event`, `CustomEvent` and `FormData`. jsdom's `dispatchEvent` rejects a foreign event instance ("parameter 1 is not of type 'Event'"), so every event the *page* dispatched after an `await` was silently untestable in exactly the lane built to verify client behaviour. The `if` reads as care and was a blind spot. | P13b: DOM- and event-shaped globals are always taken from the window (`FROM_WINDOW`), the rest only when absent; three existing DOM scenarios re-run green, so the change is a widening, not a re-baseline |
| R57 | `harness/kernel/registry.py` → `plugins.list` | A plugin that fails at `setup()` leaves no trace a frontend can read: the registry logs it and publishes a `plugin.error` **event** (`registry.py:575`), and discovery-time failures are a `logger.warning` with no record at all. So `plugins.list` can only say `loaded: false`, and a pane cannot tell "waiting for a restart" from "broken since boot" — nor can a *reloaded* page, since the event is not a durable record. | **Not fixed in P13b** (the pane now says the one thing it knows — `not loaded` — instead of guessing). The fix belongs with the health/diagnostics workstream: keep load outcomes as a durable record kind so `plugins.list` (and a reload) can read the reason from the log the kernel already trusts |
| R58 | `harness/kernel/registry.py` → `ui.contributions`, `sdk/manifest.py` | `[ui].client_module` was never a closed loop, at either end. The kernel advertised `"/" + <declared path>`, which nothing served — the web server mounts one static root, the client's own directory — so every plugin-declared module URL was a 404 in the browser and its panel silently fell back to the generic renderer; and the trust gate that was supposed to guard the feature could not fire, because `CLIENT_MODULE_TRUST` named `"config"`, which is not a `TrustTier` value, while `discover_directory` called `PluginManifest.load(path)` without a trust at all (default `builtin`). Two halves of one claim, both invisible: the catalogue showed a `client_modules` entry, the pane showed a panel, and only a browser could notice the bytes never arrived. | P14: the address is derived once in `harness/sdk/plugin_static.py` (`client_module_url`) and used by the advertisement *and* the route; `CLIENT_MODULE_TRUST = {"builtin", "local"}`; discovery passes `trust=tier.value`. Pinned by `test_the_url_the_kernel_advertises_is_the_url_that_serves_the_bytes` (frontends) and `test_every_trust_level_allowed_a_client_module_is_a_real_tier`, which reads `TrustTier` instead of copying its values — a gate that checks a vocabulary no one uses is the bug that test exists to keep out |
| R59 | `harness/sdk/plugin_static.py` → `client_module_file` | Three ways to be wrong about one address, found one after another. (1) Deriving the URL's `<plugin>` segment from the plugin's *directory* rather than the registry key serves a manifest whose `name` disagrees with its folder under one address and looks it up under the other. (2) Comparing the request's `tail` against `f"{CLIENT_MODULE_ROOT}/{tail}"` — the natural reading of a route that captures `{name}` and `{tail}` separately — refuses the legitimate file, because `client_module_url` embeds `<name>/` while `{tail}` excludes it; the first draft had this and its three failures are what surfaced it. (3) Once (2) was "fixed" by rebuilding the address from all three parts, the comparison used `name` on *both* sides, so it held for any name at all — the guard against browsing a plugin directory file by file was a tautology, and it survived until a test tried to pin it. | P14: `client_module_file(discovered, name, tail)` takes the registry key as an argument (so the advertisement and the route are literally the same call) and refuses unless `name` is the plugin's own `manifest.name`; `test_the_address_is_keyed_on_the_registry_name_not_the_folder_it_sits_in` pins a folder called `probe-plugin-dir` whose manifest says `obsidian`, in both directions, and `test_anything_not_declared_is_nothing` covers the sibling/traversal family |
| R60 | `harness/kernel/registry.py` → `load_all`, `harness/sdk/scaffold.py` | A plugin you installed could not load. The scaffold writes `entry = "plugin:MyPlugin"`, nothing put the plugin's directory on `sys.path`, `importlib.import_module("plugin")` raised `ModuleNotFoundError`, and `load_all` recorded it as a `plugin.error` event — so `~/.harness/plugins/<name>` and `<workspace>/.harness/plugins/<name>` trees (the only way to add a plugin outside the repo: there is no `harness plugin install`) were discovered, listed in `plugins.list`, and never instantiated. Every downstream surface — settings tab, panel, client module — was unreachable for exactly the plugins those surfaces exist for. No test had ever installed one, which is why the mechanism looked finished. | P14: `PluginRegistry._import_entry` — dotted import first, then the plugin's own file under a unique `sys.modules` name; no `sys.path` mutation, no change to what may *run* (tier + capability clamp + `CLIENT_MODULE_TRUST` still decide). Reproduced before fixing (`ModuleNotFoundError: No module named 'plugin'`, plugin absent from `_loaded`) and after (loads, `ui.contributions` serves `url=/static/plugins/probe-vault/probe.js`); the scenario `plugin_panel_appears_when_enabled` is the first scenario in the repo to install a plugin on disk and drive it through a browser |
| R61 | `harness/frontends/web/static/registry.js` → `mountGeneric` | The generic renderer always sent `session_id`, and `Commands.dispatch` forwards a command's arguments as keyword arguments — so any panel whose command does not name `session_id` raised `TypeError` and painted nothing. The three built-in panels (`artifact`, `sessions`, `economics`) all happened to accept it, which is the whole reason the bug survived: the feature worked for every panel the repo ships and would fail for the first panel a plugin declares — precisely the case WS-05 exists to serve. Found by writing the fixture plugin's panel against `plugins.list`, which does not. | P14: `ui.contributions` attaches `accepts` (the argument names of the panel's command, taken from the `commands_list()` rows it already computes) to each panel, and the client passes `session_id` only when that list names it. Panel key set is now `{id, title, command, renderer, plugin, accepts}`, regenerated into the catalogue so `--check` sees drift without booting a kernel; `test_every_declared_panel_can_be_refreshed_with_the_args_the_client_sends` holds the rule over every declaration, built-in or plugin |
| R62 | `harness/kernel/settings_schema.py` → `settings_owner_index` | The capability family is read from the directory a plugin was found under, which is honest for the built-in tree (`harness/plugins/hooks/<name>` → `hooks`) and nonsense for an install root (`<config>/plugins/<name>` → a family called `plugins`). Found from the other end: the new scenario asserted the Plugins pane mentioned its plugin, the assertion failed, and the painted text read `plugins 1/1 loaded` — a family heading the user would have taken for a capability nobody chose. | P14: a parent directory named `plugins` is recognised as an install root and no family is reported, so the pane files installed plugins under "Other" and `tab_for(None)` gives them the default tab (the tab an integration belongs on, which is what P13a built the fallback for). Teeth check: putting `"family": parent` back fails `test_an_installed_plugin_reports_no_capability_family` |
| R44 | `plugins/stores/fts5_search/` | Two related bugs. The row projection read v1 field names (`type`, `source`), so every row the runner indexed from a v2 record said `UNKNOWN`/`UNKNOWN` and `search_memory`'s Source column was decoration; and the only rebuild path ran when the table was empty, so a workspace whose index had gone stale had no repair — there was no CLI entry point at all. | P11: one `record_row()` shared by the live hook and the rebuild; `rebuild_from_sessions` is unconditional; new `harness sessions reindex`; `test_the_search_index_is_rebuildable_from_the_log` |

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

---

## P9 — WS-04a: the log is the source of truth

**Why.** The spec's diagnosis matched the code exactly: two record streams (JSONL + a runner-owned
`transcript_records`), role-based projection that had no way to say "this record is not for the model",
an appender that returned `None` on failure, and a `Session`/runner pair each holding *their own*
`for_session()` store instance — two step counters over one file. Anything not expressed as a record
could not survive a restart, which is also why the client re-rendered approvals and cards from a separate
`history[]` array.

**Decision.** Split WS-04 along the plan's own line: 04a = record v2 + `derive_messages` (+ the
fail-closed append and the shared log, which cannot be deferred without leaving a half-moved state);
04b = `record_kind` on durable events + the client's kind-driven grouping (`liveHistory` off the bus) +
`sessions.truncate` / `session.fork`; 04c = `docs/architecture/SESSION_LOG.md`, the reload scenario and
the memory-projection check. Non-goals respected: no SQLite, no telemetry pipeline, no projection tables,
no history rewriting.

**Changes (11 files + goldens).** `harness/sdk/types.py` (`RECORD_KINDS`, `infer_record_kind`,
`TranscriptRecord.kind/v`), `harness/kernel/transcript.py` (new), `harness/kernel/context.py`
(`MODEL_VISIBLE_KINDS`, `slice_at_last_compaction`, `repair_dangling_tool_calls`, `derive_messages`,
alias), `harness/kernel/runner.py` (shared log via `TranscriptLog.for_session`, `transcript_records`
deleted, prompt recorded inside the guarded region, `TranscriptAppendError` handler),
`harness/kernel/session.py` (eager `Session._log`, `get_history` through it),
`harness/kernel/commands.py` (`sessions.get` → `records`), `harness/kernel/catalog.py` (producer windows),
`docs/ARCHITECTURE.md`, `README.md`, CHANGELOG, 3 new test files, 24 re-baselined `transcript.json` goldens.

**Things that bit me.**
- `upgrade_record_v1` initially wrote `"tool_calls": []` / `"metadata": {}`; `TranscriptRecord.to_dict()`
  omits empty bags, so "upgrade is idempotent" and "reload equals live" both failed. Fix: write a bag back
  only when there is something in it, and *drop* empty ones (the store `setdefault`s `metadata: {}`), while
  leaving a store's non-list placeholder (`"[3 items] … [TRUNCATED]"`) alone.
- The fail-closed test could not fail the turn at first: it monkeypatched `session.transcript.append`, but
  the runner's log held a *different* store instance. That was the bug, not the test — hence `for_session`
  handing out one log (and therefore one store) per directory, built eagerly in `Session.__init__` so the
  session's instance is the canonical one.
- `execute_turn(session=session.info)` passes a `SessionInfo`, which has no `.log`; `getattr(self.session,
  "log", None)` silently returned `None` and services quietly kept its own cache. Sharing has to be wired
  through a path that actually sees the `Session`.
- A file-wide producer scan is unusable once the SDK declares a vocabulary: `RECORD_KINDS` made
  `harness/sdk/types.py` a "producer" of `tool.call`, `approval.resolved`, `settings.changed` in the
  generated EVENTS.md. The fix was a localised window in `_scan_producers`, not an exclusion list.

**Verification.** 33 new tests. `pytest tests/kernel tests/plugins tests/frontends` → 467 passed; full
`pytest -q` → **507 passed** (incl. the 24 scenarios, DOM lane on); `ruff check .` clean; `harness catalog
gen` + `--check` (drift/settings/ui) green; `python tests/frontends/ui_audit.py --strict` exit 0;
`harness plugin doctor --all` ok and `harness plugin test --all` → 220 passed. Golden diff reviewed line by
line: the only changes are `"kind"` and `"v": 2` (plus one dropped empty `metadata`). Scenario coverage stays
warning-only per P7 (`harness catalog --check --section scenarios`: 5 tools / 11 commands uncovered) — note
there is no `harness doctor` command in this repo.

---

## P10 — WS-04b: durable events, recorded policy facts, on-disk repair

Split into two commits so the schema contract and the write-path changes can be reviewed apart.

**P10a — the vocabulary and the turn path** (`daf15fa`). `EventPayload.record_kind` / `.transient` /
`carries_record`, `Event.record`, the generated `events` table in `docs/events.schema.json` and the "In the
log" column in `EVENTS.md`; log-before-emit for turn events (prompt recorded → `turn.started`; marker
appended → `turn.completed` / `turn.error` / `turn.cancelled`); `model.completed` deferred to the record it
names with a flush in `finally`; `TranscriptLog.repair_dangling_tool_calls()`; `MESSAGE_KINDS` for the
listing counter.

**P10b — the writers** (this commit). The gate records `tool.decision` (deny / ask / allow) and `ui.card`
through `PolicyGate.execute(..., log=...)`; the approval broker gained `log_for` + `resolve_recorded` and
writes `approval.requested` / `approval.resolved`; `approval.resolved` now carries the `session_id` it used
to omit (**R42**). Durable kinds therefore now cover every fact §6.7 asks a reloaded session to show.

**Design decision worth keeping.** The spec says "every durable event's payload gets `record`". Two events
cannot honestly do that here, and pretending otherwise would have made the schema a lie:
`tool.result` (a strategy batches a step's results into *one* record, which is the shape a provider
requires, while the gate publishes one preview per call) and `compaction.completed` (the summary record is
written by the loop). They keep `record_kind` — the claim about *what the log holds* — and are marked
`carries_record: false`, with the pairing key stated in the payload docstring and pinned by
`test_tool_result_events_pair_with_the_log`. Deliberately not done: moving the tool-result write into the
gate (it would either split the batch or make the plugin-owned strategy contract wrong about who writes
conversation records).

**What bit me.**
- `upgrade_record_v1` synthesised a `tool_results` bag from `metadata.call_id` on *any* record, so every new
  decision record grew a fake tool result on disk while the live cache kept none — caught by the 04a
  "reload equals live" test, not by anything that cared about decisions. Scoped to untyped v1 lines, and it
  also fixed v1 tool-result lines being typed as `user.message`. The store promotes those keys on write, so
  the promotion is mirrored in the upgrade rather than duplicated as a second writer.
- `test_log_is_source` derived request *k* as `records[: 1 + 2*k]`. It survived because the flow had two
  records per step; one new kind per step broke it. Replaced with "everything before the assistant record
  that request produced", which is the actual invariant and no longer depends on step size.
- The marker text `Turn completed (end_turn) in 9ms` made transcripts nondeterministic: the golden
  normaliser drops `duration_ms` *keys*, not a number baked into prose. Duration moved to metadata.
- `rt.settings.set("kernel.security.permission_mode", ...)` cannot override bootstrap `overrides`, so the
  plan-mode test boots in plan mode instead of switching (a test-side trap worth remembering).

**Verification.** 13 new tests (`tests/kernel/test_durable_events.py`, incl. the spec's
`test_every_durable_event_has_record_kind` verbatim, "a recorded fact never reaches the prompt", the
approval-record and denial tests). Full `pytest -q` with the DOM lane → **521 passed**; `ruff check .` clean;
`harness catalog gen` + `--check` (drift/settings/ui) green; `ui_audit.py --strict` exit 0; `harness plugin
doctor --all` ok, `harness plugin test --all` 220 passed. Goldens: 18 × `transcript.json` + 18 ×
`events.json` changed (46 `tool.decision`, 6 `ui.card`, 2+2 `approval.*` records; `turn.started`,
`model.completed`, `turn.completed`, `tool.decision`, `approval.*`, `ui.card` carry records on the wire);
`requests.json`, `http.json`, `timeline.txt` untouched across all 24 — the strongest available evidence that
recording more did not change what the model reads or what the client paints.

**Remaining in WS-04 (04c):** closed in P11 below — client single renderer over records, the
`reload_equals_live` DOM scenario, `docs/architecture/SESSION_LOG.md` with a generated kind table, and
`harness sessions reindex` for the derived FTS index.

---

## P11 — WS-04c: the client renders the log

**Why.** 04a/04b made the log the only place a fact has to be written; 04c has to make it the only place a
client reads from. Otherwise the work is invisible where users look: a refreshed page still showed less than
the live one (**R43**).

**Changes (7 files + goldens).** `harness/frontends/web/static/app.js` (`renderRecord(rec)` replacing the
role-based loop in `reloadTranscript`, plus `updateToolDecision` filling a missing `plugin · risk`
subtitle); `harness/sdk/events.py` + `harness/kernel/gate.py` (`plugin` on the decision event and record);
`harness/plugins/stores/fts5_search/index.py` (`record_row`, `rebuild_from_sessions`); `harness/cli.py`
(`harness sessions reindex`); `harness/kernel/catalog.py` (generated `RECORD_KINDS.md`);
`tests/scenarios/_lib/web_runner.py` (`transcript_live`, second-boot `reload_equals_live`,
`reload_missing_lines` for the DOM lane) and `tests/scenarios/test_scenarios.py`
(`dom_reload_contains_live_lines`); new scenario `reload_equals_live_web_dom` + its recording in
`make_recordings.py`; `docs/architecture/SESSION_LOG.md`, `docs/ARCHITECTURE.md` §5 pointer, README §5.

**Design decisions worth keeping.**
- *Paint facts, not summaries.* `renderRecord` calls the same methods the live handlers call instead of
  growing a second renderer for history. That is the whole point of "reload = replay": if a fact needs a
  special reload painter, the live path and the reload path will drift again in one release.
- *A reloaded tool card starts `running`.* The verdict belongs to the later `tool.decision` / `tool.result`
  records. Defaulting to `completed` would have made a crashed turn look successful after a refresh, which is
  the opposite of what a log is for.
- *Assertions compare lines, not positions.* One assistant record carries a step's whole batch of calls, so
  a reload paints sibling tool cards together and the approval card after them, while live interleaved them.
  `dom_reload_contains_live_lines` checks that no fact is missing and `reload_timeline_contains` pins the
  ones a role-based renderer used to drop; neither pretends the two orders are the same.
- *The generated kind table reports measured coverage, not a source scan.* Markers like
  `("_record(", "kind=")` missed every kind written through `_append_marker` (there the kind is a variable),
  so "no producer" would have been wrong for `turn.error`. Counting the scenario transcripts that actually
  hold a kind is reproducible and honest: it says `0 scenario(s)` for the reserved `job.*`/`goal.*` and for
  `tool.call`, which is exactly the state of things.

**Things that bit me.**
- The DOM goldens proved the client was right before the client was fixed: `timeline.txt` is captured from
  the **live** session, so it stayed green while a refresh lost cards. The reload report is a separate field
  (`dom["reload"]["timeline_text_on_load"]`) and needs its own assertion — that is why this scenario has
  both `reload_equals_live` and the line-set check.
- The web lane had no live-vs-reloaded comparison at all: `reload_equals_live` existed only in
  `kernel_runner.py`, and `web_runner.py` fetched `sessions.get` on the *same* daemon. Left alone, the flag
  would have compared a reload with itself; it now boots a second kernel over the same workspace, which is
  also what restarting `harness ui` does.
- Adding `plugin` to the record metadata first, and only then discovering `tests/frontends/test_static_contract.py`
  forbids the client reading a key the *event payload* does not declare — good guard, right outcome: the field
  belongs on both. Filling a missing `.card-meta` in the decision painter (rather than overwriting) is what
  keeps the live view's `fs-tools · write` intact.
- `record_row()` moved the memory scenarios' golden: the `Source` column of a `search_memory` hit is now
  `user` instead of `UNKNOWN`, which changes one card body in one `timeline.txt`. Re-baselined deliberately.
- A dataclass field with a default cannot sit before a field without one — `WebRunResult.transcript_live`
  belongs after `dom`, not in the middle.

**Verification.** 12 scenarios' worth of goldens reviewed line by line: the entire diff is
`+ "plugin": "<owner>"` — 46 record-metadata rows across 18 `transcript.json`, and 92 lines across 18
`events.json` (each decision event names the plugin once in its payload and once in the record it carries),
plus 6 `UNKNOWN` → `user` cells in the two memory scenarios and the one `timeline.txt` that shows
them. `requests.json`/`http.json` untouched in all 25. Full `pytest -q` with the DOM lane → **523 passed**
in 328 s (521 at P10b, plus the new kernel test and the new scenario);
`ruff check .` clean; `harness catalog gen` + `--check` exit 0 (new `RECORD_KINDS.md` drift-checked, 58
settings all reads declared, ui ok, refs ok; the two warnings are the pre-existing README-completeness and
scenario-coverage ones); `python tests/frontends/ui_audit.py --strict` exit 0; `harness plugin doctor --all`
ok and `harness plugin test --all` → 220 passed; 1 new kernel test (`test_the_search_index_is_rebuildable_from_the_log`,
verified to fail on the pre-fix projection).

**Scope correction.** P9's split note promised `sessions.truncate` / `session.fork` inside 04b. Neither is in
WS-04's spec, neither is one of the review's findings, and `sessions.get`'s `history` alias was dropped for
the same reason (see the deviation note in the CHANGELOG). Both are dropped here rather than left quietly
unimplemented: editing a session's history is a *checkpoint* feature and belongs with WS-07's diff view,
where the base/head pair makes a truncate honest about what it discards.

**Still open in WS-04 (with reasons).** Two of the plan's scenarios are not written:
`crash_between_call_and_result` (needs a kill between the two appends — a mid-turn `SIGKILL` inside the
scenario runner is not expressible in the current harness) and `append_failure_fails_closed` (needs a store
that raises on the third append). Both properties are pinned by kernel tests instead
(`test_a_store_that_cannot_append_stops_the_turn`, `test_dangling_tool_calls_are_repaired_on_disk`), which is
where a monkeypatched store is cheap and a recorded session would be theatre. §3.6's `checkpoint.created`
records stay out of scope for WS-04 (they belong with WS-07's diff view), and `settings.changed` records are
declared but unwritten; the generated table shows both as `0 scenario(s)` rather than the docs claiming them.

## P11b — WS-01: the record path, and the replay machinery promoted to the SDK

**Spec.** `WS-01_record_replay_tests.md`, the parts still open after the suite landed: the recorder wired into
`HARNESS_REPLAY=record`, `harness/sdk/testing/replay.py` as the public home of both providers,
`ScriptedProvider(strict_exhaustion=…)`, pytest markers, the two fail-first unit-test files §5 lists, and
`docs/testing/SCENARIOS.md`.

**Built.** `_lib/record.py` (mode detection, the tee install, `ProviderHandle.finish()`) → the SDK
(`harness/sdk/testing/replay.py`), `_lib/replay_provider.py` and `_lib/codec.py` reduced to views, both runners
wired so a scenario boots either mode without knowing which it is in, `test_record_replay.py` (4 tests),
`tests/kernel/test_replay_provider.py` (10), `tests/scenarios/test_normalize.py` (6), markers
`scenario`/`web` in `pyproject.toml` with `kind_of()` supplying them at collection, and the contributor doc.

**Evidence.** `SCENARIO_DOM_WAIT_MS=6000 pytest -q` → 539 passed (523 at P11 + 4 record-path + 10 SDK replay
+ 6 normaliser + … see the gate line in the commit). The goldens of all 25 scenarios are byte-identical before
and after this change (`requests.json`/`http.json` included): the record path is additive, and the 12 events
that *did* move during development — `turn.started`'s `data.options.provider` — moved back once the harness
stopped trying to be more explicit than the client. Teeth checks: removing `strict_exhaustion`'s raise fails
`test_exhaustion_is_an_error_when_the_script_is_strict`; removing `_HEX_ID` normalisation fails
`test_an_id_in_prose_is_relabelled_exactly_like_the_key_version`; both restored.

**Tidy pass (same commit series).** Four loose ends that made the suite lie about itself: record mode wrote
straight over a committed `provider.jsonl` (now guarded by `HARNESS_RECORD_OVERWRITE`, checked before the run
starts, `test_record_mode_refuses_to_rewrite_a_committed_recording`); `make_recordings.py` covered 24 of 25
fixtures so one recording had no reproducible source (now 25, plus a fixed-point test and a completeness
check that fails for a new synthetic scenario with no entry); `docs/ARCHITECTURE.md` claimed 24 scenarios and
`additions/README.md` still described the suite as `additions/scenarios`, in progress, with a stale run
command; and the SDK-side utilities were undiscoverable from the plugin-authoring docs (now a
`docs/PORTING_RECIPES.md` §2 recipe). Nothing else in the corpus moved.

**Deviations, with reasons.**
1. `ReplayProvider.name` is `replay`, not the spec's `scenario`. Every scenario already pins
   `kernel.provider: replay`; one key for both modes means a recording and its replay produce the same goldens,
   and `record.py`'s tee inherits it. (`plugin_name="scenario"` is still what the registry records as the
   owning plugin, which is the spec's intent for provenance.)
2. `tools/headless_timeline.mjs` stayed at `tests/scenarios/_lib/node/headless_timeline.mjs`. It is a lane of
   this suite, its imports (`_lib/dom.py`-adjacent conventions, jsdom resolution by walking up) are the suite's,
   and keeping it inside means `git mv tests/scenarios …` moves the whole thing in one step.
3. `_last_run/` is ignored by `tests/scenarios/.gitignore` rather than the root one — the rule belongs with the
   directory that produces the actuals.
4. The spec's §5 row `test_scenario[plain_answer-web]` and its "fail until the §8 client fix is in" narrative
   were consumed in the original WS-01 PR; what failed first here was the record path itself (R45), which is the
   same discipline pointed at the code that was actually missing.
5. `provider.requests.json` is `{provider, model, requests}`, not the spec's bare request list: a replay needs
   the recorded model id to name itself (R46), and the legacy list shape is still tolerated on read so a fixture
   recorded before this change replays unchanged.

## P12a — WS-05a: feature-owned UI, kernel side

**Spec.** `WS-05_feature_owned_ui.md` §3.1–§3.3 (manifest `ui` block, the built-in declaration, settings groups
from schema), §3.4 (the contract test) and the `commands.list` half of §3.1. The client restructure (§3.2) is
WS-05b; the served plugin modules, the two scenarios and `--check ui`'s final form are WS-05c.

**Already true when the phase started** — worth recording, because the spec's §2 "current state" is a snapshot
of `main` before the user's refactor and WS-02/04: the settings fallback schema in `settings.js` was *already*
deleted (WS-02 made `settings.schema` the only source), `index.html` held no `data-notice` placeholders and no
ten stub panels — three tabs remained — so three of the six fail-first rows in §5 had nothing left to fail. They
are kept as regression tests anyway (`test_no_placeholder_bindings`, `test_settings_js_holds_no_schema_of_its_own`),
which is the honest version of "red first": the *invariant* is pinned, not the archaeology.

**Built.** `UiContribution`/`UiPanel` in the SDK manifest with the validation matrix; `ui.toml` for the shipped
client and `registry.ui_contributions()` (cleared on unload, so disable ⇒ gone); `commands.list` and
`ui.contributions`; `order_groups` (**R47**); the generated `UI_CONTRACT.md` panels/kind/bindings tables; the
strengthened `--section ui` gate; 30 new tests, 5 of which were shown to fail against deliberate mutations.

**Teeth.** Adding `<div class="studio-tab" data-tab="ghost">` to `index.html` fails both the contract test and
`harness catalog --check --section ui`; pointing the `sessions` panel at `sessions.purge` fails the gate naming
`declared by web`; deleting the `case 'tool.result':` branch from `renderRecord` fails
`test_every_durable_record_kind_is_painted_or_excused` with exactly that kind listed; a `data-command="nope"`
binding fails the binding test by name. All four restored before commit.

**Deviation.** The spec's acceptance line says `harness catalog --check ui`; the CLI's actual form is
`harness catalog --check --section ui` (`--section` is repeatable, from WS-02). Nothing else about the
acceptance changed, and the `kinds_without_renderer` count stays *advisory* in the gate on purpose: `turn.completed`
legitimately has no renderer, and the list of such kinds — with a reason each — belongs to the test that a client
author reads (`UNPAINTED_KINDS`), not to a build gate that would be bypassed by someone adding a bogus
declaration to `ui.toml`.

## P12b — WS-05b: the client reads the registry

**Built.** `harness/frontends/web/static/registry.js` (`RendererRegistry`, `PanelRegistry`, `CommandBinder`,
`GENERIC_RENDERERS`, `NO_RENDERER`, `textContentOf`), eleven painters in `static/renderers/*.js` — one file per
durable record kind, named for it — and three panel modules in `static/panels/{artifact,sessions,economics}.js`.
`app.js` shrank from 1285 to 1242 lines while *gaining* the wiring and its documentation: `renderRecord` is a
one-line dispatch, `switchStudioTab` asks the registry, `loadSessions` refreshes the sessions pane and keeps only
`noteSessionRows` (the cross-cutting part: adopt the active id, update the header), and `setupRegistries` boots
`ui.contributions`, imports the plugin modules the kernel lists, and reports both gap directions.

**Behaviour preserved, on purpose and by test.** Each renderer body is a transcription of the `case` it replaces
— same app methods, same arguments, same comments about *why* (`tool.call`s are appended with status `running`,
`ui.card` reads `metadata.card`, `turn.error` falls back to `'Turn failed.'`) — so the goldens did not move: the
5 DOM lanes, `reload_equals_live_web_dom` and `dom_equals_kernel_transcript` pass against the unchanged
recordings, and the `artifact` panel's `render` is a documented no-op because `showArtifact` pushes into that host
from the card painter rather than polling a command.

**Teeth (four mutations, all reverted).** Removing `static/renderers/tool.result.js` fails
`test_the_renderer_map_is_the_modules_on_disk` *and* `test_every_durable_record_kind_is_painted_or_excused`;
deleting `turn.completed` from `NO_RENDERER` fails the coverage test naming that kind; adding an orphan
`static/panels/vault.js` fails the panel-declaration test with the three sets printed side by side; using
`panel-bogus` in `registry.js` fails `ui_audit --strict` ("classes used but defined in no stylesheet"). A fifth
mutation — the `selectSession` bug actually made during the move — is what produced R52's test.

**Deviation.** The spec's 05b line says "interactive elements move from hand-written handlers to
`data-command`". The binder exists and the generic panel path uses it, but the shipped controls were left
alone: every one of them either needs a runtime id (`approve-approval`/`deny-approval` in a card, the session
chips, `save-secret` for a named key) or has a local side effect (the composer clears itself and re-enables,
Stop closes the thinking block, New Session prompts for a title first). A `data-command` on those would be a
second path around the same command with the ordering in JS anyway, and half a binding is worse than none. The
catalogue therefore reports "no `data-command` bindings" *with the reason*, and `--check --section ui` fails
the moment one appears that names an unregistered command.

**Gate.** `pytest -q` 581 passed; `ruff check .` clean; `harness catalog gen && harness catalog --check`
drift-clean (UI_CONTRACT.md regenerated: renderer modules listed, comment-derived binding gone);
`python tests/frontends/ui_audit.py --strict` 0 findings (0 used-but-undefined, 0 defined-but-unused);
`harness plugin doctor --all` clean.

## P13a — WS-05 follow-up: settings tabs by family, sections by owner

**Why this phase exists.** The user's reaction to the running Studio — "it's basic, there are no actual plugin
integrations and settings are bare bone" — was checked against the repository rather than argued with, and it was
right in a mechanical way: 39 plugins load, only 13 ship a `settings.schema.json`, and every property that did
not name its own `x-ui.group` was defaulted to one catch-all tab (22 of 44 panel properties), which the client
then rendered as a flat list with nothing saying who owned what. Three phases followed from that; this is the
first (the other two are the Plugins pane and serving a plugin's `static/`, in P13b/P13c).

**Built.** `FAMILY_TABS` + `tab_for` + `plugin_display_name` + `settings_owner_index` in
`harness/kernel/settings_schema.py`; `with_ui_defaults(prop, namespace, *, family, owner_summary)` fills in the
tab *and* the owner metadata, so `Commands.settings_schema` (client) and `harness catalog` (document) cannot
disagree — both call the same index; the four schemas that had hand-written the catch-all group lost that line so
the derivation applies end to end; `settings.js` renders one section per owner; `styles.css` gained
`.settings-owner*`. 8 new tests; the tabs went from 6 (one of them a dumping ground) to 8 derived ones, and
`SETTINGS.md` grew `Hooks`/`Context`/`Bridges & Integrations` sections it had been silently dropping.

**Findings.** R53 (the client seeded its own open tab — a fresh daemon's settings modal looked empty) and R54 (the
catalogue's document renderer filtered through a literal tab list, which no freshness gate can see). Both were
produced by *this* change rather than existing separately: deriving something is exactly what exposes the places
that kept a copy.

**Gate at this point.** `pytest tests/kernel/test_settings_groups.py tests/kernel/test_catalog.py tests/frontends -q`
→ 81 passed; `harness catalog gen && harness catalog --check` drift-free; `ui_audit --strict` 0 findings. Full
suite run after P13c.


## P13b — WS-05 follow-up: the Plugins & Integrations pane (the client half of "show the integrations")

**Why this phase exists.** P13a made a plugin's settings findable; nothing yet made the plugins themselves
visible. The user's complaint was that the Studio looked like a chat box with a settings dialog — no integration
surface at all. Verified before starting: 39 discovered plugins, 0 client calls to `plugins.*`, 3 declared panels,
`ui.contributions.client_modules: []`, and one catch-all settings tab holding half the properties.

**Built.** `plugins.list` rows gained `disabled`, `family`, `has_settings`, `settings_count`, `settings_group`,
`panels`, `record_renderers`, `client_module` (additive: `harness plugin list` and the TUI keep their columns, and
`catalog.py`'s `plugin_info` reads named keys, so nothing had to change there); `harness/frontends/web/ui.toml`
declared `{ id = "plugins", command = "plugins.list", renderer = "list" }`;
`harness/frontends/web/static/panels/plugins.js` renders the cards (family sections, per-card state badge,
settings count with its tab, UI-contribution summary) with Enable/Disable as plain `data-command` attributes and
`Configure` routed through `ctx.select(group)` → `app.handlePanelSelect('plugins', …)` → `openSettings(group)` →
`SettingsPanel.setGroup`. `registry.js` gained `escapeAttr` (attribute values need more than a text-node escape);
`styles.css` gained the `.plugin-*` rules.

**The contract it forced.** `test_panels_are_declared_backed_and_mounted` required every declared panel to appear
as a `data-tab` somewhere in the client sources, which made the pane impossible to add the way WS-05 says a panel
is added — by declaration. Rather than bolt markup onto `index.html` to satisfy the check, the rule became
*reachability*: declared ⇒ backed ⇒ paintable (registered module or generic renderer), plus no module without a
declaration, plus `GENERIC_RENDERERS == VALID_UI_RENDERERS`. Relaxing "present in markup" to "reachable" is only
honest if something proves mounting actually works, so the DOM lane now reports `tab_ids` (4 tabs, `plugins`
among them, with `index.html` untouched), `plugin_cards` (39), `plugin_families` (9), and the
`data-configure` → settings-tab jump (`Bridges & Integrations` opened, active).

**Findings.** R55 (a successful command reported as failed, because the announce shared the command's `try`) and
R56 (Node's web globals shadowing jsdom's, which made the announce path untestable in the only lane that clicks
real controls). Both came out of *clicking in the lane* rather than reading code, which is the argument for
keeping a DOM lane around. Also checked and cleared: the 11 `test_*` names in `docs/PORTING_RECIPES.md` that do
not live under `tests/` are either conformance-kit methods (`harness/sdk/testing/kits.py`) or fenced examples in
the doc itself — no prose drift, so no new gate was added for it.

**Honest limits of this phase.** Enabling and disabling cannot take effect inside a running daemon —
`harness daemon` has no `restart` subcommand and nothing hot-unloads a plugin — so the pane says "takes effect
the next time the daemon starts" and shows `off next start` on the badge instead of faking a live toggle. And the
pane can only report the UI plugins *declare*: still 0 of 39 declare `[ui]`, which is WS-05c's job (serving a
plugin's `static/` and the two scenarios that prove a plugin's own panel and settings tab appear when it is
enabled).

**Gate at this point.** `pytest tests/frontends tests/kernel/test_plugins_command.py tests/kernel/test_settings_groups.py tests/kernel/test_ui_contributions.py -q`
→ 99 passed (9 new: `tests/kernel/test_plugins_command.py`, which is the first test `plugins.list` ever had);
`pytest tests/scenarios -k "plain_answer_web or session_reload_web_dom or approval_allow_web_dom"` → 3 passed;
`harness catalog gen && harness catalog --check` → all `[ok]` (two pre-existing `[warn]`s: plugin README
completeness, scenario coverage), with `ui: … mounts 4 declared panel(s)` and
`data_command_bindings: [plugins.disable ✓, plugins.enable ✓]`; `python tests/frontends/ui_audit.py --strict` →
0 findings (0 used-but-undefined / 0 defined-but-unused, `panels/plugins.js` 160 lines); `ruff check harness tests`
clean. Full suite: `pytest -q` → **598 passed** (6:05), the first complete run since P13a landed.

## P14 — WS-05c: a plugin's own client module is served, installed plugins load, and a scenario installs one

**Why this phase exists.** The user's verdict on the running Studio was "it's basic … there are no actual plugin
integrations and settings are bare bone". Verified, that was a statement about layers, not effort: 39 plugins
were loaded and **zero** were visible, `ui.contributions.client_modules` was `[]`, no built-in declared `[ui]`
at all, and the mechanism phases (WS-01…05b) had stopped exactly where a plugin would need the last mile — a file
the browser can fetch, and a plugin that actually loads when a user puts it on disk. WS-05c was the named
workstream for that last mile; the pack's two acceptance scenarios ("a plugin panel appears when the feature is
enabled", "a settings tab is built from a plugin's schema") were its only proof, and neither existed.

**Built.**
* `harness/sdk/plugin_static.py` — `CLIENT_MODULE_ROOT`, `CLIENT_MODULE_SUFFIXES`, `plugin_dir`,
  `client_module_url`, `client_module_file`, `client_module_trusted`. In the SDK, not the kernel, because
  `test_frontends_allowed_imports` lets a frontend import `harness.sdk`, `harness.kernel.commands` and
  `harness.kernel.bus` and nothing else; `harness/kernel/plugin_paths.py` is the kernel-side façade. Widening the
  allow-list would have been the wrong fix: the boundary is the reason the URL and the route cannot drift.
* `server.py` registers `add_get("/static/plugins/{name}/{tail:.+}", _handle_plugin_static)` *before*
  `add_static("/static/")` (aiohttp matches in registration order, so a later route would be swallowed) and answers
  every refusal with 404. Discovery applies the same trust rule up front: `discover_directory` now passes
  `trust=tier.value` into `PluginManifest.load`, which **clears** an untrusted `client_module` with a `[warn]`
  instead of raising — `bootstrap.discover_all` is unguarded, and a gate that raises turns one workspace plugin's
  typo into a kernel that will not boot.
* `PluginRegistry._import_entry` (R60) so `entry = "plugin:MyPlugin"` resolves inside the plugin's own directory,
  loaded under a unique `sys.modules` name; no `sys.path` mutation.
* `ui.contributions` publishes `accepts` per panel and `registry.js` sends `session_id` only where the command
  takes it (R61); `settings_owner_index` stops calling an install root a capability family (R62).
* Scenario lane: `Scenario.config_files` (inline text, or `@_fixtures/…` to copy a tree) written by the new generic
  `seed_tree(root, files)` — which `seed_workspace` now delegates to — and *both* lanes seed the config dir
  **before** `bootstrap_kernel`, because a scenario that boots first can only ever test a built-in. The DOM probe
  clicks every declared tab and reports `panel_texts`, hands back `client_module_urls` and per-URL fetch statuses,
  optionally clicks a plugin card's switch (`plugin_disable`, before/after badge), and runs the whole probe again
  on the restart pass. New expectations: `dom_tabs_include`, `dom_tabs_exclude`, `dom_panel_text`,
  `dom_plugin_modules_served`, `dom_disable_plugin`, `dom_restart_tabs_include`, `dom_restart_tabs_exclude`.

**The contract it forced.** `dom_plugin_modules_served` was written as "the browser fetched 200 *and* the set of
URLs the check probed equals `client_module_urls`, the list the page itself was handed" — asserting the fetched set
alone would pass even if the client were told about nothing. The fixture's module URL is checked from inside the
page (`fetch`), not from Node, so it is the same request a browser makes.

**Findings.** R58 (the advertised URL landed in the client's own tree and the trust gate named a tier that does not
exist, so it could never fire), R59 (three ways to be wrong about one address, the third — a comparison with `name`
on both sides — only visible once a test tried to pin it), R60 (installed plugins never loaded: no test had ever
installed one), R61 (the generic renderer's unconditional `session_id` worked for exactly the panels the repo
ships), R62 (an install root read as a capability family, found because a scenario's assertion failed and the
painted text was the evidence). Two lane bugs came out of the same run: a 400-char cap on `panel_texts` made a real
pane look empty (now 20 000, and the report is only for assertions — goldens never carry it), and a
scenario with `turns: []` still tripped `replay_consumed` on the recording it had been copied — the
gate was right, a scenario should not ship bytes it never asks for, so the second scenario got a
throwaway turn and both got entries in `make_recordings.py`, which
`test_every_synthetic_scenario_can_be_regenerated` demands of every synthetic scenario so the corpus
stays byte-reproducible.

**Honest limits, stated where they were found.** The DOM lane cannot *execute* a plugin's client module: the probe
imports the shipped client from disk, Node's ESM loader refuses `http:` (`ERR_UNSUPPORTED_ESM_URL_SCHEME`), and a
root-relative `/static/...` import from a `file://` base resolves to `file:///static/...`. So no scenario asserts
the module's sentinel sentence — the lane asserts the two ends around it (the app was handed the URL; the browser
gets the module's bytes; the panel mounts and paints) and the fallback is the designed degradation, proven
independently by `test_the_url_the_kernel_advertises_is_the_url_that_serves_the_bytes`. Teaching the lane to import
plugin modules would mean a Node loader hook that maps `http://` to disk reads, i.e. the test would be doing the
product's job; the browser remains the only honest place for it, which is why the preview was used as the second
half of this phase's evidence:

```
$ cp -r tests/scenarios/_fixtures/plugins/probe-vault ~/.harness/plugins/    # the shape a user installs by hand
$ curl -s $PREVIEW/static/plugins/probe-vault/probe.js → 200 text/javascript 1063B
$ curl -s $PREVIEW/static/plugins/probe-vault/settings.schema.json → 404      # undeclared sibling
$ curl -s $PREVIEW/static/plugins/nope/x.js → 404                              # not installed
POST /commands/ui.contributions → panels [artifact, economics, plugins, probe-vault, sessions],
  client_modules [{plugin: probe-vault, path: static/probe.js, url: /static/plugins/probe-vault/probe.js}],
  settings_groups includes "Probe Vault", unbacked_panels []
POST /commands/plugins.list → 40 rows; probe-vault: tier local, loaded true, family null,
  settings_count 1, settings_group "Probe Vault", panels ["probe-vault"]
```
Note what that run also settled: a plugin under `<workspace>/.harness/plugins` is tier `workspace`, so its
`client_module` is *dropped by design* and the URL 404s — the first copy of the fixture went there, and the 404 was
the rule working, not failing. (`<config>/plugins/` is also where built-in stores keep data —
`store-cost-ledger/economics.json` — and discovery ignores any directory without a manifest, so the two coexist.)

Still open, deliberately: nothing hot-unloads a plugin, so the pane and the scenario both speak of the *next*
start; `plugin.error` remains an event rather than a durable record, so `loaded: false` cannot distinguish
"broken since boot" from "waiting for a restart" (R57, owned by the health workstream); and there is still no
`harness plugin install` — the fixture is copied by hand, which is what the lane does too.

**Gate at this point.** `pytest -q` → **629 passed** (6:59; P13b ended at 598, +31 tests). New:
`tests/kernel/test_plugin_static_paths.py` (16), `tests/frontends/test_plugin_static.py` (10),
`tests/kernel/test_settings_groups.py` (9, +1), `tests/kernel/test_ui_contributions.py` (21),
`tests/frontends/test_ui_contract.py` (+1, key set now `{id,title,command,renderer,plugin,accepts}`), and the two
scenarios `plugin_panel_appears_when_enabled` / `settings_tab_from_plugin_schema`.
`harness catalog gen && harness catalog --check` → all `[ok]` (same two pre-existing `[warn]`s);
`python tests/frontends/ui_audit.py --strict` → 0 findings; `ruff check harness tests` clean. Teeth checks, each
one by putting the bug back: unregister the static route → scenario fails
`client module URLs the browser could not fetch: {'/static/plugins/probe-vault/probe.js': 404}`; revert the
`accepts` conditional in `registry.js` → `panel 'probe-vault' does not show 'probe-vault'; it shows ''`; restore
`"family": parent` → `test_an_installed_plugin_reports_no_capability_family` fails.
