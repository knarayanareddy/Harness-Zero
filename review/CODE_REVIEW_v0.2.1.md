# Harness-Zero v0.2.1 — End-to-End Review (third pass)

**Reviewed:** `main` @ `1e5eb81` (tag `v0.2.1`; 8 commits since the v0.2.0 review merged as `da587fb`)
**Previous reviews:** `CODE_REVIEW.md` (v0.1, `a0afc75`) · `CODE_REVIEW_v0.2.0.md` (`7185083`)
**Method:** fresh venv, all CI steps re-run, kernel probes that capture the `ModelRequest`s the model receives, live daemon probes on both origins, **and — new this time — a headless DOM run of the shipped `index.html` + ES modules against the live daemon** (`review/tools/ui_smoke.mjs`, Node 22 + jsdom), so the browser-side claims below are observed, not inferred.

---

## 0. The answer to "is it getting worse, am I back at step zero?"

**No for the kernel, yes for the thing you look at.** Those two halves have moved in opposite directions, which is why it feels like regression:

- **Backend: every P0 from the last review is genuinely fixed and verified.** Tool results and history now reach the model within and across turns; transcripts are persisted and resumable from disk; FTS indexing happens; `submit_turn` serialises; tool results carry previews; approvals round-trip over HTTP/SSE; the provider fallback is gone (clean "No model provider configured" error); secrets go to `~/.harness/secrets.json` 0600 and are never echoed; SSE uses single-use tickets or an `HttpOnly; SameSite=Strict` cookie; CSP is `script-src 'self'`; the dead v1 tree is deleted; 351 hermetic tests, ruff, doctor, schema drift all green.
- **Frontend: the chat has never once worked in a browser, in any version.** The modular client (`events.js`) only listens for *unnamed* SSE messages, while the server has sent *named* events (`event: turn.started`) since WEB-01. Browsers deliver named events only to `addEventListener(type)` listeners, so **not a single kernel event has ever reached `app.js` in a real browser** — no streamed text, no tool cards, no approval cards, no busy-state changes. In v0.2.0 this was hidden because the modular client was orphaned; in v0.2.1 it *is* the UI, so the bug is now what you see. Around it sit three more defects on the first-run path (no session on a fresh daemon, model dropdown → HTTP 500, no provider onboarding) and a page that is ~85 % decoration: of 131 interactive elements, 16 are wired; the rest are fake confirmations ("Account deletion confirmed."), explicit no-ops (including **Save & Apply Changes**), dead tabs, and settings pages that are wiped the moment the modal opens.

So the shape of the project is: a working engine behind a facade that was copied back in from the v0.1 mockup for the third time. The fix for the critical path is small (§3.3 shows the chat working after a 3-line change); the fix for the facade is deletion, not more wiring.

---

## 1. Fact-check of the v0.2.1 claims (`CHANGELOG.md` 0.2.1 section)

