# Harness-Zero — End-to-End Code Review

**Date:** 2026-10-13  
**Scope:** Entire repository at commit `a0afc75` — `harness/` Python package (~6.4k LOC), `harness/web/index.html` (3.2k lines), `launcher/HarnessZeroApp.swift`, `scripts/`, `tests/`, `docs/`, packaging.  
**Method:** Read all source; installed in a clean venv (Python 3.11.2, aiohttp 3.14.5, httpx 0.28.1); ran `pytest` (48 passed in 3.4s); started `harness daemon` against a throwaway git repo and probed it with `curl` / direct `_dispatch_tool` calls. Every finding marked **[verified]** was reproduced live, not inferred.

---

## 1. Executive summary

Harness-Zero is a large and ambitious design document set (22 specs, ~3k lines) paired with a Python implementation that is considerably thinner than the docs and README imply. The actual runtime is:

> prompt → 5-tier prompt assembly → one model call → up to 10 tool-dispatch iterations over 10 tools → transcript + FTS5 indexing

That core loop works and is reasonably structured. However:

1. **The local daemon is unauthenticated and CSRF-able.** Any website open in the user's browser can write files into the workspace, run agent turns (which execute shell commands), and overwrite API keys. **[verified]**
2. **Architect mode auto-commits the user's entire dirty working tree** — including `.env` secrets and the agent's own SQLite session databases — even when the edit *failed*. **[verified]**
3. **Agent tools are not confined to the workspace.** `view_file` reads any file, `write_to_file` writes anywhere, `edit_file` has no checks at all, `run_command` accepts an arbitrary `Cwd` and creates it. The "guardrails" are a trivially bypassable regex denylist and the Tirith capsule is never called. **[verified]**
4. **Roughly half of the advertised subsystems are either simulated or not wired into the agent loop** (MCP bridge, browser CDP, UI-TARS, circuit breakers, cost tracker, spawn ledger, semantic memory, repo map, state graph, concurrency gate, compaction, login tokens). Tests exercise them as isolated units, which is why the suite is green.
5. **On any provider error, or when a key is missing, the router silently falls back to a keyword-matching "mock" engine that still executes tools**, and the out-of-box no-key experience renders raw `<USER_REQUEST>` tags after every tool call. **[verified]**
6. **The test suite writes to `~/.harness/config.json`** on the developer's machine. **[verified]**

Recommendation: treat the current `main` as a prototype. Do not point it at a repository you care about until items in §3 (Critical) are fixed. A prioritised remediation plan is in §7.

---

## 2. What's good

Worth stating clearly, because the rest of this document is critical:

- **Module boundaries are sensible.** `core / models / sandboxing / memory / orchestration / skills` is a clean split, and most modules are small and readable.
- **Transcript design is solid.** Dual `transcript.jsonl` (truncated) + `transcript_full.jsonl` with byte-offset indexing is a good idea and the O(1) seek genuinely works (`TranscriptEngine.get_step`, `FTS5MemoryIndex.search(fetch_exact=True)`).
- **FTS5 integration is clean** — WAL mode, `unicode61 remove_diacritics 2`, BM25 ranking, query sanitisation that defeats FTS syntax injection (covered by `test_red_team_fts5_syntax_injection`).
- **`CommandExecutor`'s sync→background transition** (`WaitMsBeforeAsync`) with process-group kill, `PAGER=cat`, bounded output buffers, and a reactive inbox callback is a reasonable design.
- **`ReasoningStreamParser`** handles `<think>` tags split across arbitrary chunk boundaries correctly — this is the kind of thing that's usually wrong.
- **`StateGraphEngine`, `SpawnLedger`, `ConcurrencyGate`** are tidy, self-contained, and tested. They just aren't used yet.
- **Tests are fast and deterministic** (3.4s, no network) and there is a `test_red_team.py` — the instinct is right, even if (see §4.4) it tests units rather than integration.
- **Skills registry** (3-tier, YAML front-matter, progressive disclosure) is simple and works.

---

## 3. Critical findings

### 3.1 Daemon has no authentication and accepts cross-site requests — **[verified]**

`harness/core/daemon.py`

- No route checks any credential. `generate_login_url()` / `_handle_login` exist, but `/login` only returns JSON; it sets no cookie and nothing consults `_login_tokens` afterwards. Also, `harness url` builds the token in a *new, never-started* `HarnessDaemon` instance, so the token never reaches the running daemon. The login feature is non-functional by construction.
- `request.json()` in aiohttp does not enforce `Content-Type`, so every POST endpoint accepts a CORS "simple request" (`text/plain`) — no preflight, so **any web page can fire it**:

```
$ curl -X POST http://127.0.0.1:8765/file/save -H 'Content-Type: text/plain' \
       -H 'Origin: https://evil.example' --data '{"path":"pwned.txt","content":"..."}'
{"status": "saved", "path": "pwned.txt", "size": 29}

$ curl -X POST http://127.0.0.1:8765/rpc -H 'Content-Type: text/plain' \
       --data '{"jsonrpc":"2.0","id":1,"method":"turn.submit","params":{"prompt":"check git status"}}'
{"jsonrpc": "2.0", "result": {... ran `git status -s` via run_command ...}}
```

  Reachable this way: `/file/save` (arbitrary write in workspace), `/rpc turn.submit` and `/turn/stream` (agent turn → shell execution), `/settings` (overwrite API keys and default provider in `~/.harness/config.json`), `/sessions/switch` (see 3.4), `/media/upload` (unbounded disk write).
