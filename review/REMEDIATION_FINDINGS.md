# Harness-Zero — Findings Catalogue (per-finding repros, root causes, fix specs, tests)

**Baseline commit:** `a0afc75` on `main` (branch `arena/4bab20a8-harness-zero`)  
**Role of this file:** the detailed, per-finding reference (IDs `HYG-*`, `SEC-*`, `TOOL-*`, `MDL-*`, `LOOP-*`, `ORCH-*`, `UI-*`, `SIM-*`, `DOC-*`, `CONF-*`). It was the v1 work order; **the build sequence now lives in `REMEDIATION_PLAN.md` (v2)**, which is organised around the modular kernel described in `MODULAR_ARCHITECTURE.md`. v2 items cite the IDs below as "absorbs: …" — the repros, root causes, acceptance criteria and tests here remain authoritative; only the *order* and the *destination files* changed. Where this file names a destination (e.g. "`coordinator.py`"), read it through the migration map in `MODULAR_ARCHITECTURE.md` §12.

> Line numbers refer to the baseline commit. If the file has drifted, search for the quoted code instead. §3 (execution order) below is superseded by `REMEDIATION_PLAN.md` §3.

---

## 0. How to use this document

### 0.1 Conventions

| Field | Meaning |
|---|---|
| **ID** | Stable identifier. Use it in branch names, commit messages (`fix(SEC-01): …`) and PR titles. |
| **Severity** | `P0` = blocks a safe prototype; `P1` = blocks a *working* prototype; `P2` = required for the prototype to be honest/maintainable; `P3` = optional / post-prototype. |
| **Size** | `S` < 1 h, `M` 1–4 h, `L` 4–12 h, `XL` > 12 h of focused agent work. |
| **Depends on** | IDs that must be merged first. |
| **Decision** | References §2 (owner decisions). If the owner has not answered, **use the stated default** — do not invent a third option. |

### 0.2 Ground rules for the implementing agent

1. **One ID per PR** (small IDs may be batched if listed together in §3). Every PR must include the tests named in the item and must pass the Verification Gate (§7).
2. **Never fake.** If a subsystem cannot be made real within the item's scope, it is removed from the user-facing surface (tool schema, UI, settings, README), not stubbed with canned output. Simulated behaviour behind a real-looking surface is a regression, not a feature.
3. **Never widen the workspace.** Nothing the agent builds may read or write outside `<workspace>` (plus `<workspace>/.harness/` and the user config dir) unless an item explicitly says so.
4. **Tests must be hermetic.** No network, no writes outside `tmp_path`, no fixed TCP ports, no dependency on the developer's `$HOME` or env vars. HYG-01 establishes the fixtures; use them.
5. **Keep behaviour explicit.** Errors are surfaced, not swallowed. `except Exception: pass` is not allowed in the agent loop, the router, or the daemon.
6. **Do not change what you were not asked to change.** Cosmetic refactors go in their own PR.
7. **Record decisions.** When an item says "Decision Dn", note the default you applied in the PR description.

### 0.3 Environment

```bash
python3 -m venv .venv && . .venv/bin/activate
pip install -e ".[dev]"
# Hermetic test run (HYG-01 makes HOME isolation automatic; until then do it manually):
HOME=$(mktemp -d) pytest -q
# Manual daemon for probing:
harness daemon --workspace /tmp/some-git-repo --port 8765 --sandbox-port 8766
```

Verified baseline: Python 3.11.2, aiohttp 3.14.5, httpx 0.28.1, pytest 9.1.1, pytest-asyncio 1.4.0 — `48 passed in 3.4s`.

---

## 1. Target: what "working end-to-end prototype" means

### 1.1 In scope

A single-user, local-first coding agent that a developer can run against **their own git repository** with a real model provider, through either the web studio (`harness ui`) or the terminal (`harness interactive`), such that:

- the daemon is safe to leave running while browsing the web (authenticated, no CSRF, no traversal, no XSS);
- the agent's tools cannot read or write outside the workspace, and risky actions require explicit approval;
- the agent loop is complete: tool calls round-trip with native provider protocols, background commands resume the turn, long sessions compact instead of silently truncating, cost is tracked;
- edits are surgical, visible in the diff view, and **never auto-committed** without approval;
- sessions persist, can be resumed after restart, and are searchable across sessions;
- everything the UI shows corresponds to a real backend state.

### 1.2 Out of scope for the prototype (quarantine, don't delete)

Browser CDP automation, UI-TARS visual grounding, Battle Arena with real workers, LangGraph-style state graphs, Hermes spawn trees, macOS desktop automation beyond screenshots, native app packaging polish. These move to `harness/experimental/` (SIM-02, ORCH-02, ORCH-04) and out of the UI until they are real.

### 1.3 End-to-end acceptance scenarios (the prototype is "done" when all pass)

| # | Scenario | Steps | Expected |
|---|---|---|---|
| **S1** | Safe daemon | `harness ui -w <repo>`; open the printed URL; `curl -s -o /dev/null -w '%{http_code}' -X POST http://127.0.0.1:8000/rpc -H 'Content-Type: text/plain' -d '{}'`; `curl … -H 'Host: evil.example' /health` | UI loads and works; both curls return `401`/`403`/`415` (not `200`); `/health` without token still `200` from a valid Host. |
| **S2** | Configure & converse | Settings → paste API key → Save; model dropdown shows that provider's models; send "List the Python files under `harness/` and summarise `README.md`" | Key file is `0600`; the turn shows `run_command`/`view_file` tool cards; final answer references real files; Economics panel shows non-zero tokens and cost. |
| **S3** | Surgical edit | "Add a module docstring to `harness/cli.py`" | `edit_file` applied inside workspace; Diff Studio shows exactly that hunk; `git log` unchanged (no commit). |
| **S4** | Approval gate | "Delete the `build/` directory" (or any `rm -r`) | Approval card appears in UI / `[y/N]` prompt in CLI. Deny → model receives "User declined", turn continues. Approve → command runs. |
| **S5** | Jail | Scripted test: `view_file /etc/hostname`, `write_to_file ../x`, `run_command Cwd=/tmp` | All three return `is_error=True` with a "outside workspace" message; nothing written. |
| **S6** | Background wake-up | "Run `sleep 8 && echo FINISHED` and tell me the output" | Tool returns a task id; the turn suspends; on completion the model receives the output *without* calling `manage_task`; final answer contains `FINISHED`. |
| **S7** | Cross-session memory | Session A: discuss "the pelican refactor"; New Session; `search_memory` for "pelican" with scope `all` | Hit from session A returned with step index and snippet. |
| **S8** | Architect loop | Architect mode: choose files + `pytest -q` as test command; request a change | Plan shown; file-scoped diffs applied; tests run; on green an approval "Commit N files?" appears; Approve → commit contains **only** those files; on red → no commit, failures shown and retried up to the limit. |
| **S9** | Resume | Restart daemon; open Sessions; pick previous | History renders; next message continues with prior context. |
| **S10** | Hermetic CI | `HOME=$(mktemp -d) pytest -q && ruff check .` | Passes; the temp HOME contains nothing but what tests created under `tmp_path`; repo tree unchanged (`git status --porcelain` empty). |
| **S11** | Honest surfaces | Open every tab/panel of the studio with no keys configured | No panel claims a connection, account, or MCP server that does not exist; mock mode (if enabled) is labelled as mock. |

---

## 2. Decisions required from the owner (defaults the agent must assume if unanswered)

| ID | Question | Default to apply | Affects |
|---|---|---|---|
| **D1** | What should "mock mode" be? | A `MockProvider` that only returns scripted responses (`set_mock_responses`) for tests, plus a clearly labelled echo responder for `--provider mock` that **never emits tool calls**. Delete the keyword heuristics. | MDL-01, UI-04 |
| **D2** | Simulated subsystems: implement or quarantine? | Implement **MCP over stdio** (cheap, high value). Quarantine browser CDP, UI-TARS, Battle Arena, state graph, spawn ledger under `harness/experimental/` and remove from UI/tool schema. | SIM-01..05, ORCH-02, ORCH-04 |
| **D3** | Where does session data live? | Keep `<workspace>/.harness/` **and** add `/.harness/` to `<workspace>/.git/info/exclude` on start. (Alternative: `~/.harness/workspaces/<sha1(abs_path)>/`.) | LOOP-05, ORCH-01 |
| **D4** | Auth mechanism for the daemon? | Per-launch random bearer token embedded in the served page + required header on all non-public routes + Host/Origin allowlist. No cookies. | SEC-01..03 |
| **D5** | Architect/editor commit policy? | Never auto-commit. On success, request approval; commit only files the loop modified. | ORCH-01 |
| **D6** | Which providers are first-class in the prototype? | OpenAI-compatible (OpenAI, OpenRouter, DeepSeek, local Ollama/vLLM) and Anthropic with native tool protocols. Gemini best-effort with function calling. | MDL-03 |
| **D7** | License? | **No default — owner must choose.** Agent adds a `LICENSE` placeholder issue, not a file. | HYG-06 |
| **D8** | Supported platforms? | CLI/daemon: macOS + Linux. Swift launcher: macOS only. `take_screenshot`: macOS only (hidden elsewhere). | TOOL-08, HYG-03 |
| **D9** | Should `/rpc turn.submit` (non-streaming) support approvals? | No: non-interactive callers get `ELEVATED → denied` with a clear message unless they pass `"auto_approve": true` explicitly. | TOOL-02 |