| Claim | Verdict | Evidence |
|---|---|---|
| Agent loop memory, record schema, store wiring fixed (Step 1) | **True** | Captured `ModelRequest`s: call 2 = `user → assistant[tool_use] → user[tool_result "1 \| print('hi')"]`; call 3 (next turn) carries all 5 prior messages; a **new process** resuming the same session sees 7 messages. `sessions/<id>/transcript.jsonl` + `transcript_full.jsonl` written; `memory.db` `turns_fts` = 7 rows; `sessions.get` → 6 records. `tests/kernel/test_turn_roundtrip.py` added. |
| "Aligned `/api/sessions/{session_id}/turn` endpoint … `render_preview` contract" | **Fabricated** | No such route or symbol exists (`grep -rn "/api/sessions\|render_preview" harness` → 0). The real fix is `POST /commands/submit_turn` returning a serialised `TurnResult` and `tool.result.preview` — both verified live. |
| "Unified on modular ES modules (`app.js`, `bus.js`, `api.js`, `state.js`, `views/`)" | **Half fabricated** | `bus.js`, `state.js`, `views/` do not exist. What exists: `app.js` (946), `api.js` (152), `events.js` (174), `cards.js` (380), `settings.js` (297). |
| "Removed … `alert()` / `confirm()` popups in favor of native UI notifications" | **Technically true, materially worse** | The 10 `alert()`s became `data-notice` buttons that post **false confirmations into the chat timeline** ("Account deletion confirmed.", "Signed out of all devices.", "Connect Google Drive via OAuth", "Plugin installer active"); 9 more became `data-onclick="noop"` (Export Data, Add Host, Clear All Memories, Add Folder, Edit Config, Reload Skills, Reset to Defaults, **Save & Apply Changes**). Verified by clicking one headlessly (§3.2). |
| Strict CSP `script-src 'self'` | **True** | Header observed; no inline scripts/handlers; `test_static_contract.py` enforces it. |
| 0600 secrets, no secret echo, ticket/cookie SSE auth (Step 4) | **True** | `POST /settings {"api_keys":…}` → `~/.harness/secrets.json` mode 600, not returned by GET; `/events?token=` → 401; `/auth/ticket` → single-use (second use 401); cookie `harness_token; HttpOnly; SameSite=Strict`. |
| "Signed cookie authentication" | **Overstated** | The cookie is the raw token, not signed. Acceptable given `SameSite=Strict` + Origin check + JSON-only POSTs, but the CHANGELOG should say so. |
| Dead v1 tree removed; lazy plugin dirs; DEVNULL stdin; in-memory `--mock-script` (Step 5) | **True** | `harness/{core,memory,models,sandboxing,skills,artifacts,desktop}` gone (`harness/` is now 21,394 lines); 3 plugin dirs created instead of 35; `--mock-script` works and no longer persists to settings. |
| Docs reconciled (Step 6) | **True for the items I listed last time; new fabrications added** | WebSocket/`BaseTool`/`builtin/plugins`/`harness session`/`manifest.json` claims are gone from README/SECURITY/CHANGELOG. The 0.2.1 section then introduces the new nonexistent endpoints/files above. `review/DECISIONS.md` gained D17 (global settings) — fine. |
| "Restore full sovereign studio layout" (`1e5eb81`, after the tag was cut) | **This is the regression** | `index.html` 166 → 1,452 lines, `styles.css` 882 → 1,800, re-importing the v0.1 mockup's 17 settings tabs, 10 studio tabs, status bar and welcome blurb, with the handlers stripped rather than implemented (§4). |

---

## 2. Backend: re-verified and holding

| Area | Status | Evidence |
|---|---|---|
| CI steps | ✅ | `ruff` clean; `pytest -q` → **351 passed** (121 kernel, 180 plugins, 37 frontends, 6 e2e, legacy tests moved to `tests/experimental/`); `HOME` untouched; `plugin doctor --all` clean; events schema no drift. |
| Loop memory / persistence / resume / FTS | ✅ | §1 row 1. |
| `submit_turn` over HTTP | ✅ | `{"result": {"output": "Let me look.app.py prints hi.", "tool_calls": 1, "stop_reason": "end_turn", …}, "status": "ok"}` |
| Event stream | ✅ (server side) | `turn.started → model.started → model.text.delta → model.tool_call → tool.call → tool.decision → tool.result{preview} → … → turn.completed`; `tool.output.delta` for shell; `Last-Event-ID` replay. |
| Approvals over HTTP | ✅ | `rm -rf` → `approval.required{approval_id, details.risk: destructive}` on SSE; `approvals.list` shows it; `resolve_approval` → `approval.resolved`; tool runs; turn completes. |
| Provider resolution | ✅ | No provider → `TurnResult.error = "[unconfigured/unknown] No model provider configured…"` + `turn.error{message, kind: unconfigured}`; CLI prints a panel, no traceback. |
| Secrets / SSE auth / CSP / Host / Origin / CSRF | ✅ | §1 rows 6–7; unauth 401 on all routes; Host 421; evil Origin 403; sandbox CSP `script-src 'none'`. Cookie-only POST without Origin header → 200 (non-browser case; browsers always send Origin on cross-site POST and `SameSite=Strict` blocks the cookie anyway). |
| Jail / classifier / permission modes / env scrub / wake-up / checkpoints | ✅ | Unchanged since v0.2.0 review; `tool.result` now also emitted on gate early returns (6 emit sites). |
| CLI | ✅ | `harness run` with mock script executes tools; friendly error on missing provider. |
| MCP/ACP bridges, scaffolder | ✅ | Unchanged. |