- No `Host`/`Origin` validation → DNS-rebinding also works (`curl -H 'Host: evil.example' …/health` → 200).
- The Swift launcher and `Info.plist` set `NSAllowsArbitraryLoads=true`; the web UI loads `marked` from jsdelivr and fonts from Google with no SRI — so the "local-first" app also has a remote supply-chain dependency in its trusted origin.

**Impact:** drive-by RCE on the developer's machine from any website while the studio is running.

### 3.2 Architect mode commits the entire dirty working tree, even on failure — **[verified]**

`harness/orchestration/architect_editor.py::execute` / `_git_commit`

- `_git_commit` runs `git add -A` then `git commit`. With `auto_commit=True` (the default, and what both `/rpc architect.submit` and `/turn/stream mode=architect` use) and no `test_command` (the UI never sends one), it commits **regardless of `applied_any`**.
- Reproduced with no API key configured (so the "architect" was the mock): result was `success: false, final_message: "Diff failed to apply cleanly."` **and** `commit_sha: 87c8ac1…`. That commit contained the user's pre-existing `.env` (`user_secret=hunter2`), their WIP edit to `app.py`, an unrelated file, and **19 files total including `.harness/sessions/*/memory.db`, `*.db-wal`, `*.db-shm`, `transcript*.jsonl`**.
- Root causes: (a) `git add -A` instead of staging only touched files; (b) commit not gated on `applied_any`/test success; (c) the coordinator writes `.harness/` **inside the user's workspace** without adding it to `.git/info/exclude`, so every tool (`git status`, `git add -A`, `_handle_files`) sees session databases as project files.
- Also: the UI's architect mode feeds `list(workspace.glob("*.py"))[:10]` — the first ten top-level `.py` files, unrelated to the request — to the model and applies the same diff text against each one in turn.

### 3.3 Tools escape the workspace; guardrails are cosmetic — **[verified]**

`harness/core/coordinator.py::_dispatch_tool`, `harness/sandboxing/guardrails.py`

| Tool | Containment | Verified behaviour |
|---|---|---|
| `view_file` | Only `PROTECTED_SYSTEM_PATHS` denylist | Read `/tmp/secret_outside.txt` outside workspace → `1: token=abc123` |
| `write_to_file` | `evaluate_file_write_risk` returns ELEVATED+`requires_approval=True` for outside-workspace… which the coordinator ignores | Wrote `/tmp/outside_write.txt` → "File successfully written" |
| `edit_file` | **None** | No risk evaluation at all |
| `run_command` | regex denylist; `Cwd` unchecked, `mkdir -p`'d by executor | `Cwd=/tmp/brand_new_dir_created_by_agent` → directory created, command ran there |
| `run_command` denylist | pattern-based | `R=rm; $R -rf /` passes; so do `find -delete`, `git push -f`, `git checkout -- .`, `python -c "shutil.rmtree(...)"`, `curl … \| sh` |

- `RiskLevel.ELEVATED` + `requires_approval=True` is computed and then discarded everywhere — there is no approval queue, dialog, or deny-by-default. Doc 12 ("Human-in-the-Loop Security Tiers … Approval Dialogs") describes something that does not exist in code.
- `TirithSecurityCapsule.audit_command` / `sanitize_output` are **never called** from the coordinator; `self.tirith` is constructed and unused. The red-team test for credential-scraping tests the class, not the agent.
- `CommandExecutor` inherits the full `os.environ` into child processes → `ANTHROPIC_API_KEY` etc. are one `env` tool call away from landing in the transcript (and `memory.db`, and — per 3.2 — a git commit).
- Denylist protected-path check uses substring matching (`"/bin" in command`), so `/usr/local/bin/foo` is flagged ELEVATED (harmlessly, because ELEVATED is ignored).

### 3.4 Path traversal via `session_id` — **[verified]**

`daemon.py::_handle_session_switch` → `coordinator.switch_session`

```
$ curl -X POST …/sessions/switch -d '{"session_id":"../../../../../tmp/traversal_target"}'
{"error": "unable to open database file"}
$ ls -d /tmp/traversal_target     # created
```

- `session_dir = workspace/.harness/sessions/<session_id>` with no validation → `mkdir(parents=True)` anywhere on disk, then transcripts/DBs/artifacts written there.
- The failure is non-atomic: after the error, `/health` reported `"session_id": "../../../../../tmp/traversal_target"` — the coordinator was left half-switched (new `session_id`, new `transcripts`, old `memory`).
- `GET /sessions/{session_id}` has the same unsanitised join (returns 200 for `..%2F..%2F..%2Ftmp` on aiohttp 3.14). `/artifacts/{filename}` and `/media/{filename}` happened to be safe (`Path(name).name` / 404), but only by accident of aiohttp's `%2F` handling — they should be validated explicitly.

### 3.5 Stored/reflected XSS in the trusted origin (port 8000)

`harness/web/index.html` lines 1964, 2121, 2159, 2784