---

## 3. Execution order

```
Phase 0  HYG-01 → HYG-02 → HYG-03 → HYG-04 → HYG-05 → HYG-06 → HYG-07        (test isolation + hygiene; unblocks everything)
Phase 1  SEC-01 → SEC-02 → SEC-03 → SEC-04 → SEC-05 → SEC-06 → SEC-07 → SEC-08 → SEC-09
Phase 2  TOOL-06 → TOOL-01 → TOOL-02 → TOOL-03 → TOOL-04 → TOOL-05 → TOOL-07 → TOOL-08
Phase 3  MDL-01 → MDL-02 → MDL-03 → MDL-04 → MDL-05 → MDL-06 → MDL-07 → MDL-08
Phase 4  LOOP-05 → LOOP-01 → LOOP-02 → LOOP-03 → LOOP-04 → LOOP-06 → LOOP-07 → LOOP-08
Phase 5  ORCH-01 → ORCH-03 → ORCH-02 → ORCH-04
Phase 6  UI-01 → UI-02 → UI-03 → UI-04 → UI-06 → UI-07 → UI-08 → UI-09 → UI-05
Phase 7  SIM-04 → SIM-05 → SIM-01 → SIM-02 → SIM-03
Phase 8  DOC-01 → DOC-02 → DOC-03
```

Suggested PR batching: `HYG-01+02`, `HYG-03+04+05`, `SEC-01+02+03` (one middleware PR), `SEC-04+05+UI-02`, `TOOL-06+01`, `TOOL-02+UI-03`, `MDL-01+02`, `MDL-03` alone, `LOOP-01` alone, `ORCH-01` alone.

---

## 4. Task catalogue

Each item: **Problem → Repro → Root cause → Fix spec → Acceptance → Tests**.

---

### Phase 0 — Hygiene & test isolation

#### HYG-01 · P0 · S · Test suite writes to the developer's `$HOME`
- **Files:** `harness/models/router.py:98-126` (`_config_path`, `_load_config`, `save_config`), `tests/test_ui_routes.py` (POST `/settings`), `tests/test_coordinator.py:13,58` (`ModelRouter()` with env-dependent defaults).
- **Repro:** `HOME=$(mktemp -d) pytest -q; cat $HOME/.harness/config.json` → `{"default_provider": "mock", "default_model": "mock-model"}`. On a real machine this **overwrites the user's configured provider**.
- **Root cause:** `_handle_settings_post` always calls `router.save_config(...)`; `_config_path()` is hardwired to `~/.harness` and `mkdir`s on read.
- **Fix spec:**
  1. `ModelRouter.__init__(..., config_dir: Optional[Path] = None)`; resolve as `config_dir or Path(os.environ.get("HARNESS_CONFIG_DIR", Path.home() / ".harness"))`. Store as `self.config_dir`; `_config_path` becomes an instance method; no `mkdir` on read.
  2. `tests/conftest.py` with an **autouse** fixture: `monkeypatch.setenv("HOME", str(tmp_path/"home"))`, `monkeypatch.setenv("HARNESS_CONFIG_DIR", str(tmp_path/"cfg"))`, and `monkeypatch.delenv` for `OPENAI_API_KEY ANTHROPIC_API_KEY GEMINI_API_KEY DEEPSEEK_API_KEY OPENROUTER_API_KEY`.
  3. `_handle_settings_post` only calls `save_config` when `api_keys`, `default_provider`, or `default_model` were **present in the request body**.
- **Acceptance:** After `pytest`, a fresh `$HOME` contains no `.harness`; `test_coordinator` passes with `OPENROUTER_API_KEY=xyz` exported (proves env independence).
- **Tests:** `tests/test_hermetic.py::test_no_home_writes` (assert `not (Path.home()/".harness").exists()` after instantiating router+coordinator and posting settings without keys).

#### HYG-02 · P2 · S · Fixed TCP ports in tests
- **Files:** `tests/test_daemon.py:22-33` (8888/8889), `tests/test_ui_routes.py:19-23` (8990/8991), `tests/test_red_team.py` (dummy requests).
- **Fix spec:** Use `aiohttp.test_utils.TestServer`/`TestClient` (`async with TestClient(TestServer(daemon.app)) as client:`) or bind `port=0` and read `site._server.sockets[0].getsockname()[1]`. Provide a `daemon_client` fixture in `conftest.py` returning `(daemon, api_client, sandbox_client)`. Remove unused `AioHTTPTestCase, unittest_run_loop` imports.
- **Acceptance:** Two `pytest -n 2` workers (or two concurrent runs) do not collide.

#### HYG-03 · P1 · S · Hardcoded author environment
- **Locations:** `harness/models/router.py:370` (`/Users/brightech/...architecture_plan.md`), `launcher/HarnessZeroApp.swift:147-148` (`~/.gemini/antigravity/...` else `$HOME`), `harness/web/index.html:1476,2907,2966` (`/Users/brightech/Claude`), `tests/test_ui_routes.py:158`, `harness/core/daemon.py` `trusted_folders` (`~/Claude`), `harness/sandboxing/ripgrep.py:24` (`/Applications/ChatGPT.app/.../rg`), `docs/*.md` (52 `file:///Users/brightech/...` links).
- **Fix spec:** Remove each. Launcher: default workspace = last-used from `~/Library/Application Support/Harness-Zero/state.json`, else present `NSOpenPanel` (choose directory) on first launch; **never** fall back to `$HOME`. Ripgrep: `shutil.which("rg")` only, else Python fallback. Docs: convert to relative links.
- **Acceptance:** `grep -rn "brightech\|\.gemini/antigravity\|ChatGPT.app" --include=* . | grep -v "^./review/"` → empty.

#### HYG-04 · P1 · S · `.gitignore` hides source and the mock writes into the package
- **Files:** `.gitignore:13` (`lib/`), `.gitignore:50` (`harness/artifacts/`).
- **Root cause:** The mock's "plan" branch wrote `architecture_plan.md` into `harness/artifacts/` in the author's checkout; the directory was ignored to hide it, which also ignores new source files there (`manager.py` is only tracked because it predates the rule).
- **Fix spec:** Delete both lines. Keep `.harness/`. Add `*.db-wal`, `*.db-shm`, `.ruff_cache/`. (MDL-01 removes the write.)
- **Acceptance:** `git check-ignore harness/artifacts/newfile.py lib/x.py` → no output.

#### HYG-05 · P2 · S · Build artifacts at repo root
- **Files:** `AppIcon.icns` (1.8 MB), `harness-engine.spec`, `scripts/package_macos.sh` (assumes `.venv/bin/pyinstaller`).
- **Fix spec:** Move both into `packaging/macos/`; update script paths; add `[project.optional-dependencies] package = ["pyinstaller>=6"]`; script checks `command -v pyinstaller`. Consider `git lfs` or generating the icon in CI instead of committing it (owner call; default: move only).

#### HYG-06 · P2 · M · Lint, CI, license
- **Findings:** `pyflakes`: 43 unused imports; `harness/core/concurrency_gate.py:140` uses `Any` without importing it; `harness/skills/registry.py:23` `Dict[str, any]`; 4 f-strings without placeholders in `scripts/comprehensive_features_verification.py`; `harness/orchestration/architect_editor.py:196` unused `res`.
- **Fix spec:** Add `[tool.ruff]` (`select = ["E","F","I","B","UP"]`, `line-length = 120`) and fix everything it reports. Add `.github/workflows/ci.yml`: matrix `python 3.11/3.12`, `HOME=$(mktemp -d) pytest -q`, `ruff check .`, and a step that fails if `git status --porcelain` is non-empty after tests. **License (D7):** open an issue; do not add a file without the owner's choice.
- **Acceptance:** `ruff check .` clean; CI green.

#### HYG-07 · P2 · S · `TurnResult(str)` subclass hack
- **File:** `harness/core/coordinator.py:39-61`.
- **Problem:** `TurnResult` subclasses `str` so the CLI can pass it to `Markdown(...)`; attribute-carrying strings break `json.dumps`, equality and typing.
- **Fix spec:** Make `TurnResult` a `@dataclass` with `output, thinking, tool_calls, session_id, usage, error`. Update `cli.py` (`Markdown(result.output)`) and daemon (`res.to_dict()`); drop the `hasattr(res, "to_dict")` guards.

---

### Phase 1 — Daemon security (`harness/core/daemon.py`)