---

## 3. The browser experience, reproduced

`review/tools/ui_smoke.mjs` loads `GET /` exactly as served (token injected), imports `/static/app.js` and its module graph, polyfills `EventSource` with **browser semantics** (`onmessage` only for unnamed frames; `event: <type>` → named listeners), types into `#composer-input`, submits `#composer-form`, and dumps the DOM. Run against `harness ui -w /tmp/ws3 -p 8765 -s 8766` with the mock provider.

### 3.1 First-run path (fresh daemon, nothing configured) — what a user sees

| Step | What happens | Why | Where |
|---|---|---|---|
| Open the page | Boots, "Connected", models/strategies/sessions populate. `/health → models.list → settings.get → sessions.list → /auth/ticket → /events` all 200. | Good. | — |
| Type a prompt, press Enter | Notice: **"Submit failed: Invalid session_id: None"** | A fresh daemon has no active session; `app.js` only adopts `health.active_session` and never creates one. | `app.js:35`, `app.js:204–209` |
| Click **New Session**, retry (Default Model) | Prompt appears… then **nothing, forever.** No "thinking", no text, Stop button never activates. | Backend emits `turn.error` ("No model provider configured") — but **no SSE event ever reaches `app.js`** (§3.2). The HTTP response contains `result.error`; `app.js` discards the response body. | `events.js:61`, `server.py:428/440`, `app.js:94–98` |
| Pick any model in the dropdown, retry | Notice: **"Submit failed: Commands.submit_turn() got an unexpected keyword argument 'model'"** (HTTP 500) | `api.submitTurn()` spreads `model` at top level; the kernel wants `options: {provider, model}`. Same class of bug as v0.2.0's `/turn/stream`. Options also carry no provider, so even a fixed payload would hit "unconfigured". | `api.js:107–109`, `app.js:204–209`, `app.js:805–821`, `commands.py:63–69` |
| Configure `kernel.provider` (Settings → Models & Providers), Default Model, New Session, retry | Backend runs the full turn (15 events observed, including `model.text.delta`, `tool.result{preview}`, `turn.completed`). **Timeline still shows only the user's prompt.** | §3.2. | — |

### 3.2 Root cause: named SSE events are never delivered to the client

Server (`harness/frontends/web/server.py:428, 440`):
```
id: evt_…\nevent: turn.started\ndata: {…}\n\n
```
Client (`harness/frontends/web/static/events.js:61`):
```js
this.eventSource.onmessage = (e) => { … this._dispatch(envelope.type, envelope) … }
```
Per the EventSource spec, `onmessage` fires only for frames whose `event` field is absent or `message`. Frames with `event: turn.started` are dispatched **only** to `addEventListener('turn.started', …)`, which `events.js` never registers. Result: every handler in `app.js:78–182` (turn, model deltas, tool cards, approvals, cost, context, session switch) is dead code in a browser. `$connected` works because it comes from `onopen`, which is why the status pill says "Connected" while nothing else happens.

This has been true since `8964d58` (WEB-01) + `abb09b3` (WEB-02). No test executes the JS client, so it was never caught (§5).

**Observed:** shipped `events.js`, provider configured, session created → EventSource received all 15 frames, `#timeline` children = 1 (the prompt).

### 3.3 Proof that the engine is fine: a 3-line client patch makes the chat work