- Model output is rendered with `element.innerHTML = marked.parse(text)`. Current `marked` has no sanitiser; raw HTML and `javascript:`/`onerror=` attributes pass through. Model output is attacker-influenceable (file contents, search results, web pages echoed back by the model, or a malicious skill playbook rendered at line 2784).
- The dual-port design isolates *artifacts* on 8001, but the chat pane itself runs in the origin that holds all API access, so the SOP story is undermined.
- No CSP header on `/`.

### 3.6 Silent fallback to a keyword-matching engine that executes tools — **[verified]**

`harness/models/router.py`

- `_call_openai_compatible` catches **every** exception for every candidate model (auth 401, 400 schema error, 30s timeout…), then tries OpenAI, then returns `_mock_agent_turn()` with a `> [!WARNING]` banner. `generate()` does the same when the provider's key is missing. The mock emits `run_command` (`pytest`, `git status -s`), `search_codebase` and `write_to_file` calls based on substrings like `"test"`, `"status"`, `"find"`, `"plan"` — so e.g. "what's the latest status?" with a bad key runs `git status`, and a prompt containing "test" runs the test suite.
- The mock's "plan" branch writes to a hardcoded absolute path on the author's machine: `/Users/brightech/.gemini/antigravity/scratch/Harness-Zero/harness/artifacts/architecture_plan.md`. (This is also why `harness/artifacts/` — a *source* directory containing `manager.py` — is in `.gitignore`.)
- `--provider mock` on the CLI is **not honoured** if any key is discoverable: `execute_turn` only uses mock when `_mock_responses` is set or `router.default_provider == "mock"`, and `ModelRouter.__init__` overrides `default_provider` from `~/.harness/config.json`, env vars, **and `~/.hermes/.env`** (silently reading another tool's secrets). A user who asks for mock can be billed.
- The mock's tool-follow-up detection compares the last message to the literal string `"Continue solving the task."`, but `PromptCacheBuilder` wraps it in `<USER_REQUEST>…</USER_REQUEST>\n\n<CONTEXT_TIMESTAMP>…`. So in the real loop it never matches, and every tool-using turn ends with:

  > `### Architectural Assessment: <USER_REQUEST>\nContinue solving the task.\n</USER_REQUEST>\n\n<` …

  This is the default experience for anyone without an API key.

---

## 4. High-severity findings

### 4.1 Test suite mutates the developer's home directory — **[verified]**

`tests/test_ui_routes.py` POSTs `/settings` → `_handle_settings_post` → `router.save_config(default_provider="mock", default_model="mock-model")` → writes `~/.harness/config.json`. After a clean `pytest` run on a fresh machine:

```
$ cat ~/.harness/config.json
{"default_provider": "mock", "default_model": "mock-model"}
```

On a developer machine with a real config this silently resets their default provider. `ModelRouter._config_path()` also `mkdir`s `~/.harness` as a side effect of *reading* config. Tests also bind fixed ports (8888/8889/8990/8991) and `test_ui_routes` hardcodes `/Users/brightech/Claude`.

### 4.2 Simulated features presented as live

| Component | Reality |
|---|---|
| `MCPBridge.call_tool` | Returns the string `"MCP result from {server}:{tool} with args {...}"`, `is_error: False`. No process is spawned, no JSON-RPC. The model receives fabricated success for `list_directory`, `query_db`, `search_repositories`. **[verified]** |
| `/settings → extensions.mcp_servers` | Hardcoded `"status": "connected"` for three servers that never start. |
| `/settings → account` | Hardcoded fake `account_id`, `email: brightech@local.device`, `is_signed_in: true`, `keyring_status: unlocked`. |
| `BrowserCDPEngine` | `connect()` always ends in `enable_mock_mode()`; `navigate/observe/act/extract` return canned data in *both* modes. No websocket/CDP code exists. Not exposed as a tool anyway. |
| `MacOSDesktopBridge.predict_visual_click` | Returns `{x: 640, y: 480, confidence: 0.85, engine: "ui_tars_visual_grounding"}` unconditionally. |
| `harness url` / `/login` | See 3.1 — token never reaches the daemon; nothing enforces it. |
| `/turn/stream` "streaming" | Runs the whole turn, then replays the finished text in 28-char chunks with `asyncio.sleep(0.01)`. Cosmetic. |
| `/events` SSE | Writes one event and returns; stream closes immediately. (Unused by the UI.) |
| `scripts/comprehensive_features_verification.py` | Prints ✓ for "CDP connection", "MCP Bridge", "UI-TARS" etc. against the mocks above — it certifies the simulation. |
| `RepoMap` | AST/regex symbol list, no tree-sitter, no PageRank, not injected into any prompt. README: "Tree-sitter PageRank Whole-Codebase Map". |

### 4.3 Subsystems constructed but never used in the live loop

In `AgentCoordinator.__init__` the following are instantiated and then never consulted during `execute_turn`:

- `IntelligentModelRouter` (`classify_intent` / `select_active_endpoint` never called). In `execute_turn`, `endpoint = None` is set and `endpoint.record_success()/record_failure()` are dead branches — **the circuit breaker can never trip**.
- `TokenCostTracker` — `record_turn` never called; `usage` from `raw_response` never parsed; `/economics` and the UI's Economics panel are always `$0.00`.
- `SpawnLedger` — `spawn_subagent` never called; `/spawn-tree` always `[]`.
- `SemanticMemoryStore.render_memory_context` — never injected into the prompt.
- `ConcurrencyGate` — never acquired.
- `TirithSecurityCapsule` — see 3.3.
- `TranscriptEngine.compact_trajectory` — never called; no context compaction happens, the loop just slices the last 25 compact records.
- `StateGraphEngine`, `AgentArena`, `ArchitectEditorLoop`'s self-healing path — library code reachable only from tests/scripts (Arena's default worker is `asyncio.sleep(0.1)` + canned score).