#### SEC-01 · P0 · M · No authentication on any route
- **Files:** `daemon.py:48-59` (`generate_login_url`), `:305-321` (`_handle_login`), `:61-95` (routes), `harness/cli.py:224-236` (`harness url`).
- **Repro:** Any `curl` to `/rpc`, `/file/save`, `/settings`, `/turn/stream` succeeds. `harness url` mints a token in a daemon instance that is never started.
- **Fix spec (Decision D4):**
  1. `HarnessDaemon.__init__(..., auth_token: Optional[str] = None)` → `self.auth_token = auth_token or os.environ.get("HARNESS_AUTH_TOKEN") or secrets.token_urlsafe(32)`.
  2. Build `self.app = web.Application(middlewares=[self._host_guard, self._auth_guard], client_max_size=25 * 1024**2)`.
  3. `_auth_guard`: public = `{("GET","/"), ("GET","/ui"), ("GET","/health")}` plus any path starting with `/assets/`. Everything else requires header `X-Harness-Token: <token>` (constant-time compare via `hmac.compare_digest`). Missing/invalid → `401 {"error":"unauthorized"}`.
  4. `_handle_index` reads `index.html` and replaces the literal `__HARNESS_TOKEN__` with the token (add `<meta name="harness-token" content="__HARNESS_TOKEN__">` to the HTML). Because the page has no CORS headers, cross-origin pages cannot read it; SEC-03 closes the DNS-rebinding hole.
  5. Startup log prints `http://{host}:{port}/` (the page carries the token; no `?token=` needed). Write `{"port", "sandbox_port", "token", "pid"}` to `<workspace>/.harness/daemon.json` with mode `0600` so `harness url` can print a working link (and remove the token on shutdown).
  6. Delete `generate_login_url`/`_handle_login`/`_login_tokens` **or** re-implement `harness url` to read `daemon.json`. Default: re-implement (keeps the Cowork pattern honest).
- **Acceptance:** S1. Unauthenticated `POST /rpc` → 401. Authenticated → 200. UI works end to end with the embedded token.
- **Tests:** `tests/test_auth.py`: public routes 200 without token; every other registered route 401 without token and not-401 with token (iterate `daemon.app.router.routes()`); `daemon.json` has mode `0600`.

#### SEC-02 · P0 · S · CSRF via `text/plain` simple requests
- **Files:** every `await request.json()` in `daemon.py` (`:155, 273, 377, 440, 608, 719, 799`).
- **Repro:** `curl -X POST …/file/save -H 'Content-Type: text/plain' -H 'Origin: https://evil.example' -d '{"path":"pwned.txt","content":"x"}'` → `200`, file written (verified).
- **Fix spec:** In `_auth_guard` (or a third middleware): for methods other than `GET/HEAD/OPTIONS`, require `Content-Type` to start with `application/json` (or `multipart/form-data` only for `/media/upload`); otherwise `415`. If an `Origin` header is present it must be in `self.allowed_origins = {f"http://127.0.0.1:{port}", f"http://localhost:{port}"}` (+ the sandbox origins for nothing — the sandbox never calls the API), else `403`. If `Sec-Fetch-Site` is present and equals `cross-site`, `403`. The custom token header (SEC-01) already forces a preflight that will fail; this is defence in depth.
- **Tests:** `text/plain` POST with valid token → 415; JSON POST with `Origin: https://evil.example` → 403.

#### SEC-03 · P0 · S · No Host validation (DNS rebinding)
- **Repro:** `curl -H 'Host: evil.example' http://127.0.0.1:8765/health` → 200 (verified).
- **Fix spec:** `_host_guard` middleware on **both** apps: parse `request.host`; allow hostnames `127.0.0.1`, `localhost`, `::1`, `[::1]`, and `self.host` when it is a concrete address; port must equal the app's port when present. Else `421 Misdirected Request`. Add `--allow-host` CLI option (repeatable) for SSH-tunnel use.
- **Tests:** bad Host → 421 on API and sandbox apps; `localhost:<port>` → 200.

#### SEC-04 · P0 · M · XSS in the trusted origin; remote CDN/fonts in a "local-first" app
- **Files:** `harness/web/index.html:12` (`marked` from jsdelivr, no SRI), `:7-9` (Google Fonts), `:1964, 2121, 2159, 2784` (`innerHTML = marked.parse(...)`), `daemon.py:124-128` (`_handle_index`, no CSP).
- **Fix spec:**
  1. Vendor `marked` and `DOMPurify` into `harness/web/assets/` (pin versions in `harness/web/assets/VERSIONS.md`; fetch from `registry.npmjs.org` tarballs). Remove the jsdelivr and Google Fonts tags; use a system font stack.
  2. Add `function renderMarkdown(md) { return DOMPurify.sanitize(marked.parse(md ?? ""), { USE_PROFILES: {html: true}, FORBID_TAGS: ["style","iframe","object","embed","form","input","button","svg","math"], FORBID_ATTR: ["style"] , ALLOW_UNKNOWN_PROTOCOLS: false }); }` and replace all four sinks. `escapeHtml` stays for plain text.
  3. `_handle_index` sets `Content-Security-Policy: default-src 'self'; script-src 'self' 'unsafe-inline'; style-src 'self' 'unsafe-inline'; img-src 'self' data: blob:; connect-src 'self'; frame-src http://127.0.0.1:{sandbox_port} http://localhost:{sandbox_port}; frame-ancestors 'none'; base-uri 'none'; form-action 'none'`, plus `X-Content-Type-Options: nosniff`, `Referrer-Policy: no-referrer`. (Stretch: move the inline `<script>` to `assets/app.js` and drop `'unsafe-inline'` for `script-src`.)
- **Acceptance:** A scripted model response `<img src=x onerror="document.title='pwned'">` renders inert; the UI works fully offline (block network, reload).
- **Tests:** JS unit test is optional; add a Python test asserting the CSP header on `/` and that `index.html` contains no `https://` script/link tags.

#### SEC-05 · P0 · M · The port-8001 sandbox is bypassed by the port-8000 artifact pane
- **Files:** `index.html:2142-2164` (`fetchLatestArtifacts` renders any artifact via `innerHTML`), `:2339` (iframe to hardcoded `http://127.0.0.1:8001/`, no `sandbox` attr), `daemon.py:576-589` (`_handle_sandbox_artifact`, no CSP), `harness/artifacts/manager.py:69` (`render_sandboxed_html`).
- **Fix spec:**
  1. Artifact tab: `.md` → `renderMarkdown`; `.html` → `<iframe sandbox="allow-scripts" src="{sandboxOrigin}/artifacts/{name}">`; anything else → `<pre>` + `escapeHtml`. `sandboxOrigin` comes from `/health` (`sandbox_port`), never hardcoded.
  2. Every response from `sandbox_app` sets `Content-Security-Policy: default-src 'none'; script-src 'unsafe-inline'; style-src 'unsafe-inline'; img-src data: blob:; connect-src 'none'; frame-ancestors http://127.0.0.1:{port} http://localhost:{port}` and `X-Content-Type-Options: nosniff`. (`connect-src 'none'` is what actually stops an injected page from calling the API.)
  3. `render_sandboxed_html` escapes `title`; the `postMessage` bridge uses `targetOrigin = <api origin>` instead of `'*'`.
- **Acceptance:** An artifact `evil.html` containing `fetch('http://127.0.0.1:8000/file/save',{method:'POST',mode:'no-cors',body:'…'})` produces a CSP violation and no file write (with SEC-01/02 it would also get 401/415).
- **Tests:** sandbox responses carry the CSP header; API-side `GET /artifacts/x.html` returns `text/plain` (never `text/html`).

#### SEC-06 · P0 · S · Path traversal via `session_id` and filename parameters
- **Files:** `coordinator.py:196-212` (`switch_session`), `daemon.py:776-789` (`_handle_session_get`), `:796-808` (`_handle_session_switch`), `artifacts/manager.py:31-67` (`write_artifact`/`get_artifact`), `daemon.py:739` (`_handle_media_serve`).
- **Repro:** `POST /sessions/switch {"session_id":"../../../../../tmp/traversal_target"}` → `/tmp/traversal_target` created; `/health` then reports the traversal string as the active session (verified).
- **Fix spec:**
  1. `SESSION_ID_RE = re.compile(r"^session_[0-9a-f]{12}$")` (or `^[A-Za-z0-9_-]{1,64}$`); validate in `switch_session`, `new_session`, both handlers → `400`.
  2. `switch_session` becomes atomic: build `transcripts, memory, artifacts, desktop, spawn_ledger` into locals, then assign; on any exception leave state untouched; close the previous `memory.conn` / `spawn_ledger.conn`.
  3. `ArtifactManager._safe(filename)`: `Path(filename).name == filename and filename not in {".", ".."}` and resolved path under `artifacts_dir`; raise `ValueError` otherwise; both handlers map to `400`. Apply the same check in `_handle_media_serve` and `_handle_media_upload` (already uses `.name`; add the equality check).
- **Tests:** traversal `session_id` → 400 and no directory created; `..%2F` artifact → 400/404; coordinator state unchanged after a failing switch.

#### SEC-07 · P0 · S · Secrets at rest and in flight
- **Files:** `router.py:22-46` (`_load_hermes_env`), `:126` (`write_text` with default perms), `executor.py:58` (`env = dict(os.environ)`).
- **Fix spec:** (a) Write `config.json` via `os.open(path, os.O_WRONLY|os.O_CREAT|os.O_TRUNC, 0o600)`; `chmod 0o700` on the directory. (b) Remove `_load_hermes_env` (or gate behind `HARNESS_IMPORT_HERMES_KEYS=1` and log that it happened). (c) `GET /settings` must never include key values — add a test that asserts no configured key substring appears in the response body. (d) Subprocess env scrub is TOOL-05.
- **Tests:** file mode `0o600`; settings response does not leak a key set to a sentinel value.