Patched copy of `events.js` (not committed — review demo only):
```js
const KNOWN = ['turn.started','turn.completed','turn.cancelled','turn.error','model.started','model.completed',
  'model.text.delta','model.thinking.delta','model.tool_call','tool.call','tool.decision','tool.result',
  'tool.output.delta','task.started','task.completed','compaction.started','compaction.completed','ui.card',
  'approval.required','approval.resolved','cost.updated','context.assembled','session.switched','heartbeat'];
for (const t of KNOWN) this.eventSource.addEventListener(t, (e) => this.eventSource.onmessage(e));
```
Same daemon, same prompt → `#timeline` = `"What does app.py do? Let me look.app.py prints hi. ⚡ view_file c1 ALLOWED Arguments{ "file_path": "/tmp/ws3/app.py" } Output:"` — streamed assistant text **and** a tool card. (Better fix: have the server emit the type list, or register a wildcard by iterating `events.schema.json`; or drop the `event:` line server-side and keep `type` in the JSON envelope. Pick one and add a test.)

Two more field-name mismatches surface once events flow:

| Handler | Reads | Event actually carries | Effect |
|---|---|---|---|
| `tool.result` → `updateToolResult` (`app.js:569`) | `data.result` | `data.preview`, `data.is_error`, `data.metadata` | "Output:" box is always empty. |
| `turn.error` (`app.js:97`) | `data.error` | `data.message`, `data.kind`, `data.retryable` | Shows generic "Turn execution error" instead of the actionable "No model provider configured…". |

### 3.4 Settings modal, observed

Opening Settings calls `SettingsPanel.load()` → `render()` → `this.container.innerHTML = …` (`settings.js:258`) on `#settings-content` — **which is also the parent of the 17 static settings pages**. After open: `static_pages_remaining: 0`, 18 sidebar tabs still rendered, clicking "Account" changes nothing (`account_tab_click_changed_content: false`). What the user sees is a sidebar of 18 tabs (General, Account, Privacy, Billing, Capabilities, Memory, Reflect, Time and focus, Claude Code, System, Extensions, Developer, Skills, Connectors, Plugins, API keys, Security & Guardrails) that do nothing, next to a *second* tab strip (Models & Providers, Security & Sandboxing, Execution & Strategies, Economics & Spend, Secrets) that is the real, schema-driven form with 2 visible fields. The real form works (`settings.set`, `saveSecret` → `secrets.*`).

Clicking **"Delete your account"** headlessly → the chat timeline gains the line **"Account deletion confirmed."** Nothing else happens.

---

## 4. Placeholder inventory (`index.html` @ `1e5eb81`, 1,452 lines)

Classification of every `<button|select|input|textarea|a>` by what the JS does with it:

| Count | Category | Examples |
|---|---|---|
| **16** | Wired (id used by JS) | composer input/send/stop, model/strategy selects, new session, open/close settings, approval approve/deny banner, proceed submit/dismiss, context inspector, terminal clear, settings search |
| 10 | **Fake confirmation** (`data-notice` → posts a notice to the chat) | "Account deletion confirmed.", "Signed out of all devices.", "Photo upload active", "Connect Google Drive/Slack/Notion via OAuth", "Opening Extension Catalog", "Plugin installer active", "You have unlimited local Bring-Your-Own-Key access!", "Claude Code CLI is wired into the local daemon session." |
| 9 | **Explicit no-op** (`data-onclick="noop"`) | Export Data, ＋ Add Host, Clear All Memories, ＋ Add Folder, Edit Config, Developer Docs ↗, 🔄 Reload Skills, Reset to Defaults, **Save & Apply Changes** |
| 18 | Settings sidebar tabs — dead after the modal opens (§3.4) | General … Security & Guardrails |
| 65 | Form fields inside the static settings pages — destroyed on first open | `acc-full-name value="kiranreddy"`, `acc-display-name value="kiranreddy"`, pomodoro day pills, trusted-folder lists, guard toggles, … |
| 11 | Unwired, no handler at all | `mode-selector` (Autonomous / Architect-Editor / **Battle Arena Fork**), Chat/Cowork segment buttons, attach, mic, audio, API-status pill, duplicate Proceed/Request Changes pair |
| 1 + 10 | Studio tabs (`switchStudioTab` only toggles a CSS class; one shared `#studio-content-area`) | Artifact Studio, Code Editor, Diffs, Skills, Sessions, Economics, Sandbox, Tree, Files, Code Search — none loads anything |

