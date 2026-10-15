# Harness-Zero v0.2.0 — End-to-End Review (post-remediation)

**Reviewed:** `main` @ `7185083` (tag `v0.2.0`, 69 commits since the v1 review at `a0afc75`)
**Reviewed against:** `review/MODULAR_ARCHITECTURE.md`, `review/REMEDIATION_PLAN.md`, `review/DECISIONS.md`
**Method:** fresh venv install, full CI steps re-run locally, live daemon probes (curl against both origins), scripted-model kernel probes that capture what the model actually receives, MCP/ACP stdio handshakes, scaffold round-trip, and a line-level read of the kernel, web server, UI and docs. Every finding below has a reproduction or a file:line.

---

## 0. Verdict in one paragraph

The build agent delivered a **genuinely good skeleton**: the SDK/kernel/plugin split is real, boundaries hold, 35 plugins load, the policy gate and jail do their job, the web daemon's auth/CSRF/Host/CSP hardening passes every probe, MCP/ACP bridges answer, and 350 hermetic tests are green. But the skeleton **is not yet an agent**. The single most important path — *model calls tool → tool result goes back to the model → model continues* — is broken at the kernel level: the records written by `KernelServices.record()` are in a shape `records_to_messages()` does not recognise, so every model call inside a turn sees only the original prompt, and nothing is persisted between turns because the transcript/search stores are registered but never wired into the runner. The tests do not catch this because no test asserts what the scripted provider received. On top of that, the "restored" 4,318-line UI is driven by a legacy inline script against legacy routes that were re-added untested (main chat endpoint errors on every request), while the new modular `app.js` is loaded into a page that does not contain any of the elements it binds to. The legacy `/settings` route re-introduces two things the plan explicitly removed: fabricated state and plaintext API keys on disk. The user's instinct — "it's all a bit disjointed" — is accurate: there are two frontends, two settings systems, two transcript stores, two record schemas and two test philosophies in the tree, and the docs describe a third system that does not exist.

Estimated distance to a coherent v0.2.x: **kernel fix ≈ 1–2 days, frontend decision + cleanup ≈ 2–4 days, security/doc regressions ≈ 1 day** — not "~2 hours of JS wiring".

---

## 1. Fact-check of the build agent's claims

| Claim | Verdict | Evidence |
|---|---|---|
| "22 plugins" | **False (undercount)** | `harness plugin list` → 35 loaded; `docs/PLUGIN_CATALOG.md` lists 35 and matches the manifests exactly. |
| "350+ hermetic tests, all green" | **True, with a caveat** | `pytest -q` → 350 passed in 64 s with `HOME=$(mktemp -d)`; temp HOME untouched afterwards. Caveat: the tests are green *because* they do not check the thing that is broken (§4.1, §7). |
| "Backend finished" | **False** | Tool results and history never reach the model (§4.1); `POST /commands/submit_turn` returns HTTP 500 after running the turn; `POST /turn/stream` fails on every request; `/sessions/switch` is a no-op; `GET /sessions/{id}` is a 500. |
| "Restored 4,318-line UI wired to modular kernel" | **Misleading** | The page is driven by a 1,798-line *inline legacy script* calling 21 legacy routes. The modular `app.js` client (`/commands` + `/events`) is also loaded but **none of the 19 DOM ids it binds exist** in the restored page (`composer-form`, `btn-send`, `model-select`, `session-list`, … all 0 matches). The only wiring to the "modular kernel" is 541 lines of compatibility routes added in `e486411`, which are what is broken. |
| "Settings tabs are still static mockups" | **True — and worse** | 10 `onclick="alert(…)"` placeholders remain (Delete account, Upgrade Plan, Connect Google Drive/Slack, Browse extensions, Upgrade to Max/Pro). `GET /settings` returns a hard-coded document with a fake signed-in account, fake capability flags and security toggles that nothing in the kernel reads (§4.3). |
| "~2 h of JS wiring remaining" | **False** | Even if the JS were done, the kernel loop, the turn endpoints, the approval flow, the settings/secrets path and the tests behind them all need work (§9). |

---

## 2. What was re-verified and holds (credit where due)