#### SEC-08 · P1 · S · Unbounded uploads / missing limits
- **Files:** `daemon.py:684-737`.
- **Fix spec:** `client_max_size=25 MiB` on the app (SEC-01 step 2); in the multipart loop count bytes and abort with `413` past the limit; restrict extensions to an allowlist (`png jpg jpeg gif webp pdf txt md json`); store under `session_dir/media` only.

#### SEC-09 · P1 · S · Fabricated state in `/settings`
- **Files:** `daemon.py:188-194` (fake `account`), `:222-226` (MCP `"status": "connected"`).
- **Fix spec:** Delete the `account` section (and the UI tab, UI-06). MCP status must come from `MCPBridge.status()` (SIM-01); until then report `"status": "not_configured"`.

---

### Phase 2 — Tool sandboxing & approvals

#### TOOL-06 · P1 · M · Settings are stored but never read by the engine
- **Files:** `daemon.py:170-265` (`_get_cowork_settings` defaults), `coordinator.py` (no reference to settings), `router.py` (hardcoded `temperature=0.2`, `max_tokens=4096`).
- **Fix spec:** New `harness/core/settings.py`:
  ```python
  @dataclass
  class SecuritySettings: permission_mode: Literal["allow","ask","plan"] = "ask"; destructive_command_blocking: bool = True; bypass_permissions_gate: bool = False; session_spend_cap_usd: float = 5.0; approval_timeout_s: int = 300
  @dataclass
  class ModelSettings: default_provider: str = ""; default_model: str = ""; temperature: float = 0.2; max_tokens: int = 4096; reasoning_effort: str = "medium"; max_tool_iterations: int = 10; auto_fallback: bool = False; fallback_chain: list[str] = field(default_factory=list); request_timeout_s: int = 180; context_budget_tokens: int = 60000; local_base_url: str = "http://localhost:11434/v1"
  @dataclass
  class Settings: general: ...; security: SecuritySettings; models: ModelSettings; extensions: ExtensionSettings; capabilities: ...
  class SettingsStore:  # <workspace>/.harness/settings.json, atomic write, validation, change callbacks
  ```
  Coordinator, router and daemon take the store (or `Settings`) by injection; `_get_cowork_settings` is replaced by `store.load().to_dict()` + `configured_providers`. Remove dead keys (`ble_maker_enabled`, `launch_at_login`, `keep_in_background`, `locale`, `account.*`) or move them under `"ui": {...}` with a comment that they are UI-only.
- **Acceptance:** Changing `max_tool_iterations` in the UI changes the loop limit on the next turn (test via scripted mock with 3 tool calls and limit 2).

#### TOOL-01 · P0 · M · Tools are not confined to the workspace
- **Files:** `coordinator.py:504-525` (`view_file`), `:526-546` (`write_to_file`), `:547-554` (`edit_file`, no checks), `:555-569` (`run_command` `Cwd`), `executor.py:64` (`cwd.mkdir`), `sandboxing/diff_applier.py`, `orchestration/architect_editor.py`.
- **Repro:** `view_file /tmp/secret_outside.txt` → content returned; `write_to_file /tmp/outside_write.txt` → written; `run_command Cwd=/tmp/brand_new_dir` → directory created and command executed (all verified).
- **Fix spec:** New `harness/sandboxing/jail.py`:
  ```python
  class PathOutsideWorkspace(PermissionError): ...
  class WorkspaceJail:
      def __init__(self, root: Path): self.root = root.resolve()
      def resolve(self, raw: str | os.PathLike, *, for_write: bool = False) -> Path:
          p = Path(raw).expanduser()
          p = p if p.is_absolute() else self.root / p
          rp = p.resolve(strict=False)                       # follows symlinks in the existing prefix
          if rp != self.root and self.root not in rp.parents: raise PathOutsideWorkspace(str(rp))
          if for_write and self._is_git_internal(rp):         # <root>/.git/** except info/exclude
              raise PathOutsideWorkspace(f"{rp} is inside .git")
          return rp
  ```
  Use it in all four tools (and `edit_file` must also go through `SecurityPolicy.evaluate_file_write_risk`), in `DiffApplier.apply_diff` (accept a jail or pre-resolved path), in `ArchitectEditorLoop` (resolve `target_files`), in `_handle_file_content/_handle_file_save` (replace the inline checks). `CommandExecutor.run` must **not** `mkdir` the cwd; it must exist and be jailed by the caller. `PROTECTED_SYSTEM_PATHS` stays as a second layer.
- **Acceptance:** S5.
- **Tests:** symlink inside workspace pointing to `/etc` → denied; `..` escapes → denied; path under `.git/hooks` write → denied; `.git/info/exclude` write → allowed.

#### TOOL-02 · P0 · L · No approval flow; `ELEVATED` is silently allowed
- **Files:** `coordinator.py:531, 560` (only `DESTRUCTIVE` is denied), `guardrails.py:69,108` (`requires_approval=True` ignored), `index.html:1664-1670, 1689-1695` (decorative toggles), `daemon.py:437-522` (`_handle_turn_stream`, no event sink), `coordinator.py:136` (`inbox`).
- **Fix spec (Decisions D9, defaults from TOOL-06):**
  1. `harness/core/approvals.py`:
     ```python
     @dataclass
     class ApprovalRequest: id: str; kind: str; summary: str; details: dict; created_at: float; future: asyncio.Future
     class ApprovalBroker:
         def __init__(self, emit: Callable[[dict], Awaitable[None]] | None, timeout_s: float): ...
         async def ask(self, kind: str, summary: str, details: dict) -> tuple[bool, str]:  # emits "approval_required", awaits resolve or timeout → (False, "timed out")
         def resolve(self, req_id: str, approved: bool, reason: str = "") -> bool
         def pending(self) -> list[dict]
     ```
  2. Coordinator gets `self.approvals = ApprovalBroker(emit=self._emit_event, timeout_s=settings.security.approval_timeout_s)` and an `on_event` sink set per turn (`execute_turn(..., on_event=None)`; the daemon passes `send_sse`, the CLI passes a function that prompts `[y/N]`). Policy in `_dispatch_tool`:
     - `permission_mode == "plan"` → only `view_file`, `search_codebase`, `search_memory`, `view_skill` allowed; others return "plan mode: read-only".
     - `SAFE` → execute.
     - `ELEVATED` → if `bypass_permissions_gate` → execute, else `ask()`.
     - `DESTRUCTIVE` → if `destructive_command_blocking` → deny; else `ask()`.
     - Denied → `ToolResult(output="User declined: <reason>", is_error=True)`.
     - No `on_event` sink (non-interactive `/rpc turn.submit`) → `ask()` returns `(False, "non-interactive session")` unless `auto_approve=True` was passed (D9).
  3. Daemon: `POST /approvals/{id}` `{"approved": bool, "reason": str}` → `broker.resolve`; `GET /approvals` → pending. SSE event shape: `{"type":"approval_required","id","kind","summary","details":{tool, arguments, risk_level, reason}}` and `{"type":"approval_resolved","id","approved"}`.
  4. CLI interactive mode prompts inline.
- **Acceptance:** S4.
- **Tests:** scripted turn with an `rm -r` → broker emits request → resolving `False` yields the declined tool result and the loop continues; timeout path; plan-mode path; `bypass_permissions_gate` path.

#### TOOL-03 · P1 · S · `manage_task send_input` returns "Unknown tool"
- **File:** `coordinator.py:606-620` (missing `send_input`/`else` branches fall through to `:638`).
- **Fix spec:** Add `elif action == "send_input": ok = await self.executor.send_input(task_id, args.get("Input",""))`, plus `else: return ToolResult(..., f"Unknown action {action}", is_error=True)`. Add `"wait"` action that awaits completion up to `TimeoutMs` (pairs with LOOP-01).
- **Tests:** each action path.

#### TOOL-04 · P1 · M · Guardrail classifier gaps
- **File:** `guardrails.py:28-52, 55-78`.
- **Repro:** `R=rm; $R -rf /` → SAFE (verified). Also uncovered: `git push -f`, `git checkout -- .`, `git restore .`, `git stash drop/clear`, `git branch -D`, `find … -delete`, `truncate -s0`, `shred`, `chmod/chown -R`, `curl|wget … | sh|bash`, fork bombs, `> important_file` truncation of tracked files.
- **Fix spec:** Add the patterns above; classify any command containing shell variable expansion or `eval`/`exec`/`bash -c`/`sh -c`/`python -c` with file-mutating verbs as `ELEVATED`; make the protected-path check token-based (`shlex.split` with fallback) and treat a token that *starts with* a protected path as a match; keep the docstring honest: "best-effort classifier; containment is provided by `WorkspaceJail` and approvals".
- **Tests:** table-driven: 30 destructive, 20 benign (`rm -rf node_modules/.cache` is still DESTRUCTIVE → approval, by design).