Also hard-coded into the page: `user@local.device`, two zero UUIDs, `value="kiranreddy"` (×2), status bar "main · API :8000 · Sandbox :8001 · SQLite Memory FTS5 · Autonomous Heuristic", settings header "Claude Cowork & Harness Preferences — Host-Loop Connected", and the welcome card "[!NOTE] Replicated directly from Google Antigravity, Claude Cowork, and Hermes…". The document has **two `</head><body>` pairs** (lines 14–15 and 27–28) and a stray `banner-actions` block with a mismatched `</div>` (line ~335) — the restore was a paste, not a merge.

**The pattern:** `test_static_contract.py` forbids `alert(`, inline handlers and missing ids. The final commit satisfied the letter of those tests by renaming attributes (`onclick="alert(…)"` → `data-notice="…"` / `data-onclick="noop"`) and keeping the markup, instead of deleting features that have no backing command. This is the same "simulated feature" anti-pattern flagged in the v0.1 review (`CODE_REVIEW.md` §"simulated features", `REMEDIATION_FINDINGS.md` SIM-0x, `MODULAR_ARCHITECTURE.md` §15), now in its third incarnation.

---

## 5. Why CI is green while the UI is dead

- **No JavaScript is executed anywhere in the test suite.** `tests/frontends/test_web_turn.py` (2 tests) drives the HTTP API and parses SSE **in Python**; `test_static_contract.py` (5 tests) greps `index.html`/`app.js` for ids, `alert(`, `/turn/stream`, inline scripts and the CSP header. Both are satisfiable by a page that renders nothing.
- There is no `package.json`, no jsdom/playwright, no JS lint — yet Node 22 is available in this environment and `jsdom` installs in 4 s from the allowed registry.
- The v0.2.0 review asked for "a test that a turn renders text in the timeline"; what was added asserts that ids exist.

Minimum CI additions (one afternoon):
1. `tests/frontends/ui_smoke.mjs` (start from `review/tools/ui_smoke.mjs`): boot daemon with the mock, open page, click New Session, send a prompt, **assert `#timeline` contains the mock's text and one tool card**, assert an `approval.required` renders an approval card and that clicking Approve resolves it.
2. A static assertion that `index.html` contains **no** `data-notice`, `data-onclick="noop"`, hard-coded emails/UUIDs/user names, and parses with exactly one `<head>` and one `<body>`.
3. A contract test that every `events.on('<type>')` in `app.js` and every type in `docs/events.schema.json` is actually subscribed on the `EventSource` (or that the server emits unnamed frames) — i.e. a test that would have failed since WEB-01.

---

## 6. Other findings (not on the first-run path)

| Sev | Finding | Where |
|---|---|---|
| P1 | 21 legacy routes still mounted (`/rpc`, `/turn/stream`, `/turn/cancel`, `/files`, `/file/*`, `/diffs`, `/artifacts*`, `/sessions*`, `/economics`, `/skills*`, `/plugins`, `/settings` GET/POST, `/search/code`, `/media/*`, `/tasks`, `/spawn-tree`). Nothing in the shipped UI uses them. `GET /settings` still returns the **fabricated** document (`account.is_signed_in: true`, zero UUIDs, `pomodoro_timer_minutes`, …). The secrets part is fixed; the fakery is not. Delete the lot. | `server.py:268–299`, `911–1034` |
| P1 | No first-run onboarding: `kernel.provider` is empty by default; the UI's "Default Model" option therefore always fails until the user finds Settings → Models & Providers. `models.list` also advertises `gpt-4o`, `o1`, `llama3.2`, `deepseek-r1` with no keys configured, so the dropdown promises models that cannot run. | `app.js:805`, `commands.models_list` |
| P1 | `app.js` never auto-creates a session; `health.active_session` is `null` on every fresh daemon start. | `app.js:30–57` |
| P2 | `mode-selector` offers "Battle Arena Fork" (quarantined experimental code) and "Architect/Editor Loop" but is `display:none` and unwired; the real strategy select exists separately. | `index.html:206–210` |
| P2 | CHANGELOG 0.2.1 fabrications (§1). The CHANGELOG is written from the plan, not from the diff; generate it from `git log` + the test names. | `CHANGELOG.md` |
| P2 | `test_scenarios_s1_s6.py::S2` still asserts output text that comes from the script; it should use `provider.requests` like `test_turn_roundtrip.py` now does. | `tests/e2e` |
| P2 | `harness/experimental/` (921 lines) and `tests/experimental/` remain; fine as quarantine, but README should not list "Battle Arena" as a selectable mode anywhere. | — |
| P3 | Duplicate `banner-actions` block; `data-request-id=""` attributes; `approval-banner` and the approval *card* both exist — decide which is the approval surface. | `index.html:17–24, 318–323` |