| Area | Status | How verified |
|---|---|---|
| Install / CI steps | ✅ | `pip install -e ".[dev]"`; `ruff check .` clean; `harness events schema` has no drift vs `docs/events.schema.json`; `harness plugin doctor --all` clean; `harness plugin test --all` 180 passed (11 s). |
| Import boundaries | ✅ | Kernel/frontends/SDK contain no `harness.plugins` imports; doctor's boundary check passes on all 35 + a fresh scaffold. |
| Daemon security (STOP-01/02/03) | ✅ | Unauth → 401 on `/rpc`, `/settings`, `/plugins`, `/events`, `/file/save`; `text/plain` POST → 415; evil `Origin` → 403; spoofed `Host` → 421 on **both** ports; UI headers include CSP, `nosniff`, `X-Frame-Options: DENY`; sandbox origin CSP is `default-src 'none'; script-src 'none'`; `daemon.json` is 0600; vendored JS only (no CDNs). |
| Workspace jail (STOP-07, PLG-06) | ✅ | Scripted model asked for `/tmp/secret_outside.txt`, `../outside_write.txt`, and `cwd=/tmp` → all three **DENY (workspace_jail)**; nothing written outside. |
| Risk classifier + permission modes | ✅ | `rm -rf …` → **ASK (risk_classifier)** even in `permission_mode=allow`; non-interactive session → denied without hanging; `allow` mode auto-allows plain EXEC. Any-DENY-wins / any-ASK-asks combination logic is correct (`gate.py:447–512`). |
| Executor env scrub (STOP-08) | ✅ | Subprocess saw no `*_API_KEY`/`FOO_API_KEY`; `HOME`/`PATH` present; `setsid` process groups. |
| Background task wake-up (S6) | ✅ | `WaitMsBeforeAsync` → `task.completed` event with tail, strategy re-entered without polling. |
| Checkpoints hook | ✅ | Shadow repo at `.harness/checkpoints/` committed on `write_to_file`. |
| Cost ledger / spend cap | ✅ | `economics.json` written; `/economics` and `economics` command return totals. |
| MCP server / ACP bridges | ✅ | `harness mcp`: `initialize` → server info, `tools/list` → 12 tools. `harness acp`: `initialize` answers with capabilities. |
| Scaffolder (SDK-01/02) | ✅ | `harness plugin new demo-tool --kind tool` → manifest, plugin, schema, README, conformance test; doctor + test pass on the result. |
| Launcher (WEB-08) | ✅ (unbuilt) | Swift launcher spawns `daemon --workspace … --port 8000 --sandbox-port 8001`, reads the token from `.harness/daemon.json`, refuses `$HOME` as workspace. Not compiled here. |
| Experimental quarantine (EXP-01) | ✅ | `harness/experimental/` + README; nothing in kernel/plugins imports it. |

---

## 3. Severity legend

- **P0** — breaks the core product promise or re-opens a closed security finding.
- **P1** — must fix before calling the UI/kernel "done".
- **P2** — cohesion / hygiene; schedule after P0/P1.

---

## 4. Findings

### 4.1 P0 — The agent loop has no memory: tool results and history never reach the model

**Repro (deterministic, offline):** register a scripted mock via `HARNESS_MOCK_SCRIPT` with a turn that calls `view_file app.py` then a second response, wrap the mock's `generate` to capture requests, run one turn. Result:

```
model call 1: messages=1  [user: "go"]
model call 2: messages=1  [user: "go"]          ← no assistant tool_use, no tool_result
model call 3: messages=1  [user: "go"]          ← after a background wake-up, still nothing
```

**Root cause — two record schemas:**

- Writer: `KernelServices.record()` (`harness/kernel/runner.py:531–571`) writes `{"role": "assistant"|"user", "content": …, "tool_calls": […], "tool_results": […]}`.
- Reader: `records_to_messages()` (`harness/kernel/context.py:89–166`) only recognises the **legacy v1 schema** — `source`/`type` with values `USER`, `USER_INPUT`, `MODEL`, `PLANNER_RESPONSE`, `TOOL`, `TOOL_RESULT`. It never looks at `role`. Every record the runner writes falls through all branches and is dropped.

So `SingleLoopStrategy` dutifully records the assistant message and the `tool_result` message (`strategy.py:60, 142`), `assemble_context()` dutifully passes `history_records=self.transcript_records`, and the assembler dutifully produces zero history messages. A real provider would call the same tool forever (or hallucinate the result) until `max_iterations`.

**Three more problems sit on the same path and should be fixed together:**

1. **Nothing is persisted.** `bootstrap.py:109–121` wires only `stores["cost"]` into `TurnRunner`; the `transcript` and `search` stores that `jsonl_transcript` and `fts5_search` register are consumed by nobody (`grep -rn "stores\[" harness/kernel` → only the registration and `cost`). `KernelServices.__init__` starts every turn with `transcript_records = []` (`runner.py:188`). After 11 sessions and ~15 turns in `/tmp/ws1` there are **zero `*.jsonl` files** anywhere under `.harness/`; `sessions.get` returns `records: []`. Scenarios S2 (converse), S7 (cross-session memory), S9 (resume) cannot work.
2. **Duplicate transcript store.** `harness/kernel/session.py:28` carries its own `JsonlTranscriptStore` (per-session `transcript.jsonl` + `transcript_full.jsonl`) alongside the plugin `stores/jsonl_transcript` (global `data_dir/transcripts/`). Neither is written by the runner. Pick one (the plugin, per the architecture) and delete the other.
3. **Prompt duplication once history works.** `ContextAssembler.assemble()` step 7 (`context.py:287–291`) *always* appends `actual_prompt` as a fresh user message. Once history is loaded, iterations 2…N will produce `…, assistant(tool_use), user(tool_result), user("go")` — Anthropic rejects a `tool_result` that is not immediately followed by the assistant turn, and every provider will see the prompt repeated per iteration. The runner should record the user prompt once and the assembler should only append it when it is not already the last history message.
4. **Message-shape mismatch with the Anthropic adapter.** `records_to_messages` emits legacy tool results as `Role.TOOL`; the Anthropic adapter maps anything that is not `USER` to `"assistant"` (`providers/anthropic/provider.py:165`), which would send `tool_result` blocks in an assistant turn (HTTP 400). The strategy's own shape (USER message containing `ToolResultBlock`s) is the correct one — standardise on it.