#### TOOL-05 · P0 · M · Executor hardening
- **Files:** `executor.py:58` (full env), `:72` (`stdin=PIPE` never closed), `:124` (`readline` 64 KiB limit), `:141` vs `:198` (status race), `:64` (`mkdir`), `coordinator.py:555-569` (Tirith never called).
- **Fix spec:**
  1. `_scrubbed_env()`: copy `os.environ`, drop keys matching `r"(API_KEY|_TOKEN|SECRET|PASSWORD|CREDENTIAL|PRIVATE_KEY)"` (case-insensitive) and `GITHUB_TOKEN`, `GH_TOKEN`, `AWS_*`; set `PAGER=cat GIT_PAGER=cat CI=1 HARNESS_AGENT=1 TERM=dumb`.
  2. `stdin=asyncio.subprocess.DEVNULL` by default; `interactive=True` opt-in keeps the pipe (used by `send_input`).
  3. `create_subprocess_shell(..., limit=4 * 1024 * 1024)` and read with `await stream.read(65536)` chunks (no line limit); keep the 10 MiB cap with a truncation marker.
  4. `kill_task` sets `task.killed = True`; `_monitor_background_task` keeps `status == "killed"` when set.
  5. Remove `cwd.mkdir`; raise if missing.
  6. Before exec: `audit = TirithSecurityCapsule.audit_command(cmd, cwd)`; `BLOCK` → deny with reasons; `WARN` → treat as `ELEVATED` (approval). After exec: `TirithSecurityCapsule.sanitize_output` on stdout/stderr before building the `ToolResult`.
  7. Prune `self.tasks` to the last 200 finished entries.
- **Tests:** env scrub (set `FOO_API_KEY`, run `env`, assert absent); `cat` without args returns immediately (DEVNULL); 200 KB single-line output survives; kill status stable; `audit_command` wired (command with a `ghp_…` token is denied).

#### TOOL-07 · P1 · S · `write_to_file` artifact semantics and nested dir
- **Files:** `coordinator.py:526-546`, `artifacts/manager.py:25-29` (`brain_dir/"artifacts"` under `session_dir/"artifacts"` → `…/artifacts/artifacts/`).
- **Fix spec:** `ArtifactManager(brain_dir=session_dir)`; tool schema gains `UserFacing: bool=true`, `RequestFeedback: bool=false`; manifest records them; the UI (UI-03) shows the Proceed banner only when `request_feedback` is true. `edit_file` returns the list of changed files in `ToolResult.metadata["changed_files"]` (used by ORCH-01).

#### TOOL-08 · P2 · S · Platform-specific tools
- **Files:** `coordinator.py:621-624`, `desktop/macos_bridge.py`.
- **Fix spec (D8):** Register `take_screenshot` only when `sys.platform == "darwin"` and `shutil.which("screencapture")`; otherwise it is absent from `get_tool_schemas()`. Remove `predict_visual_click` (SIM-03).

---

### Phase 3 — Model router (`harness/models/router.py`)

#### MDL-01 · P0 · M · Silent fallback to a keyword-matching engine that executes tools
- **Files:** `router.py:145-166` (auto-map + missing-key → mock), `:189-400` (`_mock_agent_turn`), `:195` (follow-up detection compares to a literal the prompt builder never produces), `:370` (hardcoded path write), `:561-564` (final fallback to mock on any error), `coordinator.py:405-420` (`--provider mock` ignored when any key is discoverable), `cli.py:38,65`.
- **Repro:** No key + "check git status" → runs `git status -s`, then renders `### Architectural Assessment: <USER_REQUEST>…` (verified). Any 401/400/timeout from a provider → canned answer with tool calls.
- **Fix spec (Decision D1):**
  1. Move mock into `harness/models/mock_provider.py`: `MockProvider.generate()` returns the next scripted `ModelResponse` or, if none, `ModelResponse(content="[mock] " + last_user_text)` with **no tool calls**.
  2. `ModelRouter.generate()`: resolve provider as `explicit provider` → `settings.models.default_provider` → error `NoProviderConfigured`. `"mock"` is only used when explicitly requested. Model-prefix auto-mapping stays but only when no provider was given.
  3. Delete `_mock_agent_turn` and all keyword branches. Delete the `/Users/brightech` write.
  4. Provider failures raise `ProviderError(provider, model, status_code, message, retryable: bool)`; `execute_turn` catches it, appends a `SYSTEM_NOTIFICATION` step, and returns `TurnResult(error=...)`; daemon sends SSE `error`; CLI prints it.
  5. Missing key → `ProviderError(status=None, message="No API key configured for <provider>")` (not a mock answer).
  6. `cli.py`: `--provider` default `None` (use settings); `--provider mock` always honoured.
- **Acceptance:** With no key: a turn returns an error banner and **no tool executes**. `harness run "x" --provider mock` never calls the network and never runs tools.
- **Tests:** `test_router_no_silent_fallback` (patched httpx 500 → `ProviderError`, no mock content); `test_cli_provider_mock_honoured` with a key in env.

#### MDL-02 · P1 · S · Timeouts and client lifecycle
- **Files:** `router.py:410, 512, 609, 653`.
- **Fix spec:** One lazily-created `httpx.AsyncClient(timeout=httpx.Timeout(connect=10, read=settings.models.request_timeout_s, write=30, pool=10))` per router; `await router.aclose()` on daemon shutdown and CLI exit. Default 180 s everywhere (the 30 s OpenAI-compatible timeout is the likely source of the "query timeout" the last commit worked around).

#### MDL-03 · P1 · L · Native tool-call protocol per provider (Decision D6)
- **Files:** `coordinator.py:388-397` (history flattened to `[Tool Result]: …` user messages), `prefix_cache.py` (tools serialised into the system prompt), `router.py:566-632` (Anthropic: no `tool_result`, no `cache_control`), `:634-663` (Gemini: no tools, key in URL), `:402-456` (local: XML only).
- **Fix spec:**
  1. Transcript: `TOOL_RESULT` records store `metadata={"call_id","tool_name","is_error"}` (extend `TranscriptRecord`/`append_step`).
  2. `PromptCacheBuilder.build(tiers) -> list[Message]` where `Message` is provider-neutral: `{"role":"system"|"user"|"assistant"|"tool", "content": str, "tool_calls": [...]?, "tool_call_id": str?, "name": str?, "images": [...]?}`. Tool JSON is **not** embedded in the system prompt when the adapter supports native tools (flag per provider); it is embedded only for the local XML fallback.
  3. Adapters (`harness/models/adapters/{openai_compat,anthropic,gemini,local}.py`), each `to_request(messages, tools, settings)` / `parse_response(data) -> ModelResponse`:
     - **OpenAI-compatible:** assistant `tool_calls[{id,type:"function",function:{name,arguments:json.dumps(args)}}]`; results as `{"role":"tool","tool_call_id":id,"content":output}`.
     - **Anthropic:** top-level `system` as a content block list with `cache_control:{type:"ephemeral"}` on the last static block (identity+tools+skills); assistant content `[{type:text},{type:tool_use,id,name,input}]`; user content `[{type:tool_result,tool_use_id,content,is_error}]`; `max_tokens` from settings; parse `usage.cache_read_input_tokens`.
     - **Gemini:** `systemInstruction`, `tools:[{functionDeclarations:[...]}]`, `functionCall`/`functionResponse` parts, header `x-goog-api-key` (never query string). Consecutive same-role turns merged.
     - **Local:** send native `tools` first; on HTTP 400 mentioning tools, retry once with the XML `<tool_call>` instructions (existing parser).
  4. Within the current turn, tool results are sent **untruncated** (bounded by `context_budget_tokens`); older turns use the compact (truncated) record.
- **Acceptance:** A two-step tool turn against each adapter's recorded fixture produces a request body with the correct native structure (snapshot tests with JSON fixtures under `tests/fixtures/providers/`).
- **Tests:** per-adapter request/response snapshot tests; `test_prompt_no_tool_duplication`.

#### MDL-04 · P1 · S · Reasoning-model parameters and model-prefix map
- **Fix spec:** Omit `temperature` for `o1*`, `o3*`, `o4*`, `gpt-5*`; map `settings.models.reasoning_effort` to `reasoning_effort` (OpenAI) / `thinking: {type:"enabled", budget_tokens}` (Anthropic, when the model supports it). Prefix map adds `o4`, `gpt-5`, `claude-sonnet-4`, `claude-opus-4`, `gemini-2.5`, `qwen`, `llama` (→ local when no `/`).

#### MDL-05 · P1 · S · Fallback policy
- **Files:** `router.py:502-564`.
- **Problem:** On *any* exception (including 401) the model is swapped to `deepseek/deepseek-chat` → `llama-3.3-70b` → OpenAI `gpt-4o-mini` → mock, changing provider/jurisdiction without consent; the `auto_fallback` toggle is never read.
- **Fix spec:** Fallback only when `settings.models.auto_fallback` is true, only for `retryable` errors (408/409/429/5xx/timeouts), only within the **same provider**, only through `settings.models.fallback_chain` (default empty). Record the substitution in `ModelResponse.notices` (not prepended to `content`); the UI shows it as a badge.