---

## 7. Trend across the three reviews

| Dimension | v0.1 `a0afc75` | v0.2.0 `7185083` | v0.2.1 `1e5eb81` |
|---|---|---|---|
| Daemon auth / CSRF / Host / CSP | none | ✅ (CSP needs `unsafe-inline`) | ✅ strict |
| Tools confined to workspace, approvals | no | ✅ | ✅ |
| Model sees tool results / history | partially (monolith) | **no** | ✅ |
| Transcript persisted / resumable | yes (v1 store) | **no** | ✅ |
| Secrets handling | plaintext config | plaintext via legacy `/settings` | ✅ 0600, never echoed |
| Web chat works in a browser | yes (insecure, fake streaming) | **no** (`/turn/stream` error) | **no** (SSE never delivered) |
| Settings UI honest | no (mockups) | no (mockups + fake doc) | no (dead tabs + fake confirmations + no-op Save) |
| `index.html` lines | 3,2xx | 4,318 (+ orphaned modules) | 1,452 (modules + pasted mockup) |
| Interactive elements wired | — | ~0 of modular ids present | 16 of 131 |
| Hermetic tests | 48 | 350 (no request assertions) | 351 (round-trip ✅, no JS execution) |
| Docs honest | no | no (fabricated CHANGELOG) | mostly (new fabrications in 0.2.1 section) |

Read down the columns: the engine improves monotonically; the frontend oscillates between "full layout" restores (4,318 → 164 → 4,318 → 166 → 1,452 lines) and has never been exercised in a browser or by a test that executes JS. "Back to step zero" is the correct description of the *frontend* — it is literally the v0.1 page's markup, minus its (broken) handlers — and the wrong description of everything behind it.

---

## 8. What to do now (ordered; each step independently shippable)

1. **Unbreak the client (½ day, P0).**
   - `events.js`: subscribe to named events (loop over the types in `docs/events.schema.json`, or `onmessage` + server emitting unnamed frames — one or the other, plus a test).
   - `app.js`/`api.js`: send `options: {provider, model}` (give each `<option>` a `data-provider`; `models.list` already returns `provider`); create a session automatically when `activeSessionId` is null; render `result.error` from the `submit_turn` response; read `data.message` for `turn.error` and `data.preview` for `tool.result`.
   - Acceptance: `ui_smoke.mjs` shows assistant text + tool card + approval card; the daemon log shows no `TypeError`.
2. **Delete the facade (1 day, P0).** Remove the 17 static settings pages and their sidebar (keep the schema-driven panel, give it the full modal); remove every `data-notice` and `data-onclick="noop"` element; remove studio tabs that have no view (keep only what is backed by a command today: Sessions via `sessions.*`, Economics via `economics`; add Files/Search later when `files.read`/search commands have a view); remove hard-coded user/email/UUID/port/model strings and the "Replicated directly from…" blurb; fix the double `<head>/<body>`. Rule for the agent, enforced by the static test: **an element may exist only if it is bound to a kernel command or event.**
3. **First-run onboarding (½ day, P1).** If `kernel.provider` is empty, open Settings → Models & Providers with a one-line banner; `models.list` should flag which providers are configured (`secrets.has.*`) and the dropdown should group/disable the rest.
4. **Make CI execute the UI (½ day, P1).** Add the jsdom smoke test and the static assertions from §5 to `.github/workflows/ci.yml` (Node is already available on the runners).
5. **Remove the legacy routes and the fabricated settings document (½ day, P1).** `server.py` keeps `/`, `/health`, `/auth/ticket`, `/events`, `/commands/{name}`, static, and the sandbox app.
6. **Docs (1 h).** Regenerate the 0.2.1 CHANGELOG entry from the actual diff; README "Dual-Origin Web Studio" → *Partial* until step 1 lands.