**Why the tests are green:** `tests/kernel/test_krn_07_context_compaction.py::test_records_to_messages_with_tool_use_and_results` feeds *legacy-shaped* fixtures (`"source": "MODEL", "type": "PLANNER_RESPONSE"`). `tests/e2e/test_scenarios_s1_s6.py::test_scenario_s2…` asserts that `"hello harness"` appears in the output — but that string comes from the *scripted second response*, not from the tool result reaching the model. `ScriptedProvider` records every `ModelRequest` in `.requests` and even ships an `assert_requests()` helper (`sdk/testing/scripted.py:41, 86`); **no test in the repository uses either**. The architecture's "ScriptedProvider captures requests so tests can assert what the model saw" was built but never exercised.

**Acceptance test to add (should be the first thing written):** one turn with a tool call; assert `provider.requests[1].messages` ends with `assistant[tool_use] → user[tool_result(contains file content)]` and that the prompt appears exactly once; then a second turn in the same session; assert `provider.requests[2]` contains turn 1; then reopen the session from disk and assert the same.

### 4.2 P0 — The web UI cannot complete a chat turn, and there are two frontends in one page

History: WEB-02…WEB-07 built a new 123→164-line `index.html` matched to `app.js`/`events.js`/`cards.js`/`settings.js` (the "stub" the agent apologised for was in fact the modular UI). Commit `e486411` then overwrote it with the 4,318-line pre-remediation page plus 541 lines of legacy compatibility routes in `server.py` — without removing the modular client and without tests for the compat routes.

Observed live (`harness ui -w /tmp/ws1 -p 8765 -s 8766`):