#### MDL-06 · P1 · M · Usage, cost, spend cap
- **Files:** `router.py` (all adapters ignore `usage`), `coordinator.py:107` (`cost_tracker` never updated), `daemon.py:146-150` (`/economics` always zero), `models/cost_tracker.py`.
- **Fix spec:** `ModelResponse.usage = Usage(input_tokens, output_tokens, cache_read_tokens, cache_write_tokens)` parsed per provider (OpenAI `usage.prompt_tokens_details.cached_tokens`; Anthropic `cache_read_input_tokens`/`cache_creation_input_tokens`; Gemini `usageMetadata`). Coordinator calls `cost_tracker.record_turn(model, …)` after every model call; pricing table keyed by provider+model with a `None` → "unknown pricing" state (never silently price as Sonnet). Enforce `session_spend_cap_usd`: when exceeded, stop the turn with `SpendCapExceeded` and emit SSE `error`. Persist the tracker to `session_dir/economics.json` so it survives restarts.

#### MDL-07 · P1 · S · Circuit breaker is dead code
- **Files:** `coordinator.py:96, 408, 430-435` (`endpoint = None`), `models/circuits.py`.
- **Fix spec:** Keep `ProviderEndpoint` state per `(provider, model)` inside `ModelRouter`; `record_failure` on `ProviderError`, `record_success` otherwise; when `OPEN`, raise `ProviderError(retryable=False, message="circuit open for N s")` immediately. Delete `IntelligentModelRouter.classify_intent`/tier tables from the hot path (keep the module as experimental or remove).

#### MDL-08 · P1 · S · Model IDs and the model dropdown
- **Files:** `router.py:78-91` (`claude-3-7-sonnet` without date/`-latest` suffix is likely invalid), `architect_editor.py:46-47`, `index.html:1216-1227` (hardcoded list).
- **Fix spec:** New `GET /models?provider=…` that calls the provider's list endpoint (`/v1/models`, Anthropic `/v1/models`, OpenRouter `/api/v1/models`, Ollama `/api/tags`) with a 24 h on-disk cache, falling back to a small static list per provider. UI populates from it (UI-04). **Verify live** before pinning any default IDs; do not guess.

---

### Phase 4 — Agent loop (`harness/core/coordinator.py`)

#### LOOP-05 · P0 · S · Session data pollutes the user's repo; corrupt lines kill sessions
- **Files:** `coordinator.py:73-79` (session dir inside workspace), `transcript.py:175` (`json.loads` without guard), `coordinator.py:196-212` (connections leaked), `sandboxing/worktree.py:22` (`mkdir` on init).
- **Repro:** `git status` in the workspace shows `?? .harness/`; architect mode committed `memory.db`, `*.db-wal`, transcripts (verified).
- **Fix spec (Decision D3):** On coordinator init, if `<workspace>/.git` exists, ensure `/.harness/` is in `.git/info/exclude` (idempotent). `read_compact_history` skips malformed lines with `logger.warning`. `switch_session` closes old connections (SEC-06). `WorktreeManager` creates its base dir lazily.

#### LOOP-01 · P1 · L · Reactive wake-up has a producer but no consumer
- **Files:** `coordinator.py:643-660` (`_on_background_task_completed`, `wait_for_wakeup` never awaited), `:555-569` (`run_command` returns `task_id`).
- **Fix spec:** In `execute_turn`, after dispatching a batch of tool calls, if any result carries `task_id` and the model's next response has **no** tool calls and does not explicitly say it is done waiting (simplest: always), then `await self.wait_for_wakeup(timeout=settings.models.background_wait_s or 600)`; on event: append `REACTIVE_WAKEUP` step (`source=SYSTEM`, content = exit code + sanitised output, metadata `task_id`), push it into history as a tool-style message ("Background task <id> finished: …"), and continue the loop (counts toward `max_tool_iterations`). On timeout: append a `SYSTEM_NOTIFICATION` "still running" and continue. Emit SSE `task_completed` for the UI regardless. `manage_task wait` (TOOL-03) covers explicit waits.
- **Acceptance:** S6 with a scripted mock: response 1 → `run_command "sleep 1 && echo FINISHED"` (`WaitMsBeforeAsync=100`), response 2 → text; assert the model's third prompt contained `FINISHED` and the loop did not require a `manage_task` call.

#### LOOP-02 · P1 · M · No compaction; sliding window breaks prefix invariance
- **Files:** `coordinator.py:388` (`limit=25`), `transcript.py:180-214` (`compact_trajectory` unused), `prefix_cache.py`.
- **Fix spec:** `estimate_tokens(text) = len(text)//4`. Build history from the last `COMPACTION_SUMMARY` record onward (full history if none). When estimated history tokens > `context_budget_tokens * 0.8`, call the router with a fixed summarisation prompt (same provider/model, no tools) to produce the four sections, then `compact_trajectory(...)` and rebuild. Never drop records otherwise. Document the invariant in `prefix_cache.py`.
- **Tests:** scripted: 60 short steps with a 2 000-token budget → exactly one `COMPACTION_SUMMARY` appended and the next prompt starts with it.

#### LOOP-03 · P2 · M · Uploaded media never reaches the model
- **Files:** `coordinator.py:359-367` (media only logged), `daemon.py:684` (upload).
- **Fix spec:** Resolve `media[].url` (`/media/<name>`) to bytes (≤ 5 MiB, image MIME only), attach as `images` on the user message; adapters map to `image_url` data URLs (OpenAI), `image` base64 blocks (Anthropic), `inline_data` (Gemini). If the provider/model lacks vision, append a text note and continue.

#### LOOP-04 · P1 · S · Cancellation and concurrency
- **Files:** `daemon.py:297-303, 437-522`.
- **Fix spec:** `execute_turn` wraps the loop in `try/except asyncio.CancelledError` → append `SYSTEM_NOTIFICATION "turn cancelled"` then re-raise. Daemon keeps an `asyncio.Lock` per session; a second `/turn/stream` or `turn.submit` while one is running → `409 {"error":"turn in progress"}`.

#### LOOP-06 · P1 · M · Memory is per-session, contrary to the design
- **Files:** `coordinator.py:84-89, 203-209` (`memory.db` under `session_dir`), `memory/fts5_index.py`.
- **Fix spec:** One `FTS5MemoryIndex` at `<workspace>/.harness/memory.db` shared across sessions; `register_conversation` per session; `search(..., conversation_id=None)` default = all; `search_memory` tool gains `Scope: "current"|"all"` (default `all`); `/sessions` list derives titles/step counts from `conversation_summaries` instead of re-reading every transcript. Migration: on first start, index existing per-session DBs' transcripts (or simply re-index from `transcript_full.jsonl` files).
- **Acceptance:** S7.

#### LOOP-07 · P2 · M · Persistent memories and repo map never reach the prompt
- **Files:** `coordinator.py:108` (`semantic_memory` unused), `memory/repo_map.py` (unused).
- **Fix spec:** Tier 3 = skills catalog + `semantic_memory.render_memory_context()` + (if `settings.capabilities.repo_map` and workspace has ≤ 2 000 source files) `RepoMap.generate_map()` cached by max mtime. Add a `remember` tool (`Key`, `Content`, `Category`) so the model can persist conventions. Rename the README claim from "Tree-sitter PageRank" to what it is (AST symbol outline) unless implemented.

#### LOOP-08 · P1 · S · Observability
- **Fix spec:** `logging.basicConfig` in `cli.py` with `--verbose/-v` (DEBUG) and `--log-file`; `logger.exception` in every `except` in the loop/router/daemon; request/response sizes and latency logged at INFO per model call (never bodies or keys).

---

### Phase 5 — Orchestration

#### ORCH-01 · P0 · L · Architect/editor commits the whole dirty tree, even on failure
- **Files:** `architect_editor.py:65` (`auto_commit=True`), `:139-152` (commits when `applied_any` is False), `:193-200` (`git add -A`), `:132-136` (same diff applied to every file), `:46-49` (hardcoded Anthropic models), `daemon.py:477, 646` (`glob("*.py")[:10]` as targets), `index.html:1212` (architect mode sends no files/test command).
- **Repro:** Verified: `success:false` + `commit_sha` set; 19 files committed including `.env` and `.harness/*.db`.
- **Fix spec (Decision D5):**
  1. Diff format with file headers (Aider style); parser returns `[(path, search, replace)]`; apply per file through the jail; per-block failure messages are fed back to the editor model.
     ```
     harness/cli.py
     <<<<<<< SEARCH
     …
     =======
     …
     >>>>>>> REPLACE
     ```
  2. `auto_commit=False` default. On success, if an `on_event`/approval sink exists: `ask("commit", f"Commit {n} files?", {"files": [...], "message": ...})`; on approval `git add -- <files>` then `git commit -m …`. Never `add -A`. Never commit when `success` is False.
  3. Precondition: if `git status --porcelain` shows pre-existing staged changes, refuse to commit (message: "index not clean").
  4. Models/providers come from settings (`models.architect_model`, `models.editor_model`, each optional → default model); no hardcoded IDs.
  5. Targets: `files` from the request (UI-07) or discovered via `search_codebase`; never `glob("*.py")[:10]`. Required: `test_command` optional but if absent the UI must say "no verification will run".
  6. Return `ArchitectEditorResult.changed_files`.