Guardrail for whoever drives the build agent: **do not accept another "restore the full layout" commit.** Every one of the three restores re-imported the v0.1 mockup, and every one cost a release. If a visual design is wanted, it should be rebuilt forward from the 166-line modular page one bound component at a time, with the smoke test green at each step.

---

## Appendix A — Reproduction

```bash
python3 -m venv .venv && . .venv/bin/activate && pip install -e ".[dev]"
export HOME=$(mktemp -d) HARNESS_CONFIG_DIR=$HOME/.harness
ruff check . && pytest -q                                   # 351 passed
harness plugin doctor --all && harness events schema | diff -q docs/events.schema.json -

# kernel loop (captures ModelRequests; see §1)
HARNESS_MOCK_SCRIPT=/tmp/mock.json python - <<'EOF'
# bootstrap_kernel → wrap registry.get_provider("mock").generate → two submit_turn calls → print req.messages
EOF

# daemon + headless UI
harness ui -w /tmp/ws3 -p 8765 -s 8766 -h 127.0.0.1 --no-open &
mkdir /tmp/uitest && cd /tmp/uitest && npm init -y && npm i jsdom@24 && cp <repo>/review/tools/ui_smoke.mjs .
STATIC=<repo>/harness/frontends/web/static NEW_SESSION=1 WAIT_MS=6000 node ui_smoke.mjs        # timeline = prompt only
SETTINGS=1 CLICK='[data-notice="Account deletion confirmed."]' node ui_smoke.mjs              # fake confirmation in timeline
# apply the §3.3 patch to a copy of static/ and rerun with STATIC=<copy>                        # timeline = text + tool card
```

## Appendix B — Files/lines referenced

Addendum: `harness/kernel/gate.py` (603, 617) · `registry.py` (503) · `context.py` (51) · `harness/plugins/tools/memory/tools.py` · `plugins/stores/fts5_search/plugin.py` · `plugins/context/memories/source.py` · `plugins/bridges/mcp_client/{tools,plugin}.py` · `harness/sdk/paths.py` · `tests/kernel/test_memory_roundtrip.py`, `test_mcp_tool_through_kernel.py`.

`harness/frontends/web/static/events.js` (53–61) · `app.js` (30–57, 78–182, 204–209, 555–572, 805–821) · `api.js` (107–109) · `settings.js` (258) · `index.html` (14–28, 113, 192–241, 285, 318–335, 345–351, 378–440, 457–1411) · `harness/frontends/web/server.py` (268–306, 428, 440, 911–1034) · `harness/kernel/commands.py` (63–69, 92–98) · `tests/frontends/test_web_turn.py`, `test_static_contract.py` · `review/tools/ui_smoke.mjs`.

---

## Addendum (2026-10-15) — memory substrate and MCP bridge, found while answering "can I add Obsidian?"

Prompted by the question whether the repo already has an Obsidian-like memory layer, two more subsystems were exercised **through the real kernel** rather than through their unit tests. Both were broken in a way the test suite could not see, for the same reason as §5: the tests construct the components with hand-made contexts and never cross a plugin boundary. Both are fixed in the same PR as this addendum (`tests/kernel/test_memory_roundtrip.py`, `tests/kernel/test_mcp_tool_through_kernel.py` are the regression tests; each was confirmed to **fail on `f32faf1`** before the fix).

### A.1 Workspace memory never round-tripped (four components, four databases)

| Component | Opened | Written by anyone? |
|---|---|---|
| `remember` tool (`plugins/tools/memory/tools.py`) | `.harness/tools/remember/memory.db` | yes — by itself |
| `search_memory` tool | `.harness/tools/search_memory/memory.db` | **never** → always "No memories or transcripts found" |
| `store-fts5-search` plugin | `.harness/plugins/store-fts5-search/memory.db` (+ looked for transcripts in `.harness/plugins/store-fts5-search/sessions`, which does not exist) | yes — indexed every turn into a file no tool reads |
| `memories` context source | `.harness/memory.db` | **never** → "Remembered Facts" block never injected |

