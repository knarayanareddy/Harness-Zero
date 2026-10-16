# Harness-Zero — end-to-end review of `main` @ `158c836` (post-remediation)

**Scope.** `main` after the build agent's commit `158c836` *"fix(v0.2.1): remediate all code review findings"*
(15 files, +1642/−2027, pushed directly to `main` on 2026-10-15 21:59 +0200 on top of PR #4's merge `58f13b5`).
The question asked: the harness "is still all over the place" — is that true, where exactly, and what is the
shortest ordered path out. Everything below was verified against the checked-out tree, not the commit message.

**Verdict in one paragraph.** The commit is a real clean-up, not a cosmetic one: `server.py` went from 1257 to
578 lines (21 unrouted REST handlers gone, `/commands/{name}` + `/events` are now the only API), `index.html`
from 1452 to 289 lines (the 17 mock-up pages are gone, every id the JS touches exists), the named-SSE fix from
PR #4 survived the conflict resolution, and all suites pass (ruff clean; `pytest tests` 360; `harness plugin
test --all` 180; the 20-scenario flow suite passes its DOM expectations). **The remaining "all over the place"
feeling is accurate, and it has seven concrete causes** — none of them is "more clean-up of the same kind":
(1) no shared contract for payload shapes between kernel and UI (the new Economics tab reads four keys the
kernel never emits and therefore always shows `$0.0000 / 0 tokens`), (2) two styling systems with 128 utility
classes that no stylesheet defines, (3) facade remnants of the "studio" layout (an Artifact pane nothing
populates, a terminal drawer nothing shows, static "Autonomous / main / Ready" pills), (4) session lifecycle
decided in the browser (every daemon restart + page load creates another empty session), (5) tests in four
places with three jsdom bootstraps and two npm roots, and the suite that would have caught the regressions is
not in CI, (6) version/changelog drift (the `[0.2.1]` release section was rewritten after the tag), and (7) a
handful of kernel loose ends the UI work exposed (`CostLedger.totals()` ignores `session_id`,
`cost.updated.session_usd` is the *turn* cost, Ollama models are "configured" while Ollama is down).
This PR fixes the one hard regression (Economics keys) with a contract test, re-baselines the 7 scenario goldens
that legitimately moved, adds a static UI audit tool, and leaves the rest as an ordered plan (§6) with acceptance
commands, because each remaining item is a design decision the build agent should take once, not a patch.

---

## 1. What was run

Fresh `.venv`, `pip install -e ".[dev]"`, Node 22, `npm ci` (root) and `npm --prefix additions/scenarios/_lib/node ci`.
Hermetic env for every run: `export HOME=$(mktemp -d)` **then** `export HARNESS_CONFIG_DIR=$HOME/.harness`
(two statements — see Appendix A for why one statement silently points at the real `~/.harness`).

| Check | Result on `158c836` | Note |
| :-- | :-- | :-- |
| `ruff check .` | clean | claim ✅ |
| `pytest tests -q` | **360 passed** (126 s) | commit message says 354; user reported 360 |
| `harness plugin test --all` | **180 passed** | claim ✅ |
| `pytest tests/frontends/test_ui_smoke.py` | 1 passed (14 s) | jsdom, **not Playwright** (see §2) — silently *skips* if `node_modules/jsdom` is absent |
| `pytest additions/scenarios -q` (strict, DOM lane on) | **13 passed, 7 failed** → after golden refresh **20 passed** | all 7 failures are the same benign drift: a leading `session.started` event because `init()` now auto-creates a session; **every DOM expectation (F-1…F-12) still passes** |
| `python additions/tools/ui_audit.py` (new) | 0 missing ids · 12 static-only ids · **128 used-but-undefined CSS classes** · 63 defined-but-unused · 36 inline `style=` | the numbers behind §4.2–4.3 |
| Live probes against `harness ui` (mock provider) | see §4.1, §4.4, §4.7 | `models.list`, `economics`, 3× daemon restart |

---

## 2. Claims in the commit message vs. what the tree shows

| # | Claim | Fact | |
| :-- | :-- | :-- | :-- |
| 1 | Named SSE listeners for "turn.started, **turn.delta**, …" | `events.js` `KNOWN_EVENT_TYPES` = the 30 types of `docs/events.schema.json` + `heartbeat`; covers every type Python emits; `turn.delta` does not exist anywhere (message typo). `heartbeat` is a dead entry — the server sends heartbeats as SSE *comments* (`: heartbeat`), which never reach a listener. | ✅ code / ⚠️ message |
| 2 | Nested `options:{model,provider}` payload + auto-create session | True. `api.js submitTurn` folds extras into `options`; `commands.submit_turn` accepts both forms (PR #4). Auto-create has a side effect (§4.4). | ✅ / ⚠️ |
| 3 | "17 mockup facade pages removed, zero data-notice/noop/placeholders" | `index.html` 1452 → 289 lines; 0 `data-notice`, 0 `noop`, 0 inline handlers; all 39 JS-referenced ids exist. **But** the page still contains static mock text: `Autonomous` pill (l.74), `Architecture Plan` banner (l.207), `main` / `● Ready` status bar (l.234–236), `Cache Tier: Invariant` (l.271), and an Artifact Studio pane that no code ever fills (l.218–221). | ✅ mostly |
| 4 | ~650 lines of unrouted legacy REST handlers deleted from `server.py` | 1257 → 578 lines; routes now exactly `/`, `/ui`, `/health`, `/auth/ticket`, `/events`, `/commands/{name}`, static; `api.js` only calls those. | ✅ |
| 5 | `sessions_switch` hardened | `validate_session_id` → `KeyError` on unknown id → 404 via the mapping; malformed id → 400. Verified live. | ✅ |
| 6 | `_is_provider_configured()` + `configured` flag solves phantom models (F-5) | Not for the case F-5 was about: with no Ollama running, `models.list` returns `llama3.2:latest`, `qwen2.5-coder:latest`, `deepseek-r1:latest` flagged `configured: true` (`commands.py:279` hard-codes `ollama → True`). Unconfigured API providers *raise* inside `list_models()` and are dropped, so the "Unconfigured (Requires API Key)" `<optgroup>` in `populateModels()` can never render. | ❌ |
| 7 | Exception mapping `ValueError→400`, `KeyError/FileNotFoundError→404` | `server.py:343–346`; verified (`files.read` missing → 404; bad session id → 400). Unknown command name → 400 (arguably 404). | ✅ |
| 8 | `test_settings_security.py` migrated to `/commands/*` | 7 tests, all through `/commands/*`; passes. Docstring still says `POST /settings`. | ✅ |
| 9 | New `test_static_contract.py` | 7 useful checks (ids, no `alert`, no `/turn/stream`, no inline handlers, CSP, schema↔`KNOWN_EVENT_TYPES`). Good pattern — extended in this PR (§5 R1). | ✅ |
| 10 | "Playwright UI smoke test" | `tests/frontends/ui_smoke.mjs` is a trimmed fork of `review/tools/ui_smoke.mjs` (jsdom + fetch-based EventSource polyfill). Root `package.json` depends on `jsdom` only. The test *skips* when jsdom is not importable from the cwd. | ⚠️ |
| 11 | "354 hermetic + 180 plugin conformance tests pass" | 360 + 180. | ✅ |
| 12 | Node setup + `npm install` in CI | Present. `npm install` with a committed lock file should be `npm ci`. `additions/scenarios` is **not** run in CI — which is why the 7 golden drifts went unnoticed. | ⚠️ |
| 13 | CHANGELOG updated | The new entries were written **into the `[0.2.1]` release section** (l.36–52), but tag `v0.2.1` = `1e5eb81`, which predates PR #3, PR #4 and this commit. The same section describes an `/api/sessions/{session_id}/turn` endpoint that has never existed. `[Unreleased]` (l.10) is where this belongs. | ❌ |
| 14 | ruff clean, unused imports removed | ✅ | ✅ |

---

## 3. What the refactor got right (keep it)

* **One API surface.** `/commands/{name}` + `/events` + `/auth/ticket` + `/health`. `api.js` is 172 lines and has
  exactly three `fetch` targets. This is the first version where "how does the UI talk to the kernel" has one answer.
* **The page is now small enough to reason about** (289 lines) and the static contract test keeps it honest.
* **Nothing from PR #4 regressed.** Transcript reload on refresh (F-11), tool-card decision/output (F-12),
  named SSE delivery (F-4), approvals, Tirith blocks, memory, thinking → all 20 scenario DOM expectations pass.
* **Error handling at the boundary** is explicit (`HarnessError|ValueError → 400`, `KeyError|FileNotFoundError → 404`,
  everything else logged + 500) and `submit_turn` failures surface as a notice instead of a hung spinner.
* **CI runs Node**, so the jsdom tests actually execute on GitHub.

---

## 4. Why it still feels "all over the place" — root causes with evidence

### 4.1 There is no shared contract for payload shapes between kernel and UI

The same concept — spend — has three shapes and no test tied them together until this PR:

| Producer | Shape |
| :-- | :-- |
| `cost.updated` event (`runner.py:482–495`) | `{turn_usd, session_usd, cap_usd, unknown_pricing}` — and `session_usd` is assigned `self.turn_cost_usd`, i.e. it is the **turn** cost |
| `economics` command / `GET /health .spend` (`commands.py:424–435`) | `{spend_cap_usd, totals: Usage.to_dict()}` = `{input_tokens, output_tokens, cache_read_tokens, cache_write_tokens, reasoning_tokens, cost_usd}`; `totals` is `null` before the first model call; `CostLedger.totals(session_id=…)` (`ledger.py:130`) **ignores `session_id`** — "session spend" is lifetime spend |
| `renderEconomicsTab()` as committed (`app.js:474–484` in `158c836`) | read `data.session_usd`, `data.cap_usd`, `data.token_usage.{input,output,total}`, `data.totals.total_cost_usd` — **none of which exist** → the tab always rendered `$0.0000 / $5.00` and `0` tokens (the `5.00` was a literal in the JS). `updateEconomics()` had the same `totals.total_cost_usd` key, so the header indicator never left `$0.0000` either |

`docs/events.schema.json` only enumerates event *names*; `data` is an untyped `object`. So the tooling that exists
(`harness events schema` drift check, `test_events_subscription_contract`) can catch a missing *topic* but not a
renamed *field* — which is exactly the class of bug behind F-11/F-12 in PR #4 and R1 here. Until payloads are
typed once (SDK dataclass → generated schema → UI contract test), every UI feature will keep guessing keys.

Live proof after the fix in this PR (mock provider, one turn, click *Economics*): the tab shows
`Prompt (Input) Tokens: 5 · Completion (Output) Tokens: 12 · Total Tokens: 17`, matching
`POST /commands/economics → {"totals": {"input_tokens": 5, "output_tokens": 12, …}}`. Note the remaining
inconsistency: the ledger stores `cost_usd: 0.0` for the mock provider while the event says `unknown_pricing: true`.

### 4.2 Two styling systems, neither complete

`python additions/tools/ui_audit.py`:

* **128 classes are used in markup but defined nowhere**: `cards.js` 72 (`border-2 border-amber-500/70
  bg-amber-950/30 hover:bg-amber-500 truncate space-y-1 py-1.5 …`), `app.js` 35, `settings.js` 17,
  `index.html` 4 (`economics-view onboarding-banner session-list theme-dark`). They are Tailwind utilities,
  but the page loads no Tailwind (CSP `script-src 'self'` forbids the CDN anyway); `styles.css` l.1725–1800
  hand-copies ~60 of them, so cards render *partially* styled — the approval card's "Approve" button
  (`bg-amber-600`, `cards.js:285–300`) has no background.
* **63 classes in `styles.css` are used by nothing** (`editor-*`, `media-*`, `settings-page/-sidebar/-tabs-scroll`,
  `mode-segment*`, `stability-*`, `diff-add/-del/-hunk`, `user-bubble`, …) — the CSS of the deleted pages is still there.
* **36 inline `style="…"` attributes** (19 in `index.html`, 17 in `app.js` templates such as the economics card),
  i.e. a third place where styling lives.

### 4.3 Facade remnants of the "studio" layout

| Element | State | Evidence |
| :-- | :-- | :-- |
| Artifact Studio pane (`#studio-content-area`) | never populated; artifact cards render in the timeline (`cards.js`), the pane only gets `hidden` toggled | `app.js:451–470`; placeholder text `index.html:220` |
| Terminal drawer (`#terminal-drawer`, `display:none`) | `tool.output.delta` appends into `#terminal-body` (`app.js:135–137`, `731–737`) but nothing ever shows the drawer | `index.html:279` |
| `Autonomous` pill, `main` branch, `● Ready`, `Cache Tier: Invariant`, `Autonomous Heuristic` | static strings, no JS updates them (`agent-mode-badge`, `inspector-cache-status` are among the 12 ids no JS references) | `index.html:74, 234–240, 271` |
| "Unconfigured (Requires API Key)" `<optgroup>` | unreachable (§2 #6) | `app.js:916–946` |
| Onboarding banner | works, but keys off `kernel.provider` being empty; with the hermetic default settings it shows even when the only usable provider is `mock` | `app.js:67–75` |

### 4.4 Session lifecycle is decided in the browser

`init()` (`app.js:52–57`) creates a session whenever `/health` reports no active session. `SessionManager.active_session`
starts as `None` on every daemon start and nothing resumes the most recent session, so:

```
daemon start #1 → page load: no active session → created one   (sessions on disk: 1)
daemon start #2 → page load: no active session → created one   (sessions on disk: 2)
daemon start #3 → page load: no active session → created one   (sessions on disk: 3)
```

(three restarts, zero turns, three empty `session_xxxxxxxxxxxx` rows in the Sessions tab, which shows raw ids
only). The scenario goldens moved for the same reason (`session.started` now precedes every web scenario).
Also `init()` connects SSE *after* `reloadTranscript()` (`app.js:61–62`), so events emitted in that window during
a refresh mid-turn are lost (no `Last-Event-ID` on a fresh `EventSource`).

### 4.5 The test estate is split four ways

| Place | What | Problem |
| :-- | :-- | :-- |
| `tests/` (testpaths) | 360 tests | `tests/e2e/test_scenarios_s1_s6.py::test_scenario_s1_safe_daemon` builds **its own toy aiohttp app** with its own middlewares (l.57–136) and asserts against *that* — it proves nothing about `HarnessWebServer` (which uses `Authorization: Bearer`/cookie, not `X-Harness-Token`). `test_ui_smoke.py` hard-codes ports 8791/8792 under a comment saying "ephemeral". |
| `tests/frontends/ui_smoke.mjs` | jsdom bootstrap #1 (fork of the review tool) | duplicate |
| `review/tools/ui_smoke.mjs` | jsdom bootstrap #2 | the original, now redundant |
| `additions/scenarios/_lib/node/headless_timeline.mjs` | jsdom bootstrap #3 — the complete one (frame-by-frame SSE log, DOM snapshots, reload lane) | the only one with real coverage, **not in CI**, not in `testpaths` |
| root `package.json` vs `additions/scenarios/_lib/node/package.json` | two npm roots for one dependency (`jsdom`) | duplicate |

### 4.6 Version and document drift

* CHANGELOG `[0.2.1]` rewritten after the tag (§2 #13); `[Unreleased]` holds only PR #4's entries.
* `GET /health` returns `"version": "2.0.0"` (`server.py:321`) while `pyproject`/`harness.__version__` say `0.2.1`.
* README §4 says "56+ test files, 340+ tests"; the commit message says Playwright; `turn.delta`.
* Three documentation roots: `docs/` (21 research syntheses of *other* systems + catalog + schema), `review/`
  (this series), `additions/` (runnable deliverables). There is still no document that describes *this* system
  end to end (kernel → commands → web; event catalog with payloads; session lifecycle).

### 4.7 Kernel loose ends the UI work exposed

* `CostLedger.totals(session_id=…)` ignores its argument (`ledger.py:130–150`).
* `cost.updated.session_usd` = turn cost (`runner.py:490`).
* `models.list` fans out to every provider on each page load; with no keys that is five `WARNING` lines per load.
* "Configured" is a string heuristic on secret names in `Commands` (`commands.py:277–296`) instead of a question
  the provider answers (`Provider.is_configured()` / `is_reachable()`); `openai_compat` is special-cased by name.

---

## 5. Defects found in `158c836` and their status

| ID | Defect | Severity | Status |
| :-- | :-- | :-- | :-- |
| **R1** | Economics tab + header indicator read non-existent keys → always `$0.0000`, `0` tokens, literal `$5.00` cap | functional regression (new feature dead on arrival) | **Fixed in this PR** (`app.js renderEconomicsTab/updateEconomics` read `spend_cap_usd`, `totals.cost_usd`, `totals.*_tokens`; no hard-coded cap). Guarded by `tests/frontends/test_static_contract.py::test_economics_payload_keys_match_app_js`, which parses the two functions and asserts every `data.X` / `totals.X` they read is a key the real `Commands.economics()` payload / `Usage.to_dict()` has. Verified the test fails on the committed version. |
| **R2** | New empty session per daemon restart + page load | UX / data hygiene | Open → plan P2 |
| **R3** | F-5 claimed fixed; Ollama phantom models still "configured"; unconfigured optgroup unreachable | correctness of the model picker | Open → plan P4 |
| **R4** | 7 scenario goldens drifted (benign) and nothing in CI noticed | process | Goldens **re-baselined in this PR** (`HARNESS_REPLAY=update`, then a strict run: 20 passed); CI job → plan P5 |
| **R5** | CHANGELOG rewrites the released `[0.2.1]` section; `/health.version` = `2.0.0` | hygiene | Open → plan P6 |
| R6 | `init()` connects SSE after transcript reload | edge case | Open → plan P2 (two-line reorder + `Last-Event-ID`) |
| R7 | `test_ui_smoke.py` fixed ports; `npm install` instead of `npm ci`; `heartbeat` listener dead | minor | Open → plan P5 |

---

## 6. Ordered consolidation plan

Order matters: contracts first (so everything after is checked by a test instead of by eye), then the two
behaviours users notice (sessions, styling/facade), then the provider truth, then test/doc consolidation.
Each step has an acceptance command that must pass before the next starts.

### P1 — Typed payloads: one definition per event/command result (≈1–2 days)

* Add `@dataclass` payload types to `harness/sdk/types.py` for every event in the catalog (`CostUpdated`,
  `ToolCallStarted`, `ToolDecision`, `ToolResult`, `TurnCompleted`, …) and for command results that the UI
  consumes (`EconomicsSnapshot = {turn_usd, session_usd, lifetime_usd, cap_usd, unknown_pricing, usage: Usage}`).
  Emit them (`Event(type=…, data=CostUpdated(...).to_dict())`), and make `economics` and `cost.updated` share
  `EconomicsSnapshot` so there is exactly one spend shape.
* Extend `harness events schema` to emit `data` sub-schemas per type (`oneOf` on `type`), regenerate
  `docs/events.schema.json`; CI already diffs it.
* Generalise the new contract test: for every `this.events.on('<type>', (data) => …)` handler in `app.js`, the
  `data.<key>` references must be properties of that type's schema. Same for `api.js` command helpers.
* Fix the two kernel bugs that the typed shape makes visible: `CostLedger.totals(session_id)` filters by session;
  `session_usd` is the session sum, `turn_usd` the turn sum.

Acceptance: `pytest tests/frontends/test_static_contract.py tests/kernel -q` · `harness events schema | diff -u docs/events.schema.json -` · the Economics tab shows session ≠ lifetime after two sessions.

### P2 — Session lifecycle in the kernel, not the browser (≈0.5 day)

* `SessionManager.resume_or_create()`: on daemon start (or first `/health`), resume the most recent non-busy
  session; create lazily on the first `submit_turn` when none exists. Remove `createNewSession()` from `init()`.
* `sessions.list` rows carry `created_at`, `turns`, `title` (first user message, 60 chars); the Sessions tab shows them.
* `init()`: connect SSE first, then `reloadTranscript()`; pass `Last-Event-ID` from the last rendered event.
* Re-baseline the scenario goldens once (`HARNESS_REPLAY=update`, then strict).

Acceptance: the §4.4 probe yields **1** session after three restarts · `pytest additions/scenarios -q` 20 passed.

### P3 — One styling system and no facade (≈1 day)

Decide once: (a) vendor a **compiled** Tailwind (`npx tailwindcss -i static/tw.in.css -o static/vendor/tw.css
--content "static/*.{html,js}"`, commit the output; CSP-safe, no CDN) and delete the hand-copied utility block
in `styles.css`; or (b) rewrite the card/settings templates to semantic classes and delete the utilities.
(a) is the smaller diff because `cards.js`/`settings.js` are already written in utilities.
Then remove or wire each facade item in §4.3: delete the Artifact pane or render `artifact` cards into it; show
the terminal drawer when `tool.output.delta` arrives or delete it; replace the static pills with real values
(`health.version`, `kernel.permission_mode`, SSE state) or remove them; drop the unreachable optgroup until P4.
Move the 17 inline-style templates in `app.js` to classes.

Acceptance: `python additions/tools/ui_audit.py --strict` exits 0 (0 missing ids, 0 undefined classes) ·
`grep -c 'style="' harness/frontends/web/static/app.js` = 0 · `pytest tests/frontends -q`.

### P4 — Provider truth lives in the provider (≈0.5 day)

* SDK contract: `Provider.is_configured() -> bool` (keys present) and `async Provider.is_reachable() -> bool`
  (Ollama: `GET /api/tags` with a 300 ms timeout). Delete `_is_provider_configured()` and its name heuristics.
* `models.list` returns `{…, configured, reachable}`; caches per provider for 30 s; logs at `DEBUG`, not `WARNING`.
* `populateModels()` renders three groups: ready / needs key / unreachable (disabled).

Acceptance: hermetic `POST /commands/models.list` with Ollama down returns no Ollama entry marked ready ·
no `WARNING` lines on page load · `tests/plugins/test_ollama.py` covers `is_reachable()`.

### P5 — One test estate (≈0.5 day)

* `git mv additions/scenarios tests/scenarios`; add to `testpaths`; one jsdom bootstrap
  (`tests/scenarios/_lib/node/headless_timeline.mjs`) and delete `tests/frontends/ui_smoke.mjs` and
  `review/tools/ui_smoke.mjs`; one root `package.json` (`jsdom`), `npm ci` in CI.
* CI: a `scenarios` job (`SCENARIO_DOM_WAIT_MS=6000 pytest tests/scenarios -q`, ≈150 s).
* Delete `test_scenario_s1_safe_daemon` (its real counterparts are `test_web_server.py` /
  `test_settings_security.py`) or point it at `HarnessWebServer`; ephemeral ports in `test_ui_smoke.py`.

Acceptance: `pytest -q` runs kernel + frontends + scenarios in one invocation · `find . -name package.json -not -path '*/node_modules/*' | wc -l` = 1 · `ls **/ui_smoke.mjs` finds nothing.

### P6 — Version and document hygiene (≈0.5 day)

* CHANGELOG: move the `158c836` bullets to `[Unreleased]`; remove the `/api/sessions/{id}/turn` line; keep
  released sections immutable.
* `/health.version` from `harness.__version__`; README test counts from the suite or dropped.
* Write `docs/ARCHITECTURE.md` (≈200 lines) that describes *this* system: process model, kernel modules,
  command catalogue, event catalogue with payloads (generated from P1), session lifecycle, web auth, where
  tests live. Move the 21 research syntheses to `docs/research/`. Point README at `review/` and `additions/`
  as history.

Acceptance: `git diff --stat v0.2.1 -- CHANGELOG.md` touches only `[Unreleased]` and newer sections ·
`curl /health | jq .version` = `0.2.x` · every path in README resolves.

---

## 7. Definition of "not all over the place" — the checklist to run after P1–P6

```bash
export HOME=$(mktemp -d); export HARNESS_CONFIG_DIR=$HOME/.harness
ruff check .
pytest -q                                   # kernel + frontends + scenarios, one suite
harness plugin test --all
harness events schema | diff -u docs/events.schema.json -
python additions/tools/ui_audit.py --strict  # 0 missing ids, 0 undefined classes
test "$(find . -name package.json -not -path '*/node_modules/*' | wc -l)" = 1
test "$(grep -c 'style="' harness/frontends/web/static/app.js)" = 0
```

Plus two manual probes: three daemon restarts leave one session; one mock turn makes the Economics tab and the
header indicator agree with `POST /commands/economics`.

---

## Appendix A — environment gotcha that bit this review

`export HOME=$(mktemp -d) HARNESS_CONFIG_DIR=$HOME/.harness` in **one** statement expands `$HOME` *before* the
new value is assigned, so `HARNESS_CONFIG_DIR` points at the real `/home/<user>/.harness`. The CI workflow uses
two `export` lines (correct); scripts and docs should do the same. Symptom: a `settings.json` written under the
temp HOME is ignored and the daemon reports *"No model provider configured"*.

## Appendix B — files touched by this review's PR

| Path | Change |
| :-- | :-- |
| `harness/frontends/web/static/app.js` | R1: `renderEconomicsTab()` / `updateEconomics()` read the kernel's actual keys; no literal `$5.00` |
| `tests/frontends/test_static_contract.py` | `test_economics_payload_keys_match_app_js` (fails on the committed code, passes on the fix) |
| `additions/scenarios/*/golden/events.json` (7) | re-baselined for the leading `session.started` |
| `additions/tools/ui_audit.py` | static UI coherence audit (ids, undefined/unused classes, inline styles, scripts); `--strict` is the P3 acceptance gate |
| `review/CODE_REVIEW_158c836.md`, `review/README.md`, `additions/README.md` | this document and index entries |