- **Acceptance:** S8; a dirty unrelated file is never staged.
- **Tests:** scripted router: failed diff → no commit; green tests → commit contains exactly the touched files; dirty `.env` untouched.

#### ORCH-03 · P2 · S · Worktree manager robustness
- **Files:** `worktree.py:53` (`base_branch="main"`), `:25-50` (stale branch), sync `subprocess.run` everywhere.
- **Fix spec:** Record the base commit at creation (`git rev-parse HEAD`) and diff against it; before `add -b`, `git worktree prune` and delete a stale branch of the same name; run git via `asyncio.create_subprocess_exec` (or `asyncio.to_thread`) and make the API async.

#### ORCH-02 · P2 · M · Battle arena is simulated and reachable from the UI mode selector
- **Files:** `battle_arena.py:131-143` (sleep + canned score), `:93-97` (merges into the current branch without approval), `:99-102` (winner's worktree never removed), `index.html:1213, 1813` (`battle` mode → plain prompt).
- **Fix spec (Decision D2):** Move to `harness/experimental/battle_arena.py`; remove the simulated default worker (require `worker_fn`); merging requires approval; remove the `battle` option from the UI. If/when implemented: `worker_fn` = child `AgentCoordinator(workspace=worktree_path, session_dir=<session>/battles/<id>/<agent>)` running `execute_turn`.

#### ORCH-04 · P3 · M · Spawn ledger / state graph / concurrency gate
- **Fix spec (D2):** Move `spawn_ledger.py`, `state_graph.py`, `concurrency_gate.py` to `harness/experimental/`; remove `/spawn-tree` and the Spawn Tree panel until a `spawn_subagent` tool exists. If implemented later: child coordinator in the same workspace with `nesting_depth` from the ledger, summarised result returned as the tool output.

---

### Phase 6 — Web UI (`harness/web/index.html`)

#### UI-01 · P0 · S · Authenticated fetch wrapper
- **Fix spec:** `const TOKEN = document.querySelector('meta[name="harness-token"]').content; async function api(url, opts={}) { … headers: {...opts.headers, 'X-Harness-Token': TOKEN, ...(opts.body && !(opts.body instanceof FormData) ? {'Content-Type':'application/json'} : {})} … }` and replace all `fetch(` calls (≈ 25). On `401` show a toast "Session token invalid — reload the page".

#### UI-02 · P0 · S · Sanitised rendering (see SEC-04)
- Replace sinks at `:1964, 2121, 2159, 2784` with `renderMarkdown`; `.html` artifacts only via the sandbox iframe (SEC-05).

#### UI-03 · P0 · M · Approval cards and honest Proceed banner
- **Files:** `index.html:1303-1318, 2142-2175`.
- **Fix spec:** Handle SSE `approval_required` → render a card in the timeline (tool, arguments, risk, reason) with **Approve / Deny** → `POST /approvals/{id}`; disable after resolution; show `approval_resolved`. The Proceed banner appears only when the latest artifact has `request_feedback: true`; **Proceed** resolves the pending artifact approval (if any) instead of sending a prompt; **Request changes** sends the feedback prompt as today.

#### UI-04 · P1 · S · Model selector
- **Files:** `index.html:1216-1227, 1907-1926`.
- **Fix spec:** Populate from `GET /models` (MDL-08) grouped by configured provider; send `{provider, model}` on `/turn/stream` (daemon forwards `provider` — today it drops it, `daemon.py:489`). Remove "Autonomous (Sovereign Engine)"; if D1 keeps a visible mock, label it "Mock (no model, for testing)".

#### UI-06 · P1 · M · Settings modal truthfulness
- **Files:** `index.html:1348-1725, 2868-3160`.
- **Fix spec:** Remove the Account tab; Extensions tab lists MCP servers from `/plugins` with real status (SIM-01) and an "add server" form writing `extensions.mcp_servers`; Security tab controls map 1:1 to `SecuritySettings` (TOOL-06) and nothing else; remove BLE/launch-at-login/locale or move under "UI preferences"; remove hardcoded paths; `cowork_user_files_path` → read-only display of the workspace.

#### UI-07 · P1 · S · Mode selector
- **Fix spec:** Options: `Agent` and `Architect/Editor`. Architect mode opens an inline form: file picker (from `/files`, multi-select) and optional test command; both sent with the request. Remove `Battle Arena Fork` (ORCH-02).

#### UI-08 · P1 · S · Economics / Spawn panels
- **Fix spec:** Economics reads real data (MDL-06) and shows "unknown pricing" when applicable. Spawn Tree panel hidden (ORCH-04).

#### UI-09 · P2 · M · `/events` SSE
- **Files:** `daemon.py:323-342` (writes one event and returns), `index.html` (unused).
- **Fix spec:** Either implement a long-lived SSE channel (heartbeat every 15 s; events: `task_completed`, `approval_required`, `session_changed`) consumed by the UI, or delete the route. Default: implement (it is the natural home for background-task notifications).

#### UI-05 · P2 · L · Real streaming
- **Files:** `daemon.py:489-511` (post-hoc replay with `asyncio.sleep`).
- **Fix spec:** Remove artificial delays now (S). Later: adapters support `stream=True` (OpenAI SSE deltas, Anthropic `content_block_delta`), coordinator yields `text_delta`/`thinking_delta`/`tool_call` events through `on_event` as they arrive.

---

### Phase 7 — Simulated subsystems

#### SIM-04 · P1 · S · Self-certifying verification script
- **File:** `scripts/comprehensive_features_verification.py` (prints ✓ for mocked CDP, MCP, UI-TARS).
- **Fix spec:** Delete it. Real coverage lives in `tests/` and the E2E scenarios (§1.3, scripted in `tests/e2e/`).

#### SIM-05 · P1 · S · Login URL
- Covered by SEC-01 step 5/6. Remove the two red-team tests that validate the dead token store, replace with tests for the new mechanism.

#### SIM-01 · P1 · L · MCP bridge returns fabricated results
- **Files:** `skills/mcp_bridge.py:54-69`, `coordinator.py:112-134` (three servers auto-registered with `npx` commands), `daemon.py:816-823` (`/plugins`), `coordinator.py:626-636` (`call_mcp_tool`).
- **Fix spec (Decision D2):** Implement the MCP **stdio** transport: spawn `command args` with `asyncio.create_subprocess_exec` (env scrubbed per TOOL-05 plus server-specific env from settings), newline-delimited JSON-RPC 2.0: `initialize` (protocolVersion, capabilities, clientInfo) → `notifications/initialized` → `tools/list` → `tools/call`; request ids, 60 s timeouts, restart-on-crash with backoff, `status() -> {name: "connected"|"error"|"stopped", last_error}`. Servers come **only** from `settings.extensions.mcp_servers` (none by default). Discovered tools are merged into `get_tool_schemas()` as `mcp__<server>__<tool>` with the server's `inputSchema`; results' `content[].text` concatenated; `isError` honoured. Remove the generic `call_mcp_tool` tool. `/plugins` reflects real status.
- **Tests:** a tiny Python MCP echo server under `tests/fixtures/mcp_echo_server.py` (speaks the protocol over stdio) used by `test_mcp_stdio_roundtrip`.

#### SIM-02 · P2 · S · Browser CDP engine
- **File:** `browser/stagehand_engine.py` (always mock; no CDP calls exist).
- **Fix spec (D2):** Move to `harness/experimental/`; delete `enable_mock_mode` fallbacks in `connect()` (connect must fail honestly); remove `/browser` slash command and the "CDP Stagehand" references from skills/UI.

#### SIM-03 · P2 · S · UI-TARS stub
- **File:** `desktop/macos_bridge.py:58-72` (`{x:640,y:480,confidence:0.85}`).
- **Fix spec:** Delete; `desktop-automator` skill text updated accordingly.

---

### Phase 8 — Documentation

#### DOC-01 · P1 · M · README/docs honesty
- **Fix spec:** README gets an **Implementation status** table (Implemented / Partial / Experimental / Planned) per feature; fix "15 test suites" (25 files / 48+ tests); document `harness ui`, the token, `HARNESS_CONFIG_DIR`, settings schema, mock mode semantics; replace absolute `file:///Users/brightech` links with relative links (52 occurrences, mostly `docs/00` and `docs/14`); rename "Tree-sitter PageRank" unless implemented; remove claims about MCP/browser/UI-TARS that D2 quarantined.

#### DOC-02 · P1 · S · `SECURITY.md`
- Threat model: local single-user daemon; token + Host/Origin checks; workspace jail; approval policy; env scrubbing; what is **not** provided (no OS-level sandbox, agent commands run as the user).

#### DOC-03 · P2 · S · `CHANGELOG.md` + versioning
- Start at `0.2.0` for the prototype; list IDs closed.

---

## 5. Conflicts and design collisions (why some items must be fixed together)

| ID | Conflict | Resolution |
|---|---|---|
| **CONF-01** | Antigravity-style artifact pane renders artifacts with `innerHTML` on the trusted origin, defeating the Cowork-style 8001 sandbox. | SEC-05 + UI-02: `.html` only through the sandboxed iframe; `.md` sanitised. |
| **CONF-02** | Session data (`.harness/`) inside the workspace vs. architect `git add -A`. | LOOP-05 (`.git/info/exclude`) + ORCH-01 (file-scoped staging). |
| **CONF-03** | "AWAITING APPROVAL / Proceed" banner implies a gate; the loop has none. | TOOL-02 + UI-03 + TOOL-07 (`RequestFeedback`). |
| **CONF-04** | 5-tier prefix cache vs. 25-record sliding window and duplicated tool JSON. | LOOP-02 + MDL-03. |
| **CONF-05** | Dual-port SOP story vs. unauthenticated API accepting simple requests. | SEC-01/02/03 (the token half of the Cowork design). |
| **CONF-06** | "Local-first" vs. CDN script, Google Fonts, and reading `~/.hermes/.env`. | SEC-04 + SEC-07. |
| **CONF-07** | `--provider mock` flag vs. auto-detected keys overriding it; silent mock fallback on provider errors. | MDL-01. |
| **CONF-08** | `.gitignore` ignores a source package (`harness/artifacts/`) because the mock wrote into it. | HYG-04 + MDL-01. |
| **CONF-09** | Per-session `memory.db` vs. "cross-session recall" claim and `database_mcp` pointing at it. | LOOP-06 + SIM-01. |
| **CONF-10** | Launcher default workspace `$HOME` vs. workspace-wide tools (`rglob`, `.harness/`, commits). | HYG-03. |
| **CONF-11** | `ELEVATED` + `requires_approval=True` computed, then ignored; Settings toggles with no backend. | TOOL-02 + TOOL-06. |
| **CONF-12** | Red-team tests validate units (`Tirith`, login tokens) that the runtime never calls, giving false assurance. | TOOL-05 (wire Tirith) + SEC-01 (real auth) + replace those tests with integration tests through `_dispatch_tool`/HTTP. |
| **CONF-13** | Tool schema claims "alphabetically sorted" (`get_tool_schemas` docstring) but appends `call_mcp_tool` last; the builder re-sorts. | SIM-01 removes `call_mcp_tool`; keep a single sort in the builder. |

---

## 6. Known unknowns — verify against live systems before pinning

1. **Model IDs** for defaults (`claude-3-7-sonnet`, `claude-3-5-haiku-latest`, `gemini-1.5-pro`, `o3-mini`, …) — list live via MDL-08; do not hardcode guesses.
2. **Anthropic `cache_control`** block placement limits (max 4 breakpoints) and minimum cacheable prefix; verify `cache_read_input_tokens` appears in `usage`.
3. **Gemini** function-calling shapes for v1beta and role-alternation strictness.
4. **Ollama/vLLM** native `tools` support varies by model; the XML fallback path must be exercised with at least one local model.
5. **MCP** stdio servers require Node (`npx`); the bridge must report "command not found" cleanly.
6. **`rg`** availability; the Python fallback's regex-vs-literal semantics must match `rg` (`case_sensitive=False` currently escapes the pattern — make both paths regex).
7. **aiohttp** `%2F` handling changed across 3.8 → 3.9+; the explicit filename/session validation (SEC-06) must not rely on router behaviour.
8. **Swift launcher** build is untestable in CI without macOS runners; keep it thin and test the engine via CLI.

---

## 7. Verification gate (run before every merge)

```bash
# 1. Hermetic tests + lint
export HOME=$(mktemp -d); export HARNESS_CONFIG_DIR=$HOME/.harness
pytest -q && ruff check . && test -z "$(git status --porcelain)"

# 2. Daemon security probes (after SEC-01..03)
harness daemon -w /tmp/probe-repo --port 8765 --sandbox-port 8766 &
sleep 2
curl -s -o /dev/null -w '%{http_code}\n' -X POST http://127.0.0.1:8765/rpc -H 'Content-Type: text/plain' -d '{}'        # expect 401 or 415
curl -s -o /dev/null -w '%{http_code}\n' -H 'Host: evil.example' http://127.0.0.1:8765/health                               # expect 421
curl -s -o /dev/null -w '%{http_code}\n' -X POST http://127.0.0.1:8765/sessions/switch -H 'Content-Type: application/json' \
     -H "X-Harness-Token: $(python -c 'import json;print(json.load(open("/tmp/probe-repo/.harness/daemon.json"))["token"])')" \
     -d '{"session_id":"../../x"}'                                                                                          # expect 400
curl -sI http://127.0.0.1:8766/ | grep -i content-security-policy                                                           # expect connect-src 'none'

# 3. Tool jail (after TOOL-01/05)
pytest -q tests/test_jail.py tests/test_executor_hardening.py

# 4. E2E scripted scenarios (scripted MockProvider; real-provider variant gated by HARNESS_E2E_PROVIDER)
pytest -q tests/e2e

# 5. Honesty check
grep -rn "brightech\|Sovereign Autonomous Engine\|status\": \"connected\"" harness/ tests/ README.md | grep -v experimental  # expect empty
```

**Definition of done for the prototype:** §1.3 scenarios S1–S11 pass; §7 gate passes; every P0/P1 item above is closed or explicitly deferred by the owner in writing.

---

## Appendix A — Index by file

| File | Items |
|---|---|
| `harness/core/coordinator.py` | HYG-07, TOOL-01, TOOL-02, TOOL-03, TOOL-05, TOOL-07, TOOL-08, MDL-01, MDL-06, MDL-07, LOOP-01, LOOP-02, LOOP-03, LOOP-04, LOOP-05, LOOP-06, LOOP-07, LOOP-08, SEC-06, SIM-01 |
| `harness/core/daemon.py` | SEC-01…SEC-09, TOOL-02, TOOL-06, MDL-08, LOOP-04, UI-09, ORCH-01, SIM-01, HYG-01 |
| `harness/models/router.py` | HYG-01, HYG-03, SEC-07, MDL-01…MDL-08 |
| `harness/models/prefix_cache.py` | MDL-03, LOOP-02 |
| `harness/models/circuits.py`, `cost_tracker.py` | MDL-06, MDL-07 |
| `harness/sandboxing/executor.py` | TOOL-01, TOOL-05 |
| `harness/sandboxing/guardrails.py` | TOOL-04 |
| `harness/sandboxing/tirith_security.py` | TOOL-05 (wire), SIM-05 |
| `harness/sandboxing/diff_applier.py` | TOOL-01, ORCH-01 |
| `harness/sandboxing/worktree.py` | LOOP-05, ORCH-03 |
| `harness/sandboxing/ripgrep.py` | HYG-03, §6.6 |
| `harness/sandboxing/jail.py` (new) | TOOL-01 |
| `harness/core/approvals.py` (new) | TOOL-02 |
| `harness/core/settings.py` (new) | TOOL-06 |
| `harness/core/transcript.py` | MDL-03, LOOP-02, LOOP-05 |
| `harness/memory/fts5_index.py` | LOOP-06 |
| `harness/memory/semantic_memories.py`, `repo_map.py` | LOOP-07 |
| `harness/artifacts/manager.py` | SEC-05, SEC-06, TOOL-07 |
| `harness/skills/mcp_bridge.py` | SIM-01 |
| `harness/skills/registry.py` | HYG-06 |
| `harness/orchestration/architect_editor.py` | TOOL-01, ORCH-01 |
| `harness/orchestration/battle_arena.py`, `spawn_ledger.py`, `state_graph.py`, `harness/core/concurrency_gate.py` | ORCH-02, ORCH-04 |
| `harness/browser/stagehand_engine.py` | SIM-02 |
| `harness/desktop/macos_bridge.py` | TOOL-08, SIM-03 |
| `harness/web/index.html` | SEC-04, SEC-05, UI-01…UI-09, HYG-03 |
| `harness/cli.py` | MDL-01, SEC-01, TOOL-02, LOOP-08 |
| `launcher/HarnessZeroApp.swift`, `scripts/package_macos.sh` | HYG-03, HYG-05 |
| `scripts/comprehensive_features_verification.py` | SIM-04 |
| `tests/` | HYG-01, HYG-02, every item's tests, CONF-12 |
| `.gitignore`, `pyproject.toml`, CI | HYG-04, HYG-05, HYG-06 |
| `README.md`, `docs/`, `SECURITY.md`, `CHANGELOG.md` | DOC-01…DOC-03 |

## Appendix B — Items closed by deletion (no replacement needed)

`_mock_agent_turn` keyword branches (MDL-01) · `_load_hermes_env` (SEC-07) · fake `account` settings (SEC-09) · `predict_visual_click` (SIM-03) · `enable_mock_mode` fallbacks in `connect()` (SIM-02) · simulated arena worker (ORCH-02) · `comprehensive_features_verification.py` (SIM-04) · `call_mcp_tool` generic tool (SIM-01) · artificial `asyncio.sleep` in `/turn/stream` (UI-05) · `endpoint = None` dead branches (MDL-07) · `IntelligentModelRouter.classify_intent` from the hot path (MDL-07) · `cwd.mkdir` in the executor (TOOL-05) · `glob("*.py")[:10]` target selection (ORCH-01).