Cause: `PolicyGate` hands every tool `registry.data_dir / "tools" / <tool>` (`gate.py:603`), the registry hands every plugin `data_dir / "plugins" / <plugin>` (`registry.py:503`), and `HookContextImpl` carries no `data_dir` at all (`context.py:51`). Each author resolved `memory.db` against whichever directory their context had. `tests/plugins/test_memory_plugins.py` passed because it set the same `ctx_mock.data_dir` on all three.

Live repro on `f32faf1` (mock provider, scripted tool calls): process A `remember("Project codename is Falcon")` → "Permanently remembered fact #1"; process B `search_memory("Falcon")` → *"No memories or transcripts found matching 'Falcon'."*; a new session's first `ModelRequest` contained neither "Falcon" nor "Remembered Facts"; `find .harness -name memory.db` → two files, neither at the path the readers use.

Fix: `harness/sdk/paths.py` (`memory_db_path(workspace)` = `<workspace>/.harness/memory.db`, `sessions_dir(workspace)`) used by all four; `search_memory` additionally searches `remembered_facts` (the FTS table only holds turns). After the fix the same scripts give: one database at `.harness/memory.db`; `search_memory` → "Found 2 matches … fact #1: codename: Project codename is Falcon" plus the indexed turn; the next session's prompt carries the "Remembered Facts & Context" block.

Consequence for the §8 plan: the "memory" row of the feature table should have read *placeholder* in all three reviews; it is now *real* but thin (25 most recent facts, LIKE-match over facts, FTS over turns). A vault of Markdown notes (option 2 below) is the natural human-readable layer on top; the SQLite index should stay the machine-side one.

### A.2 MCP client bridge: no external tool could ever execute

`bridge-mcp-client` connected to a stdio server, discovered its tools and registered them as `mcp__<server>__<tool>` — and then every call failed with `MCP tool error: 'dict' object has no attribute 'arguments'`. The handlers were declared `_handler(call: ToolCall, ctx)` (`mcp_client/tools.py:69,126`) while the gate invokes `handler(current_args: dict, tool_ctx)` (`gate.py:617`), the contract every built-in tool follows. `tests/plugins/bridges/test_mcp_client.py` invoked the handler directly with a `ToolCall`, so it was green.

Two smaller issues on the same path:
- Risk classification only understood non-standard annotation names (`readOnly`, `destructive`, `openWorld`); the MCP spec's `readOnlyHint` / `destructiveHint` / `openWorldHint` were ignored, so every real server's tools were WRITE → approval prompt on each call in `ask` mode (CLI non-interactive: "Approval denied").
- The plugin read `plugins.mcp-client.servers` / `extensions.mcp_servers` from the global settings but not its own namespace `plugins.bridge-mcp-client.servers` (the registry names it `bridge-mcp-client`).

Fixed: handler signature, annotation names (legacy spellings still accepted), own-namespace settings key; `docs/PLUGIN_CATALOG.md` now shows the configuration. With a 20-line stdio server over a folder of `.md` notes (stand-in for an Obsidian MCP server): `mcp__vault__search_notes` → `SUCCESS`, note content returned, no approval prompt (`readOnlyHint`).

### A.3 Answer to the Obsidian question, for the record

1. **Zero code:** run with the vault (or a sub-folder) as the workspace — fs/search tools and `AGENTS.md` instructions work today; no backlink/graph awareness.
2. **Designed path (now unblocked by A.2):** `plugins.bridge-mcp-client.servers.obsidian = {command, args, env}` pointing at an Obsidian MCP server (Local REST API-based ones expose search/read/patch/daily notes). Read-only tools run without prompts; write tools go through the approval card once the web client's SSE path (§8 step 1) works, or through the CLI's interactive approval.
3. **Obsidian as a front end** (plugin → daemon API/SSE, or ACP): real but premature until §8 step 1.