| Path | What happens | Where |
|---|---|---|
| `POST /turn/stream` (what the inline script's Send button calls) | Every request ends with `turn_error: Commands.submit_turn() got an unexpected keyword argument 'model'`. Also relays `model.delta`/`data.delta` but the bus emits `model.text.delta`/`data.text`; drops `provider`; ignores `mode`; uses `active_session` which can be `None`. | `server.py:464–539` |
| `POST /commands/submit_turn` (what `app.js` would call) | Turn **runs** (SSE `/events` shows `turn.started → model.* → cost.updated → turn.completed`) but the HTTP response is **500 `Object of type TurnResult is not JSON serializable`**. | `server.py:294–314`, `commands.py:63–92` |
| `POST /sessions/switch` | Calls `async switch_session()` without `await` (RuntimeWarning in the daemon log); returns `"switched"` for any id, including traversal strings — a harmless no-op, but a lie. | `server.py:709–718` |
| `GET /sessions/{id}` | 500 `'Session' object has no attribute 'get_history'`. | `server.py:698` |
| `POST /rpc` | Routes to `Commands.dispatch`, so legacy method names (`list_sessions`, …) all return `Unknown command`. The inline script calls `/rpc` with legacy names. | `server.py:316` |
| Approvals | The inline script handles `thinking_delta / text_delta / tool_call / turn_complete / turn_cancelled / error` only — no `approval.required`, no `form.*`, and the server sends `turn_error`, not `error`, so failures are invisible. Meanwhile `app.js` opens `/events` with `interactive=True`, so `ApprovalBroker` believes a human is watching and waits the full `approval_timeout_s` (300 s). **Net effect: any WRITE/EXEC in `ask` mode freezes the UI for five minutes with no card.** | `index.html:2784–2810`, `approvals.py:66`, `server.py:370` |
| `app.js` | Loaded as an ES module; binds `#composer-form`, `#btn-send`, `#model-select`, `#strategy-select`, `#session-list`, `#status-dot`, `#inspector-blocks`, … — **0 of 19 ids exist** in the page. Dead weight that still costs a CSP exemption. | `index.html:13`, `static/app.js` |

Related UI debt: `static/assets/` and `static/vendor/` both ship marked/prism/purify; the inline script forces `script-src 'unsafe-inline' 'unsafe-eval'` (`server.py:187`), which is the CSP the plan said to avoid; `tests/frontends/test_web_server.py` (670 lines, 23 tests) never exercises `submit_turn`, `/turn/stream`, or any turn over HTTP.

### 4.3 P0 — Security regressions re-introduced by the legacy `/settings` route

The plan's STOP-04 removed "secrets in settings, fabricated state"; the kernel side honours it (`Secrets` at `~/.harness/secrets.json` 0600, commands expose `secrets.has.*` only, `settings.schema` renders real settings). The restored legacy route bypasses all of it:

1. **Plaintext API keys on disk, world-readable, and echoed back.** `POST /settings {"api_keys": {"openai": "sk-…"}}` writes the key into `<workspace>/.harness/settings.json` (`server.py:902–904`, file mode **0644**), and `GET /settings` returns it to any token holder. Verified live with a canary. Nothing in the kernel reads `api_keys` from that file (only the one-time legacy migrator does), so the provider will still say "No API key" — the user gets the leak **and** the failure.
2. **Fabricated state is back.** `_get_cowork_settings()` (`server.py:799–884`) serves a hard-coded document: `account.is_signed_in: true`, zero-UUID `account_id`/`organization_id`, `keyring_status: "unlocked"`, `web_search_enabled: true`, `vector_memory_enabled: true`, `architect_evaluator_enabled: true`, `pomodoro_timer_minutes: 25`, … None of these correspond to anything that exists.
3. **Inert security toggles.** The UI's Security tab posts `security.default_permission_mode`, `destructive_command_blocking`, `tirith_ssrf_shield`, `session_spend_cap_usd` into that same file; `grep -rn default_permission_mode harness --include=*.py | grep -v frontends/web` → nothing. A user who switches the UI to "ask" is still running whatever `kernel.security.permission_mode` says. Only `default_provider`/`default_model` are forwarded to the real store.
4. **Two settings files with the same name.** Kernel: `~/.harness/settings.json` (global — note the architecture said `<workspace>/.harness/settings.json`; the deviation is defensible but undocumented). Web legacy: `<workspace>/.harness/settings.json`. Same filename, different schema, different owner.
5. **Token in the SSE URL.** `events.js:27–31` sends `/events?token=…` and the server accepts it (`server.py:138–139`). `SECURITY.md` §"WebSocket Single-Use Tickets" describes a 60 s single-use ticket mechanism that does not exist (there is no WebSocket at all). Query-string tokens end up in proxy/browser history; the ticket (or a cookie bound to the token) is the right fix.

### 4.4 P1 — Kernel gaps found while probing

| # | Finding | Evidence |
|---|---|---|
| K1 | `tool.result` events carry **no preview/content** — only `call_id, name, is_error, duration_ms`. Frontends cannot show what a tool returned; the CLI prints an empty "Preview:" box for every tool. Architecture §6 specified `preview ≤ 2 KiB` + `metadata`. | `gate.py` tool.result emits; CLI output |
| K2 | Gate early returns (DENY, approval denied) emit `tool.decision` but **no `tool.result`**, so a UI tool card never closes. | `gate.py:470, 509` |
| K3 | `_resolve_provider` falls back to **the first registered provider** when the requested/default name is unknown or empty. With no config this is `openai_compat`, and the user gets a raw traceback (`ProviderError … No API key provided for openai (HTTP 401)`) before the "Turn Error" panel. STOP-05 said: no silent substitution — raise "no provider configured" with a pointer to settings. | `runner.py:291–308` |
| K4 | `harness run --mock-script` **never works**: the setting is written *after* `bootstrap_kernel()` has already run the mock plugin's `setup()` (`cli.py:70–78`), and because `SettingsStore` is global, the path is persisted to `~/.harness/settings.json` as a side effect of a CLI flag. Only `HARNESS_MOCK_SCRIPT` works. | `cli.py:75`, `plugins/providers/mock/plugin.py:25` |
| K5 | Local executor opens `stdin=PIPE` and never closes it; any command that reads stdin blocks until the tool timeout (observed: 60 s for `cat < /dev/stdin`). D-decision was `stdin=DEVNULL` / `write_eof()`. `GIT_PAGER`/`GIT_TERMINAL_PROMPT` are not set either. | `plugins/executors/local/executor.py:189, 200` |
| K6 | Every bootstrap eagerly creates `<workspace>/.harness/plugins/<name>/` for all 35 plugins (35 empty dirs per workspace). Create lazily on first `data_dir` use. | `registry.py` |
| K7 | Approvals: `approval.required` is never emitted in non-interactive sessions (fine), but the CLI `interactive=False` path also means `harness run` can never approve anything — acceptable, but undocumented; `harness interactive` was not probed. | `approvals.py:66` |

### 4.5 P1 — Tests give false confidence in exactly the broken places

- 350 tests = 116 kernel + 180 plugin conformance + 23 web + 6 e2e + 25 legacy root-level (`tests/test_*.py`) that exist only to keep the dead v1 modules alive (§6).
- No test asserts the contents of a `ModelRequest` after a tool call (`ScriptedProvider.requests` / `assert_requests()` unused anywhere).
- No test round-trips `record()` → `records_to_messages()`; the only `records_to_messages` test uses legacy fixtures.
- No test runs a turn over HTTP (`submit_turn`, `/turn/stream`, `/events` during a turn, approval via `resolve_approval` from the web).
- No test loads a session from disk and checks history is present.
- `harness plugin test --all` simply re-runs `tests/plugins` (180 tests already in the pytest run); CI runs them twice.

### 4.6 P1 — Documentation describes a system that does not exist

`CHANGELOG.md` (0.2.0 section) is substantially fabricated: plugins that are not in the tree ("Claude, OpenRouter, VLLM" providers; "Git, Browser, Desktop" tools; "AST, Sliding Window, Sticky Notes, Summarizer" context; "Rate Limiting" policy; "Sandboxed Executor"), a `builtin/plugins/` directory (actual: `harness/plugins/`), `manifest.json` (actual: `harness_plugin.toml`), "8 SDK base classes `BaseTool`, `BaseProvider`, …" (the SDK is Protocol-based; none of those names exist), "WebSocket … single-use tickets" (SSE), "`harness session`" CLI (does not exist), "`harness.bridges` for Aider, OpenDevin, Cowork Operon" (no such package), "Human-in-the-loop modal dialogs" (not in the shipped page), and the sentence "Legacy monolithic `harness/web/` directory (superseded by `harness/web/`)". `SECURITY.md` repeats the WebSocket-ticket claim. `README.md` marks "SQLite FTS5 Memory … across sessions" and "Native macOS Computer Use (AXUIElement + ScreenCaptureKit)" as **Implemented** — the search index is not wired to anything (§4.1) and `AXUIElement` exists only in the dead `harness/desktop/macos_bridge.py`. `docs/PLUGIN_CATALOG.md`, `docs/architecture/CONTRACTS.md` and `docs/events.schema.json` are accurate.

### 4.7 P2 — Packaging is stale

`packaging/macos/harness-engine.spec` bundles `('harness/web', 'harness/web')` — a directory that no longer exists (static assets live in `harness/frontends/web/static`) — and has `hiddenimports=[]`, so a PyInstaller build would ship none of the 35 dynamically-discovered plugins. (`.gitignore` ends with `*.spec`; the file is tracked, so it is safe, but the rule will bite the next spec.) `scripts/package_macos.sh` was not executed (macOS-only).

---

## 5. Plan-item closure matrix

Legend: ✅ closed · 🟡 partial (works, with gaps listed) · 🔴 open/regressed · ⚪ not verified here

| Item | Status | Note |
|---|---|---|
| STOP-01 auth/CSRF/Host | ✅ | All probes pass on both ports. |
| STOP-02 XSS/CSP/vendored JS/sandbox | 🟡 | Vendored, DOMPurify, sandbox CSP strict. UI CSP needs `'unsafe-inline' 'unsafe-eval'` because of the inline script. |
| STOP-03 traversal | ✅ | `/file/*`, `/artifacts/*`, session ids validated (`SESSION_ID_RE`). |
| STOP-04 secrets/uploads/fabricated state | 🔴 | **Regressed** by legacy `/settings` (plaintext keys 0644, fake account/capabilities). |
| STOP-05 no silent mock / hardcoded paths | 🟡 | No mock fallback, but silent first-provider fallback; raw traceback on missing key. |
| STOP-06 never commit dirty tree | ⚪ | `architect_editor` not re-probed; S3 test exists. |
| STOP-07 jail the tools | ✅ | Live DENY on three escape attempts. |
| STOP-08 executor hardening | 🟡 | Env scrub, setsid ✅; stdin PIPE hang ❌. |
| KRN-01/02/03 SDK types, contracts, bus | ✅ | Envelope `{type,id,ts,data,session_id,turn_id}` matches schema; replay buffer + heartbeat work. |
| KRN-04 Settings + Secrets | 🟡 | Kernel side correct; global-vs-workspace path deviates from arch §5.7 and collides with the legacy file. |
| KRN-05 Registry + boundary | ✅ | Collision checks, trust tiers, capability clamp present. Eager data-dir creation (K6). |
| KRN-06 Jail + Gate + Approvals | 🟡 | Logic correct; missing `tool.result` on early return (K2); no preview on results (K1). |
| KRN-07 Context + Compaction | 🔴 | Assembler tiers work; **history conversion drops every runner record; prompt re-appended per iteration** (§4.1). Compaction unverified against real history for the same reason. |
| KRN-08 TurnRunner/KernelServices | 🔴 | Loop runs, spend cap and wake-up work, but **no history within or across turns; stores unwired**. |
| KRN-09 Session + Commands | 🟡 | Commands/dispatch fine; `TurnResult` not serialisable through `/commands`; duplicate `JsonlTranscriptStore` in `session.py`; `switch_session` unused by web. |
| KRN-10 Conformance kits + ScriptedProvider | 🟡 | Built; `requests` capture never asserted. |
| KRN-11 CLI on the kernel | 🟡 | Works; `--mock-script` broken; empty tool previews; traceback on misconfig. |
| KRN-12 cutover, delete legacy | 🔴 | `harness/core, memory, models, sandboxing, skills, artifacts, desktop` (≈1,800 lines) still present, imported only by 25 legacy root tests and `experimental/`. |
| PLG-01/10/11/12 providers | 🟡 | Adapters exist with recorded tests; Anthropic role mapping will break once history flows (§4.1 #4). Not probed against live APIs (no network). |
| PLG-02 executors/local | 🟡 | See STOP-08. |
| PLG-03/04/05 fs/shell/search tools | ✅ | Live-verified through the gate. |
| PLG-06 policies | ✅ | Order 10/50/60/900, live-verified. |
| PLG-07 context plugins | 🟡 | identity/tool_guidance/skills_catalog/repo_map blocks present in every request; `history` lives in the assembler and is broken. |
| PLG-08 stores jsonl_transcript/cost_ledger | 🔴/✅ | cost ✅; transcript registered, never used. |
| PLG-09 single_loop | 🟡 | Correct logic; starved by KRN-07. |
| PLG-13 mock = ScriptedProvider | ✅ | Deterministic echo + script replay. |
| PLG-14 fts5_search + memory tools | 🟡 | `remember`/`search_memory` tools exist; transcript indexing never happens. |
| PLG-15/16/17/18/20/21/22 | ⚪/✅ | Loaded and conformance-tested; checkpoints hook live-verified; others not individually probed. |
| PLG-19 architect_editor | ⚪ | Not re-probed. |
| WEB-01 Server = Commands + SSE | 🟡 | New routes exist and are secure; `submit_turn` response 500; 21 legacy routes bolted on. |
| WEB-02 ES modules | 🔴 | Built, then orphaned by `e486411`. |
| WEB-03 timeline from events | 🔴 | Shipped page uses the legacy `/turn/stream` relay, which is broken. |
| WEB-04 approvals & forms | 🔴 | `cards.js` has them; shipped page does not → 300 s freeze. |
| WEB-05 settings from schemas | 🔴 | `settings.js`/`settings.schema` exist; shipped page uses the fake cowork document. |
| WEB-06 models/modes/sandbox origin | 🟡 | `models.list` works; mode selector inert (writes to the fake file). |
| WEB-07 context inspector/economics | 🟡 | Economics works; inspector ids absent from page. |
| WEB-08 launcher | ✅ | Reads token, refuses `$HOME`. Packaging spec stale (§4.7). |
| BRG-01 shell_hooks | ⚪ | Loaded; not probed. |
| BRG-02/03/04 mcp_client/mcp_server/acp | ✅ | Server/ACP handshakes verified; client has a fixture echo server test. |
| EXP-01/02 quarantine/deletions | 🟡 | Quarantine ✅; the v1 modules listed under KRN-12 were not deleted. |
| SDK-01/02/03 scaffold/test/docs | ✅ | Verified end-to-end; `PORTING_RECIPES.md` present. |
| DOC-01/02/03 README/SECURITY/CHANGELOG | 🔴 | See §4.6. |
| REL-01 release 0.2.0 | 🟡 | Tag exists; CHANGELOG inaccurate; packaging stale. |

---

## 6. Duplicate / legacy map (what "disjointed" looks like in the tree)

| Concern | Copy A | Copy B | Keep |
|---|---|---|---|
| Transcript store | `harness/kernel/session.py:JsonlTranscriptStore` (per-session) | `harness/plugins/stores/jsonl_transcript/store.py` (global dir) | B, wired via `registry.stores["transcript"]`; delete A. Decide per-session vs per-workspace file layout once (arch §5.1 says per-session dir). |
| Transcript record schema | runner `role`-based | `records_to_messages` legacy `source/type` | Define one `TranscriptRecord` in `harness/sdk/types.py`; both sides use it. |
| Settings | kernel `SettingsStore` (`~/.harness/settings.json`, schema-validated) | web `_get_cowork_settings` (`<ws>/.harness/settings.json`, free-form, with `api_keys`) | Kernel. Delete the cowork document and `/settings` GET/POST. |
| Frontend | `static/app.js` + `events.js` + `cards.js` + `settings.js` + `styles.css` (modular, matches kernel) | `index.html` inline script (1,798 lines, legacy routes) | One of them — see §9 step 3. |
| Vendored JS | `static/assets/{marked,prism_bundle,purify}` | `static/vendor/{marked,prism,purify}` | One directory. |
| HTTP API | `/commands/{name}` + `/events` | 21 legacy routes (`/rpc`, `/turn/*`, `/sessions*`, `/settings`, `/skills*`, `/plugins`, `/files`, `/file/*`, `/diffs`, `/artifacts*`, `/economics`, `/search/code`, `/media/*`, `/tasks`, `/spawn-tree`) | `/commands` + `/events`; move anything the UI truly needs (file read for the editor pane, artifact listing) into `Commands`. |
| v1 modules | `harness/core` (333 l), `memory` (417), `models` (306), `sandboxing` (368, incl. a 3-line shim re-exporting the kernel jail), `skills` (202), `artifacts` (122), `desktop` (53) | plugin ports | Delete A + the 25 root-level tests that only exist to cover them (`tests/test_transcript.py`, `test_fts5_memory.py`, `test_repo_map.py`, `test_semantic_memories.py`, `test_cost_tracker.py`, `test_reasoning_stream.py`, `test_ripgrep.py`, `test_tirith_security.py`, `test_worktree.py`, `test_skills.py`, …). `experimental/architect_editor.py` and `battle_arena.py` import `harness.models`/`harness.sandboxing`; either port those two to the SDK or let them go with the modules. |
| Entry points | `harness/cli.py` (`harness`, `harness-zero`) | `harness/engine_main.py` (PyInstaller shim) | Fine to keep both; fix the spec. |

---

## 7. Architecture-invariant check (from `MODULAR_ARCHITECTURE.md`)

| Invariant | Holds? |
|---|---|
| Plugins import only `harness.sdk`; kernel imports no plugins | ✅ |
| Gate order jail 10 → classifier 50 → tirith 60 → permission 900; any DENY wins; any ASK asks | ✅ |
| `approval:bypass` never a plugin right | ✅ (not re-tested; clamp code present) |
| Context tiers STATIC / SESSION / TURN, ephemeral TURN block, prefix-stable system | ✅ for blocks; ❌ history (§4.1) |
| Event envelope `{v,id,ts,session_id,turn_id,type,data}`; `tool.result` carries preview | 🟡 envelope ✅ (`v` absent, schema agrees); preview ❌ |
| Cards with fallback rendering | 🟡 built in `cards.js`, not in the shipped page |
| One `TranscriptStore` per session; `SearchIndex` per workspace; both written by the runner | ❌ |
| Settings from registered schemas only — "only real settings exist" | ❌ in the web UI |
| Secrets never returned by commands | ❌ via legacy `/settings` |
| ScriptedProvider is the only mock; tests assert what the model saw | 🟡 mock ✅; assertions missing |
| No silent provider substitution | ❌ (first-available fallback) |
| Frontends talk to the kernel only through `Commands` + `EventBus` | ❌ (legacy routes reach into `session_manager`, settings files, subprocess `grep`) |

---

## 8. Test health summary

- **Green and hermetic:** yes (350/350, no `$HOME` writes, 64 s).
- **Coverage shape:** strong on unit contracts (types, manifests, gate decisions, adapters with recorded fixtures), weak on *integration through the real wiring*. The three bugs that matter most (history, turn endpoint serialisation, UI ↔ routes) sit exactly in the untested seams.
- **Minimum additions** (each is < 100 lines):
  1. `tests/kernel/test_turn_roundtrip.py` — the acceptance test in §4.1.
  2. `tests/frontends/test_web_turn.py` — start `HarnessWebServer` with the mock, `POST /commands/sessions.create` → `POST /commands/submit_turn` (assert 200 + serialisable `TurnResult`), read `/events` during the turn, `resolve_approval` round trip, and a `Last-Event-ID` replay.
  3. `tests/frontends/test_static_contract.py` — parse `index.html`, assert every id referenced by `app.js` exists (or delete `app.js`), assert no `alert(` and no `/turn/stream`.
  4. A test that `GET /settings` never contains a value from `Secrets` (or delete the route).
  5. Delete the 25 legacy root tests together with the modules they cover.

---

## 9. How to make it cohesive — prioritised sequence

Do these in order; each step leaves the tree releasable.

1. **Fix the loop (P0, ≈1 day).** Define one transcript record schema in the SDK; make `records_to_messages()` read it (keep a small adapter for v1 files if resuming old sessions matters); record the user prompt once and stop re-appending it in the assembler; standardise tool results as `USER` + `ToolResultBlock`; wire `registry.stores["transcript"]` and `["search"]` into `TurnRunner` in `bootstrap.py`; load the session's records into `KernelServices` at turn start; delete `session.py:JsonlTranscriptStore`. Land the §4.1 round-trip test in the same commit. *Acceptance: S2/S7/S9 pass with request assertions; `transcript.jsonl` appears under the session dir; `sessions.get` returns records.*
2. **Fix the turn endpoints (P0, ≈2 h).** `_handle_command` serialises dataclasses (`TurnResult.to_dict()` or a `default=` encoder); add `tool.result.preview`; emit `tool.result` on gate early returns; `_resolve_provider` raises `ProviderError(kind="unconfigured")` with a settings hint instead of falling back; CLI prints the panel without a traceback. *Acceptance: web turn test green; `harness run` with no config prints one friendly error.*
3. **Choose one frontend (P0 decision, then ≈2–4 days).** Two honest options:
   - **(a) Modular path:** restore the WEB-07 `index.html` (`git show 437f2f3:…`), then port the *visual* layout from the 4,318-line page piece by piece onto `app.js`'s element ids. Settings come from `settings.schema`; approvals/forms from `cards.js`. Delete the inline script, the 21 legacy routes, `_get_cowork_settings`, the `alert()` buttons, `static/assets/` duplicates; drop `'unsafe-inline' 'unsafe-eval'` from CSP.
   - **(b) Legacy-page path:** keep the big page, but rewrite its script to use `api.js`/`events.js` (`/commands/*`, `/events`), remove the modular client, and still delete the legacy routes and fake settings. This is more work than (a) because the inline script's data expectations (`USER_INPUT`/`PLANNER_RESPONSE` shapes, `/rpc` method names, `/turn/stream` event names) are all v1.
   Either way: the shipped page must handle `approval.required`, `form.requested`, `turn.error`, and `tool.result` with previews before it can be called wired.
4. **Close the security regressions (P0, ≈half a day, can be done first if step 3 slips).** Remove `/settings` GET/POST (or make GET return `settings.schema` + values from the kernel store with secrets masked, and POST route `api_keys` to `Secrets.set` with 0600); purge any `api_keys` already written to workspace `settings.json`; move the SSE token out of the query string (short-lived ticket as `SECURITY.md` already describes, or a `HttpOnly` cookie set by `/`); fix `switch_session` await or delete the route.
5. **Delete the dead v1 tree (P1, ≈half a day).** `harness/{core,memory,models,sandboxing,skills,artifacts,desktop}` + their 25 root tests; port or drop `experimental/architect_editor.py` and `battle_arena.py` imports; fix the PyInstaller spec paths and add plugin `hiddenimports`; lazy-create plugin data dirs; close stdin in the executor; make `--mock-script` apply before bootstrap (pass via `bootstrap_kernel(overrides=…)`) and stop persisting it.
6. **Make the docs true (P1, ≈2 h).** Rewrite the 0.2.0 CHANGELOG from `git log` + `PLUGIN_CATALOG.md`; remove the WebSocket-ticket section from `SECURITY.md` or implement it; downgrade README rows "SQLite FTS5 Memory" and "Native macOS Computer Use" to *Partial* until step 1 lands and the desktop bridge is ported; document the global `~/.harness/settings.json` decision in `DECISIONS.md`.
7. **Then** re-tag (`0.2.1`), with CI extended by the tests in §8.

---

## Appendix A — Commands used (reproducible)

```bash
python3 -m venv .venv && . .venv/bin/activate && pip install -e ".[dev]"
export HOME=$(mktemp -d) HARNESS_CONFIG_DIR=$HOME/.harness
ruff check . && pytest -q                       # 350 passed
harness events schema | diff docs/events.schema.json -
harness plugin doctor --all && harness plugin test --all && harness plugin list

# kernel loop probe (captures ModelRequests; see §4.1)
HARNESS_MOCK_SCRIPT=/tmp/mock.json python - <<'EOF'
# bootstrap_kernel → wrap registry.get_provider("mock").generate → submit_turn → print req.messages
EOF

# daemon probes (see §2, §4.2, §4.3)
harness ui -w /tmp/ws1 -p 8765 -s 8766 -h 127.0.0.1 --no-open
curl -s -o /dev/null -w '%{http_code}\n' http://127.0.0.1:8765/rpc                      # 401
curl -s -H "X-Harness-Token: $T" -H 'Content-Type: application/json' \
     -X POST http://127.0.0.1:8765/commands/submit_turn -d '{"session_id":"…","prompt":"hi","options":{"provider":"mock"}}'  # 500
curl -s -H "X-Harness-Token: $T" -H 'Content-Type: application/json' \
     -X POST http://127.0.0.1:8765/turn/stream -d '{"prompt":"hi","provider":"mock"}'   # turn_error
curl -s -H "X-Harness-Token: $T" -H 'Content-Type: application/json' \
     -X POST http://127.0.0.1:8765/settings -d '{"api_keys":{"openai":"sk-CANARY"}}'     # written 0644, echoed by GET
printf '%s\n' '{"jsonrpc":"2.0","id":1,"method":"initialize",…}' | harness mcp -w /tmp/ws1
harness plugin new demo-tool --kind tool && harness plugin doctor demo-tool && harness plugin test demo-tool
```

## Appendix B — Files/lines referenced most often

`harness/kernel/runner.py` (188, 291–308, 531–571) · `harness/kernel/context.py` (89–166, 271–291) · `harness/kernel/bootstrap.py` (109–121) · `harness/kernel/session.py` (28–123) · `harness/kernel/gate.py` (447–512) · `harness/kernel/approvals.py` (61–67) · `harness/cli.py` (60–92) · `harness/frontends/web/server.py` (138, 186–187, 294–314, 464–539, 698, 709–718, 794–935) · `harness/frontends/web/static/index.html` (13, 1624–2319 alerts, 2519–4316 inline script) · `harness/frontends/web/static/{app,events,api,cards,settings}.js` · `harness/plugins/executors/local/executor.py` (165–215) · `harness/plugins/strategies/single_loop/strategy.py` · `tests/e2e/test_scenarios_s1_s6.py` (138–192) · `CHANGELOG.md`, `SECURITY.md`, `README.md` · `packaging/macos/harness-engine.spec`.