Confirmed with `grep` over `harness/`: zero call sites outside the definitions.

### 4.4 Provider adapters and the "prefix cache" story

- **Conversation structure is lossy for every provider.** History is rebuilt from compact transcript records as `{"role":"assistant","content":text}` (tool calls dropped) and `{"role":"user","content":"[Tool Result]: …"}`. No provider receives native `tool_calls`/`tool_use` + `tool_result` pairs. Models lose the call↔result linkage and the truncated (1500-char) compact content is what gets re-sent.
- **Gemini gets no tools at all** (`_call_gemini` ignores `tools`), uses `role: user` for system, and puts the API key in the URL query string.
- **Anthropic:** no `cache_control` breakpoints, so the "90%+ prompt-cache hit rate" claim cannot hold there; `max_tokens` fixed at 4096; settings `temperature`/`max_tokens`/`reasoning_effort` are stored but never read by the router.
- **OpenAI-compatible:** 30s timeout (vs 120s elsewhere) — reasoning models routinely exceed it and trip the mock fallback (likely the "query timeout" the last commit message refers to); `temperature` sent to `o1`/`o3` which reject it; OpenRouter silently substitutes `deepseek/deepseek-chat` → `llama-3.3-70b` → OpenAI `gpt-4o-mini` on *any* error, changing data-processing jurisdiction without consent.
- Tool schemas are serialised into the system prompt **and** sent as native `tools` — tokens paid twice.
- The 25-record sliding window of history breaks prefix invariance after the 25th step: every subsequent turn shifts the prefix and invalidates the cache.
- `get_tool_schemas` docstring says "alphabetically-sorted"; `call_mcp_tool` is appended last (the builder re-sorts, so it's only misleading).

### 4.5 Hardcoded author environment

- `router.py`: `/Users/brightech/.gemini/antigravity/scratch/Harness-Zero/...`
- `launcher/HarnessZeroApp.swift`: default workspace `~/.gemini/antigravity/scratch/Harness-Zero`, **falling back to `$HOME`** — the packaged app runs the agent with the user's entire home directory as workspace (`_handles_files` rglobs it, `.harness/` is created in it, and if `$HOME` is a git repo, 3.2 applies to it).
- `ripgrep.py`: falls back to executing `/Applications/ChatGPT.app/Contents/Resources/rg`.
- `tests/test_ui_routes.py`: `/Users/brightech/Claude`.
- `daemon.py` default `trusted_folders` includes `~/Claude`.

### 4.6 Functional bugs

- `manage_task` with `Action: "send_input"` falls through the if-chain and returns **`Unknown tool: manage_task`**. **[verified]**
- `CommandExecutor.kill_task` sets `status = "killed"`, then `_monitor_background_task` overwrites it with `completed`/`failed` when the process exits.
- `CommandExecutor.run` opens `stdin=PIPE` and never closes it; commands that read stdin hang until `WaitMsBeforeAsync` and become "background tasks".
- `_read_stream` uses `readline()` with the default 64 KiB limit; a longer line raises inside the reader task and the rest of that stream's output is silently lost (`gather(return_exceptions=True)`).
- `TranscriptEngine.read_compact_history` has no try/except — one corrupt line makes every future `execute_turn` in that session raise.
- `switch_session` leaks the previous `FTS5MemoryIndex` / `SpawnLedger` sqlite connections and does not re-register the MCP `database_mcp` path.
- `_handle_diffs`: `git diff HEAD` empty ⇒ `git diff` is necessarily empty too (redundant fallback); untracked files never shown.
- `_handle_files`: `rglob("*")` walks `.git/`, `node_modules/`, `.venv/` fully and filters afterwards; then truncates to 100 entries.
- `DiffApplier`: replaces the *first* occurrence silently when the SEARCH text appears multiple times; no file-path markers so multi-file edits are impossible; regex requires `\n` (CRLF files always fail); the "Try normalized whitespace" comment has no code behind it.
- `WorktreeManager.get_diff` defaults to `main`; `create_worktree` fails if the branch survived a previous crash ("branch already exists"); `AgentArena` never removes the *winning* worktree.
- `TirithSecurityCapsule.calculate_blast_radius`: `any("-f" in t for t in tokens)` marks `rm my-file.txt` as recursive/force.
- `FTS5MemoryIndex` docstring says "cross-session memory" but the DB is per-session (`session_dir/memory.db`); `/search` and `search_memory` only ever see the current session.
- `_mock_agent_turn` default branch prints `user_text[:60]` (raw tags) instead of `clean_text`.

### 4.7 Secrets handling

- API keys saved to `~/.harness/config.json` in plaintext with default `0644` permissions.
- Keys are posted from the browser over plain HTTP to the daemon (fine on loopback, but see 3.1 — any site can *also* POST new keys).
- Keys read from `~/.hermes/.env` without the user opting in.
- Subprocess env inheritance (3.3) + transcripts + FTS index + `git add -A` (3.2) form a complete exfiltration chain from env var to committed repository.

---

## 5. Medium / low findings

**Repo hygiene**
- No `LICENSE` file. Given the README frames the project as "reverse-engineered" from proprietary products, licensing and provenance deserve an explicit statement.
- `.gitignore` ignores `harness/artifacts/` (a source package — new files there are silently untracked) and `lib/` (a very common source dir name).
- `AppIcon.icns` (1.8 MB) and `harness-engine.spec` are build artifacts committed at the root.
- README: "All 15 modular test suites" — there are 25 test files / 48 tests; "22 modular engineering blueprints" — there are 22 files numbered 00–21, OK. `harness interactive` is documented; `harness ui` (the main entry point) is not.
- No lockfile despite README recommending `uv`; no CI config; no `ruff`/`mypy` config. `pyflakes` reports 43 unused imports plus `undefined name 'Any'` in `concurrency_gate.py:140` (masked by `from __future__ import annotations`).
- `Skill.metadata: Dict[str, any]` (builtin `any`, not `typing.Any`).
- Single squashed commit — no history to bisect.

**Daemon / async**
- Synchronous `subprocess.run` (worktree, diffs, architect commit) and synchronous sqlite calls run on the event loop.
- `_handle_media_upload` multipart path has no size cap; files land in `session_dir/media` with only `Path(...).name` sanitisation (adequate).
- `_handle_turn_cancel` cancels the task but `execute_turn` has no cancellation handling — a transcript can be left with a `USER_INPUT` and no response.
- `/turn/stream` drops `provider` (only `model` is forwarded), so the UI's model selector relies entirely on prefix auto-mapping in `generate()`.
- `start()` sandbox app shares `coordinator.artifacts` by reference — fine, but "untrusted" origin reads the same manifest the trusted one writes; artifact HTML is wrapped but not `sandbox`-attributed.

**Launcher / packaging**
- `applicationWillTerminate` calls `proc.waitUntilExit()` on the main thread with no timeout → app hangs on quit if the daemon doesn't exit on SIGINT.
- `inspectWebView` only `console.log`s; it does not open the inspector.
- Health poll falls through to loading the URL even when health never succeeded.
- `package_macos.sh` assumes `.venv/bin/pyinstaller` exists but `pyinstaller` is not in any dependency group.

**Docs**
- The 22 docs are internally consistent but describe a target architecture (Go/Bun daemons, ConnectRPC, tree-sitter, approval state machines, CDP) that the Python code does not implement. There is no "status" column distinguishing implemented / partial / planned, so a reader cannot tell which claims are live.

---

## 6. Tests — assessment

- 48 tests, 3.4 s, deterministic, no network. Good.
- Coverage is **unit-level**; integration gaps are exactly where the critical bugs are:
  - No test asserts a tool is confined to the workspace (the traversal test accepts `"does not exist"` as a pass).
  - No test exercises `architect.submit` end-to-end against a dirty repo.
  - No test checks that `Tirith`/`SecurityPolicy` are *invoked* by the coordinator.
  - No test sends `Content-Type: text/plain` or a foreign `Origin`.
  - `test_coordinator` uses `ModelRouter()` with no explicit keys, so it depends on the developer's environment (works today because `set_mock_responses` short-circuits).
- `test_red_team.py` is a good idea and should become the backbone of a real integration suite once the fixes below land.

---

## 7. Recommended remediation plan (ordered)

**P0 — before anyone runs this on a real repo**

1. **Daemon auth + CSRF/rebinding defence.** Generate a random bearer token at startup; inject it into `index.html`; require it (header) on every non-`GET` and on `/file/content`, `/sessions/*`, `/settings`, `/media/*`. Enforce `Content-Type: application/json` on JSON routes. Reject requests whose `Host`/`Origin` is not `127.0.0.1|localhost:<port>`. Add a CSP on `/`. Vendor `marked` and add DOMPurify (or switch to a sanitising renderer); remove external font/CDN loads.
2. **Workspace jail.** One helper `_resolve_in_workspace(raw) -> Path` (resolve symlinks, `relative_to(workspace)`, else raise) used by `view_file`, `write_to_file`, `edit_file`, `run_command.Cwd`, `DiffApplier`, `ArchitectEditorLoop`, `/sessions/*` (validate `session_id` against `^session_[0-9a-f]+$`). Make `switch_session` atomic (build new objects, then swap).
3. **Approval or deny-by-default.** Route `RiskLevel.ELEVATED` and `DESTRUCTIVE` to the existing `inbox`/SSE channel as an approval request (or deny with a clear tool error until a UI exists). Call `Tirith.audit_command` before execution and `sanitize_output` after. Pass a scrubbed env (drop `*_API_KEY`, `*_TOKEN`, `*SECRET*`) to subprocesses.
4. **Architect/editor safety.** Default `auto_commit=False`; never `git add -A` — stage only files `DiffApplier` actually modified; never commit when `applied_any` is False or tests failed; write `.harness/` into `.git/info/exclude` on coordinator start (or move session data to `~/.harness/sessions/<workspace-hash>/`).
5. **Make mock explicit.** Mock only when `provider == "mock"` was *requested*; on provider errors raise and surface the error to the UI/CLI (there is already an `error` SSE event). Remove `~/.hermes/.env` reading or make it opt-in. Remove the hardcoded `/Users/brightech/...` path. Fix the follow-up detection (or drop the mock's tool-calling branches entirely).

**P1 — correctness and honesty**

6. Fix tests to isolate `HOME` (`monkeypatch.setenv("HOME", tmp_path)`, or make `ModelRouter` accept a `config_path`) and use ephemeral ports (`port=0` + `site._server.sockets[0].getsockname()`).
7. Either implement or clearly label as *simulated*: MCP (`stdio` JSON-RPC is ~150 lines with `asyncio.subprocess`), browser CDP, UI-TARS, login tokens. Make `/settings` report real state and drop the fake account block.
8. Wire the dormant subsystems or delete them from the hot path: parse `usage` → `cost_tracker.record_turn`; use `IntelligentModelRouter` for real failover; inject `semantic_memory.render_memory_context()` and (optionally) `RepoMap` into Tier 3; call `compact_trajectory` when history exceeds a budget.
9. Proper tool-call protocol per provider (native `tool_calls`/`tool_use` + `tool_result` from the *full* transcript, not the truncated one); `cache_control` for Anthropic; tools for Gemini (or document that Gemini is chat-only); raise OpenAI-compatible timeout to match others; omit `temperature` for reasoning models; stop sending tool JSON in the system prompt when native tools are used.
10. Fix `manage_task send_input`, `kill_task` status race, stdin handling, `readline` limit (`limit=` on the stream or `read(n)`), `read_compact_history` resilience, connection cleanup in `switch_session`.

**P2 — hygiene**

11. Add `LICENSE`; remove `harness/artifacts/` and `lib/` from `.gitignore`; move `AppIcon.icns`/`.spec` under `packaging/`; add `ruff` + CI running `pytest` with `HOME` isolated; fix the README test count and document `harness ui`.
12. Add an "Implementation status" table to the README/docs (Implemented / Partial / Simulated / Planned) so the specs and the code can diverge honestly.
13. Launcher: default workspace should be a user-chosen folder (NSOpenPanel on first run), never `$HOME`; bound `waitUntilExit`.

---

## 8. Verification log (what was actually run)

```
python3 -m venv .venv && pip install -e ".[dev]"      # clean env, Python 3.11.2
pytest -q                                              # 48 passed in 3.37s
cat ~/.harness/config.json                             # created by the test run
harness daemon --workspace /tmp/ws --port 8765 ...     # throwaway git repo
curl text/plain POST /file/save  (foreign Origin)      # 200, file written
curl text/plain POST /rpc turn.submit                  # 200, tool executed
curl POST /sessions/switch {"session_id":"../../../../../tmp/traversal_target"}
                                                       # dir created, coordinator half-switched
curl POST /rpc architect.submit (no API key, dirty tree, .env present)
                                                       # success:false + commit_sha set; 19 files committed
_dispatch_tool: view_file /tmp/…, write_to_file /tmp/…, run_command Cwd=/tmp/new, R=rm bypass,
                manage_task send_input, call_mcp_tool  # all as described above
pyflakes harness/ tests/ scripts/                      # 43 unused imports, 1 undefined name
```

No source files were modified during this review; the only addition is this document.

---

## Appendix A — How the Antigravity and Claude Cowork features are actually integrated

Scope of this appendix: every feature the README/docs attribute to **Google Antigravity** or **Claude Cowork (Operon)**, traced to the code that implements it and to whether the live agent loop (`AgentCoordinator.execute_turn` + `HarnessDaemon`) actually uses it. Status key: **Wired** = used in the live loop · **Library-only** = implemented and tested but nothing in the runtime calls it · **Cosmetic** = UI/config surface with no engine effect · **Simulated** = returns canned data · **Divergent** = implemented differently from the doc's own spec in a way that matters.

### A.1 Antigravity-attributed features

| Feature (per docs) | Where | Status | What's actually there |
|---|---|---|---|
| Tiered `transcript.jsonl` + `transcript_full.jsonl` with pointer truncation (doc 03) | `core/transcript.py` | **Wired** | Faithful: same line schema (`step_index/source/type/status/created_at/content/thinking/tool_calls`), 1500-char truncation with `truncated_fields`, byte offsets recorded. Lives under `<workspace>/.harness/sessions/<id>/` rather than a `brain/` dir — fine, except that it's inside the user's repo (see §3.2). |
| Trajectory compaction `<CONTEXT_SUMMARY>` (doc 03 §3) | `TranscriptEngine.compact_trajectory` | **Library-only** | Builds the exact markdown structure from the doc, but nothing calls it. The loop just slices `read_compact_history(limit=25)`; there is no token-budget trigger and no summariser call. |
| SQLite FTS5 memory with byte-offset O(1) seek (doc 09) | `memory/fts5_index.py` | **Wired / Divergent** | Indexing and `search(fetch_exact=True)` seek work and are used on every step. But the schema is a simplified one-row-per-step table, not the chunked `user_step_byte_offsets` design the doc extracts; the `conversation_summaries` table drops the `battle_id`, `winning_conversation_id`, `nesting_depth`, `parent_conversation_id` columns that doc 09/11 call out as the Antigravity innovation. The DB is **per-session**, so the "recall across weeks of work" claim is false — `/search` and `search_memory` only see the current session. |
| Reactive wakeup inbox, "0% polling" (doc 02) | `coordinator.inbox`, `_on_background_task_completed`, `wait_for_wakeup` | **Half-wired** | Producer side exists: background `run_command` completion puts a `TASK_COMPLETED` event on the queue. **Consumer side does not exist** — `wait_for_wakeup` is never awaited anywhere, so events accumulate and never resume a planner turn. The model must poll with `manage_task status`, which is the anti-pattern the doc describes. |
| Git worktree subagent sandboxing (doc 05) | `sandboxing/worktree.py` | **Library-only** | `create_worktree/remove_worktree/get_diff` work. Only `AgentArena` calls them, and no tool or RPC lets the model or UI start an arena. `WorktreeManager` is instantiated by the coordinator purely for its `mkdir` side effect. |
| Battle Mode (doc 11) | `orchestration/battle_arena.py` | **Library-only / Simulated** | Parallel worktrees + evaluator + `git merge --no-ff` winner are implemented, but the default `worker_fn` is `asyncio.sleep(0.1)` returning a canned score of 80/90 — no model is ever invoked. Not reachable from the tool schema, RPC, or UI. |
| Artifact metadata contract `UserFacing` / `RequestFeedback` (doc 07) | `artifacts/manager.py`, `write_to_file`, `index.html` | **Wired / Divergent** | `write_to_file` with `ArtifactSummary` writes a manifest entry (into `…/artifacts/artifacts/`, double-nested). The model can't set `UserFacing`/`RequestFeedback` — the tool schema doesn't expose them, so they are always `True/False`. The UI shows the "AWAITING APPROVAL / Proceed" banner for **every** artifact regardless, and "Proceed" doesn't block anything — it just sends the text prompt `Approved artifact X. Proceed…`. The doc's "blocks agent turns until user interaction" semantics are absent. |
| Sandboxed generative-UI webview (doc 07 §B) | `render_sandboxed_html`, sandbox app | **Divergent** | The doc specifies `<iframe sandbox="…">` + strict CSP + `postMessage`-only. Implementation: the 8001 page has no CSP, the 8000 UI embeds it in an iframe with **no `sandbox` attribute**, and — more importantly — the Artifact tab on port 8000 fetches the same artifact and renders it with `marked.parse()` → `innerHTML` in the **trusted** origin. An HTML artifact is therefore executed in the trusted origin, bypassing the sandbox entirely. |
| Tiered tool permissions / human-in-the-loop (doc 12) | `sandboxing/guardrails.py`, `coordinator._dispatch_tool` | **Partially wired / Divergent** | Tier classification exists. The doc's state machine (`PromptUser → Approve/Deny`) does not: `DESTRUCTIVE` is hard-denied, `ELEVATED` (outside-workspace writes, protected-path references) is **silently allowed**, and `edit_file` is never classified. The Settings → Security toggles ("Ask For Approval", "Destructive Command Approval Gate", "Bypass Permissions Gate") are never read by the engine. |
| Progressive skills with YAML front-matter (doc 04) | `skills/registry.py`, `view_skill` tool | **Wired** | Works as described; catalog is Tier 3 of the prompt and `view_skill` loads the full playbook on demand. |
| 5-tier prefix-invariant prompt (doc 10) | `models/prefix_cache.py` | **Wired / Divergent** | Layout matches the doc. Invariance is broken in practice by the 25-record sliding window (prefix shifts every turn after step 25) and no provider adapter sets cache breakpoints (Anthropic `cache_control`). Tool JSON is duplicated into the system prompt even when native tools are sent. |
| Studio UI chrome: activity bar, thinking accordion, tool cards, Proceed banner, diff view | `web/index.html` | **Wired (visual)** | Faithful visual replica. Everything behind it is driven by the same single `/turn/stream` call; "streaming" is post-hoc chunk replay. |

**Net for Antigravity:** the two genuinely integrated pieces are the transcript engine and FTS5 indexing (plus the skills registry). Worktrees, Battle Mode, compaction, and the reactive wakeup consumer are present as code but disconnected from the agent; the approval and artifact-feedback semantics are UI-only.

### A.2 Claude Cowork / Operon-attributed features

| Feature (per docs) | Where | Status | What's actually there |
|---|---|---|---|
| Dual-port origin separation, 8000 trusted / 8001 untrusted (doc 13 §B) | `daemon.py` two `web.Application`s | **Wired / Ineffective** | Both ports start. But the doc's diagram pairs this with *"Port 8000: Authenticated via Single-Use Token"*, and that half was not built: 8000 has no auth, no `Origin`/`Host` check, and accepts `text/plain` JSON. SOP stops an 8001 page from *reading* 8000 responses, but it does not stop write-only `no-cors` POSTs to `/file/save`, `/rpc`, `/settings`. So the precise attack the doc says this design neutralises ("an agent creates an HTML page that … calls internal APIs") still works. The port split without the token is security theatre. |
| Single-use 3-minute login URL (`claude-science url`, doc 13 §A) | `generate_login_url`, `/login`, `harness url` | **Non-functional** | `harness url` instantiates a *new* `HarnessDaemon` to mint the token, so the running daemon never knows it; `/login` sets no cookie/session; no route checks anything. Two red-team tests cover the token's expiry/replay logic in isolation, which is why this looks "verified". |
| CIDR / cloud-metadata shield with interactive **Approval Card** (doc 13 §C) | `sandboxing/tirith_security.py` | **Library-only / Divergent** | Only `169.254.169.254`, `metadata.google.internal`, Alibaba's `100.100.100.200`, and four loopback ports are matched; the RFC 1918 ranges and `127.0.0.0/8` from the doc are absent. There is no Approval Card — the verdict is a hard `BLOCK`, and `audit_command` is never called by the coordinator anyway. Settings `security.tirith_ssrf_shield: true` is decorative. |
| In-flight concurrency gate / classifier prioritisation (`concurrency_gate.py` docstring) | `core/concurrency_gate.py` | **Library-only** | Clean implementation, never `acquire()`d. There is also no "safety classifier" request type in the runtime for it to prioritise. |
| Model auto-fallback (`coworkModelAutoFallback`) | `router._call_openai_compatible` | **Wired / Over-reaching** | Implemented only for OpenRouter, as a hardcoded chain (`deepseek/deepseek-chat → llama-3.3-70b → openai gpt-4o-mini → mock`). It triggers on *any* exception, including auth errors, and the `models.auto_fallback` toggle is never read, so it cannot be turned off. The final hop to the keyword-matching mock is not a fallback Cowork has. |
| Bun-compiled standalone daemon (doc 13 §A) | `harness-engine.spec`, `package_macos.sh`, Swift launcher | **Analogous** | PyInstaller one-file binary + WKWebView shell is a reasonable stand-in. Default workspace falls back to `$HOME` (see §4.5). |
| Zod-discriminated `Resource` model (doc 13 §D) | — | **Absent** | No equivalent (`pydantic` is a dependency but unused anywhere in `harness/`). |
| 7-tab settings replicating `desktop_config.json` (last commit) | `_get_cowork_settings`, `index.html` settings modal | **Cosmetic** | Keys mirror Cowork names (`coworkUserFilesPath`, `trusted_folders`, `allowed_sites`, `bypass_permissions_gate`, `default_permission_mode`, `session_spend_cap_usd` …). Of all of them, only `models.default_provider/default_model` and `api_keys` affect behaviour (via `router.save_config`). `trusted_folders`, `allowed_sites`, permission mode, spend cap, temperature, max_tokens, max_tool_iterations are stored and never read. The Account tab shows a hardcoded fake identity; the Extensions tab shows three MCP servers as "connected" that never start. |
| Remote-control / headless `serve --no-browser --detached` | `harness daemon`, `harness ui --no-open` | **Partial** | Headless start exists; "remote" access is unsafe without the missing auth. |

**Net for Cowork:** the only Cowork pattern that is both implemented and active is the dual-port split, and it does not deliver its security property because the token-auth half of the design was omitted. Everything else is library-only, cosmetic, or non-functional.

### A.3 Where the two integrations conflict

1. **Antigravity studio pane vs Cowork sandbox.** The Artifact tab renders artifacts via `marked.parse` → `innerHTML` on port 8000. That is a direct bypass of the 8001 isolation: whatever the sandbox was meant to contain is executed in the trusted origin anyway.
2. **Antigravity in-repo session storage vs Aider-style auto-commit.** `.harness/` sits inside the workspace; `git add -A` in architect mode commits transcripts and SQLite files (verified in §3.2).
3. **Antigravity "Proceed" semantics vs the actual loop.** The banner implies a gate; the loop has no gate, so users are trained to expect a safety control that does not exist.

### A.4 Minimum to make the borrowed designs actually hold

- Add the token to port 8000 (the missing half of the Cowork design): mint at startup, inject into `index.html`, require on every mutating route, validate `Origin`/`Host`, and set `Content-Security-Policy: frame-ancestors 'none'; default-src 'self'` on 8001 responses plus `sandbox="allow-scripts"` on the embedding iframe. Render artifacts on 8000 only through the 8001 iframe, never via `innerHTML`.
- Give the reactive inbox a consumer: when `run_command` goes to background, suspend the turn and `await wait_for_wakeup()`; append a `REACTIVE_WAKEUP` step and continue the loop.
- Wire `ELEVATED` to a real approval request (there is already an SSE channel and a cancel endpoint; an `approve`/`deny` endpoint plus an `asyncio.Future` per pending tool call is ~60 lines) and make the Settings → Security toggles read from `settings.json`.
- Expose `RequestFeedback`/`UserFacing` in the `write_to_file` schema; show the Proceed banner only when `request_feedback` is true, and have "Proceed" resolve a pending approval rather than send a prompt.
- Either expose arena/worktree/spawn via tools or RPC with a real `worker_fn`, or move them (and the concurrency gate, state graph) to an `experimental/` package and say so in the README.
- Make FTS5 cross-session: one `memory.db` per workspace keyed by `conversation_id` (the schema already has the column), and add the `parent_conversation_id`/`battle_id` columns if the lineage features are kept.
