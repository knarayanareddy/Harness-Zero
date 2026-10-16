# Changelog

All notable changes to the **Harness-Zero** project are documented in this file.

The format is based on [Keep a Changelog](https://keepachangelog.com/en/1.1.0/),
and this project adheres to [Semantic Versioning](https://semver.org/spec/v2.0.0.html).

---

## [Unreleased]

### Changed — P7 / WS-02 generated catalogues and freshness gates
- New `harness catalog` group: `gen` rewrites `docs/catalog/{PLUGINS,TOOLS,SETTINGS,EVENTS,COMMANDS,UI_CONTRACT}.md`
  and `catalog.json` from a booted kernel; `--check [section]` gates them (`drift`, `settings`, `readmes`, `ui`,
  `scenarios`, `refs`); `readme <plugin>` prints the README Configuration table. `docs/PLUGIN_CATALOG.md` is deleted
  in favour of the generated plugin list.
- Settings ownership moved to the plugins: nine built-in plugins now ship `settings.schema.json` (with
  `x-namespace`, `x-alias-namespaces` for compatibility spellings and `x-secrets` for the secret keys they read), the
  registry registers them at load, and `Commands.settings_schema` is the aggregation of registered schemas.
  `kernel.request_timeout_s` and `kernel.plugins.capabilities` are declared; `kernel.security.jail_enabled` and
  `plugins.openai-compat.default_model` — settings the panel offered with no reader — are gone.
- Fixed: provider `base_url` / mock `script_file` settings could never resolve (the namespaced view prefixed the
  absolute key the plugin read), so `harness run --mock-script` and a hand-edited `settings.json` were ignored (R36);
  `kernel.max_tool_iterations` was shown in the panel but never applied — `TurnRunner` now seeds `max_iterations`
  from it (R38); `harness plugin info <name>` raised `AttributeError` (R35); the web client carried a second,
  invented copy of the settings schema that replaced the kernel's on any fetch error (R32).
- `bridge-mcp-client` dropped its third, unreachable `extensions.mcp_servers` fallback (R37).
- New tests: `tests/kernel/test_settings_declared.py` (every read declared; every panel setting read),
  `tests/kernel/test_catalog.py` (catalogue = tree, commands = dispatcher, events = SDK, committed docs = fresh boot),
  `tests/plugins/test_provider_settings.py`, and a plugin-to-plugin import guard in `tests/kernel/test_import_boundaries.py`.
- CI runs `harness catalog --check --section drift --section settings --section ui` in place of the single-file
  events-schema diff (the drift check covers that file too, and `pytest -q` runs it as well).

### Changed — P6 version and document hygiene
- Released sections `[0.2.1]`, `[0.2.0]`, `[0.1.0]` below are restored byte-for-byte to their git tags; the post-tag
  edits made in `158c836` are recorded in the "`main @ 158c836` remediation" block of this `[Unreleased]` section.
  `tests/test_docs_and_versions.py` pins the released sections so this cannot recur unnoticed.
- `/health` reports `harness.__version__` (was a literal `"2.0.0"`).
- Command errors that carry a status keep it: `TurnInProgress` is HTTP 409 (was flattened to 400).
- `harness.kernel.commands.BUILTIN_COMMANDS` is the catalogue of built-in commands; `dispatch` resolves through it.
- `TranscriptStore.list_sessions` removed from the SDK contract, scaffold and `jsonl_transcript` (a store holds one
  session; listing is `SessionManager.list_sessions`); the unused `sessions_index.jsonl` is no longer written.
- New `docs/ARCHITECTURE.md` — process model, kernel modules, command and event catalogues, session lifecycle, web
  authentication, test map — checked against the code by `tests/test_docs_and_versions.py`.
- The 22 research syntheses moved from `docs/` to `docs/research/`; README documents reading order and marks
  `docs/research/`, `review/` and `additions/` as history.
- Scenario goldens no longer pin `model.started.request_tokens_estimate` (it varies with the tmp path length).

### Changed — P5 one test estate (`review/CONSOLIDATION_LOG.md`)
- The flow-of-information suite moved from `additions/scenarios` to **`tests/scenarios`** and is part of plain `pytest -q` (kernel, plugins, frontends, e2e and the 21 record/replay scenarios in one invocation; CI runs it with the jsdom DOM lane, `SCENARIO_DOM_WAIT_MS=6000`).
- One `package.json` at the repository root (`jsdom 24.1.3`, `tailwindcss 3.4.17`; `npm ci`); the nested `tests/scenarios/_lib/node/package.json` is gone and the headless script resolves jsdom from the root install.
- `tests/frontends/ui_smoke.mjs`, `tests/frontends/test_ui_smoke.py` and `review/tools/ui_smoke.mjs` deleted; their remaining unique checks (settings modal opens with schema-driven group tabs, no browser console errors) are scenario expectations (`settings_groups_include`, `no_console_errors`) asserted by `plain_answer_web`.
- `tests/e2e/test_scenarios_s1_s6.py::test_scenario_s1_safe_daemon` deleted — it tested a throw-away aiohttp app; the real 401/415/421 checks live in `tests/frontends/test_web_server.py`.
- `additions/tools/ui_audit.py` → `tests/frontends/ui_audit.py` (still a CLI: `python tests/frontends/ui_audit.py --strict`).

### Changed — P4 provider truth lives in the provider (`review/CONSOLIDATION_LOG.md`)
- **SDK contract:** `Provider.is_configured() -> bool` and `async Provider.is_reachable() -> bool | None` are part of `harness.sdk.contracts.Provider`; `list_models()` returns the provider's catalogue with or without credentials (no network call when unconfigured, `ProviderError(kind="auth")` only when real credentials are rejected). `ProviderKit.test_readiness()` checks both; the scaffold and `docs/PORTING_RECIPES.md` show them. **Breaking for third-party providers:** the registry now refuses to register a provider that lacks any contract member (`PluginLoadError` naming them).
- **`models.list` rows are `harness.sdk.types.ModelRow`:** `{id, display_name, provider, context_tokens, input_usd_per_mtok, output_usd_per_mtok, configured, reachable, status}` with `status` ∈ `ready` / `needs_key` / `unreachable`. Probes run concurrently, a failing provider is logged at DEBUG and skipped, the command never raises. `Commands._is_provider_configured()` (secret-name heuristics) is gone.
- **Fixed: Ollama was offered as a working model while not running.** `OllamaProvider` probes `GET /api/tags` with a 300 ms timeout, caches the verdict (up or down) for 30 s, lists installed models when up and its catalogue — flagged `unreachable` — when down.
- **Fixed: hosted providers disappeared from the picker without a key** (and logged a WARNING per page load). Anthropic, Gemini, OpenAI, OpenRouter and DeepSeek now appear as `needs_key`; a self-hosted OpenAI-compatible endpoint (vLLM, LM Studio) is valid without a key.
- Web model picker groups by `status` (Ready / Needs API key / Not running); CLI `/models` shows a Status column.

### Changed — P3 one styling system, no facade (`review/CONSOLIDATION_LOG.md`)
- **Two stylesheets, one system.** `static/styles.css` is rewritten as components only (tokens, frame, conversation, banners, studio, cards, settings, inspector; 165 classes, all used) and the utilities the templates use (`flex`, `gap-2`, `text-xs`, `truncate`, `hidden`, …) come from `static/vendor/tw.css`, compiled from `index.html` + `*.js` by `npm run build:css` (`tailwind.config.cjs`, Tailwind 3.4.17, preflight off, committed, CI rebuilds and diffs it). No CDN, CSP unchanged.
- **Fixed: nothing marked `hidden` was hidden.** The page had no `.hidden` rule, so the approval banner, the proceed banner, the onboarding banner, the Stop button, the session list and the economics view were all visible on load; four undefined CSS custom properties left the settings dialog without a background. `tests/frontends/test_ui_coherence.py` now checks the cascade in jsdom.
- **Facade removed or made real:** `tool.output.delta` streams into the tool card's output (the hidden terminal drawer is gone); artifact cards render in the Artifacts tab (with a live proceed banner) as well as the timeline; the slash menu (`/plan /diff /compact …`, which no kernel command interpreted) is gone; title bar / status bar show the workspace (`/health.workspace`, new), permission mode (`kernel.security.permission_mode`, refreshed on `settings.changed`), active session title, selected model, daemon version and SSE state instead of `🪝 main`, `Autonomous`, `UTF-8`, `Sovereign Daemon Online`; the hard-coded welcome turn is replaced by the empty-timeline hint.
- Zero inline `style=` attributes in `index.html`, `app.js`, `cards.js` (progress cards use a native `<progress>`); state is carried by data attributes (`data-status`, `data-level`, `data-state`, `data-stability`, `data-error`).
- `additions/tools/ui_audit.py --strict` is now the UI gate (missing/unreferenced ids, undefined/unused classes, inline styles, stylesheet set and order, foreign scripts) and runs in CI.

### Changed — P2 kernel-owned session lifecycle (`review/CONSOLIDATION_LOG.md`)
- **The kernel resumes and creates sessions; frontends follow.** `bootstrap_kernel(resume_session=True)` makes the most recently updated session that has turns the active one (`SessionManager.resume_last()`, publishes `session.switched`); `submit_turn` without a `session_id` uses it or creates one and announces it with `session.started`. The web client no longer creates a session on every page load (previously each refresh left an empty session behind), connects SSE before fetching the transcript, adopts the kernel-chosen id from `session.started`, and shows an empty-timeline hint until the first prompt. `CliApp` no longer mints its own `session_<uuid>`: it uses the kernel's active session or adopts the one created for its first prompt.
- **`sessions.list` rows are `harness.sdk.types.SessionRow`** `{session_id, workspace, created_at, updated_at, title, turns, message_count, active, busy}`, newest activity first; `title` is the first user prompt (≤60 chars). The web picker shows title / turns / last activity; the CLI `/sessions` table does the same.
- `harness run` starts a new session per invocation unless `--continue/-c`; `harness interactive` resumes the last conversation unless `--new/-n`.
- Flow test: new scenario `daemon_restart_web_dom` (two prompts, daemon restart, page reload → same session, one row, full transcript on load); seven web goldens lose the phantom boot `session.started`.

### Changed — P1 typed event/card contract (`review/CONSOLIDATION_LOG.md`)
- **Every event payload is now a dataclass** in `harness/sdk/events.py` (`EVENT_PAYLOADS`, one `@event_payload` class per type, e.g. `ToolDecision`, `CostUpdated`, `UiCard`). `Event(data=<payload>)` serialises it and refuses a payload of the wrong type; `validate_event()` checks any envelope (required fields, leaf types, no undeclared keys, card validity for `ui.card`). All 45 kernel/plugin emit sites use the dataclasses. `harness events schema` now emits `$defs/payloads/<type>` sub-schemas with an `allOf`/`if`/`then` dispatch on `type`; `docs/events.schema.json` regenerated.
- **One spend shape — `EconomicsSnapshot`** `{turn_usd, session_usd, lifetime_usd, cap_usd, unknown_pricing, usage}` (`harness/sdk/types.py`, built by `harness/kernel/economics.py`). It is what the `economics` command returns, what `cost.updated` carries and what `GET /health` embeds as `spend`. The previous `{spend_cap_usd, totals}` command result is gone; the CLI `/economics` panel and the web Economics tab read the snapshot.
- **Card wire shape is the SDK's.** `cards.js` renders `Card{kind, id, title, data}` with the keys in `harness.sdk.cards.CARD_FIELDS` (markdown, code, diff, table, notice, progress, tree, approval, artifact, form) plus web-internal `tool`/`task`/`compaction`; the dead `plan` kind and every `a || b || c` alias chain are removed. `notice` gains the `success` level; `validate_card` rejects undeclared `data` keys. `ui.card` events from `bridge-shell-hooks` and `form.submit` now carry a real `Card`.
- `task.started` and `settings.changed` are emitted for the first time (background inbox; `settings.set`); the never-emitted `log` type is removed from the catalogue (28 → 29 types net).
- `CostLedger.record()` takes `session_id`; `totals(session_id=…)` filters by it, so session spend ≠ lifetime spend. The runner keeps `cost_usd=None` (instead of `0.0`) when a model has no known pricing, so `unknown_pricing` and the ledger agree.
- `reloadTranscript()` reads `TranscriptRecord` as written (`role`, `tool_calls[{id,name,arguments}]`, `tool_results[{tool_use_id,content,is_error}]`) and restores tool outputs after a page refresh.

### Changed — `main @ 158c836` remediation (pre-consolidation; these bullets were written into the released `[0.2.1]` section after the tag and are restored here)
- Web frontend unified on modular ES modules (`app.js`, `cards.js`, `api.js`, `events.js`, `settings.js`) with named `addEventListener` subscriptions for every event type in `events.schema.json`.
- Removed legacy inline scripts, duplicate `<head>`/`<body>` tags, fake confirmation `data-notice` attributes and `data-onclick="noop"` buttons; replaced 17 static mockup settings pages with the schema-driven `SettingsPanel` (`settings.schema`); first-run onboarding banner; CSP `script-src 'self'`.
- Removed 21 unrouted legacy REST endpoints (including `/api/sessions/{session_id}/turn` from 0.2.1) and ~650 lines of dead handlers from `server.py`; `/commands/{name}` + `/events` are the API. `sessions_switch` validates ids; command exceptions map to 400/404.
- Added `tests/frontends/test_static_contract.py`. (The jsdom smoke test and the credential-name-based model `<optgroup>` added at the same time were superseded in P5 and P4 above.)

### Fixed
- `tool.call.arguments` on the bus aliased the gate's mutable `current_args` dict, so SSE consumers saw hook/policy rewrites (absolute paths) depending on queue timing; payload serialisation now snapshots the data.
- `remember` no longer emits a notice card duplicating its own result text.

### Added
- `tests/conftest.py::event_contract_guard` — autouse fixture: every event published by non-test code during the suite must pass `validate_event`.
- `tests/frontends/test_static_contract.py`: `test_event_handlers_read_only_contract_fields` (every `data.<key>` an `app.js` handler reads ⊆ that event's payload schema, one level of delegation followed), `test_card_renderers_read_only_contract_fields` (`cards.js` ⊆ `CARD_FIELDS`), `test_transcript_reload_reads_transcript_record_fields`; the scenario suite validates every observed bus event / SSE frame against the contract.

### Fixed (earlier in this cycle)
- **Economics tab and header spend indicator always showed `$0.0000` / `0` tokens.** `renderEconomicsTab()` / `updateEconomics()` read `session_usd`, `cap_usd`, `token_usage.*` and `totals.total_cost_usd`, none of which the `economics` command emits (`{spend_cap_usd, totals: Usage.to_dict()}`). They now read the real keys and no longer fall back to a literal `$5.00` cap. Contract test: `tests/frontends/test_static_contract.py::test_economics_payload_keys_match_app_js`. (Review: `review/CODE_REVIEW_158c836.md`.)
- **Workspace memory did not round-trip.** `remember`, `search_memory`, the FTS5 store and the `memories` context source each opened a *different* `memory.db` (`.harness/tools/<tool>/`, `.harness/plugins/<plugin>/`, `.harness/`), so a remembered fact could never be searched or injected into a later session. All four now use `harness.sdk.paths.memory_db_path(workspace)` = `<workspace>/.harness/memory.db`; the FTS5 store also re-indexes from the real `.harness/sessions` directory. `search_memory` now searches remembered facts as well as transcript turns. Kernel-level regression test: `tests/kernel/test_memory_roundtrip.py`.
- **MCP tools could never execute inside the kernel.** `bridge-mcp-client` handlers took `(call: ToolCall, ctx)` while the gate invokes `handler(args: dict, ctx)`; every MCP call failed with `'dict' object has no attribute 'arguments'`. Handlers now follow the kernel contract. Regression test through the real gate: `tests/kernel/test_mcp_tool_through_kernel.py`.
- `bridge-mcp-client` now reads servers from its own settings namespace (`plugins.bridge-mcp-client.servers`) in addition to the legacy global keys.
- MCP risk classification recognises the spec-standard `readOnlyHint` / `destructiveHint` / `openWorldHint` annotations (previously only non-standard `readOnly` / `destructive` / `openWorld`), so read-only MCP tools no longer require approval in `ask` mode.

### Added
- `harness.sdk.paths` — canonical shared workspace paths (`harness_dir`, `memory_db_path`, `sessions_dir`).

---

## [0.2.1] - 2026-10-15

### Remediation Release: Full End-to-End Cohesion & Security Hardening

Release `0.2.1` addresses all architectural, security, and runtime gaps identified during the post-v0.2.0 audit (`review/CODE_REVIEW_v0.2.0.md` §9 Steps 1–6), establishing complete end-to-end cohesion between the microkernel, web frontend, agent loop, and plugin ecosystem.

#### Fixed & Remediated
- **Agent Loop Memory & Transcript Store Wiring (Step 1)**:
  - Corrected `TurnRecord` dataclass serialization mismatch and integrated session transcripts into `TurnRunner`.
  - Fixed model context feeding so tool results, error payloads, and conversational history persist and inform subsequent turns across sessions.
  - Added hermetic roundtrip verification tests (`tests/kernel/test_turn_roundtrip.py`).
- **Turn Endpoints & Tool Result Previews (Step 2)**:
  - Aligned `/api/sessions/{session_id}/turn` endpoint with dataclass-based request and response models.
  - Implemented streaming SSE and JSON fallback for real-time turn execution and tool result previews (`render_preview` contract).
  - Resolved provider resolution edge cases for scripted and external provider configurations (`tests/frontends/test_web_turn.py`).
- **Modular Frontend Unification & Strict CSP (Step 3)**:
  - Fully unified web frontend on modular ES modules (`app.js`, `bus.js`, `api.js`, `state.js`, `views/`).
  - Removed legacy inline scripts, inline styles, and `alert()` / `confirm()` popups in favor of native UI notifications and modals.
  - Enforced strict Content Security Policy (`default-src 'self'`) across static serving routes.
- **Security Hardening (Step 4)**:
  - Enforced POSIX file permissions `0600` on credentials, secrets, and settings files (`~/.harness/settings.json`, `~/.harness/secrets.json`).
  - Implemented single-use ticket and signed cookie authentication for SSE event streams, preventing unauthorized stream interception.
  - Added secret redaction filters preventing token exposure in settings GET endpoints and logs.
- **Dead v1 Tree Removal & Isolation Hardening (Step 5)**:
  - Removed dead legacy v1 packages (`harness/desktop`, `harness/web`, `harness/core`, etc.).
  - Hardened `local-executor` command execution with `DEVNULL` stdin to prevent zombie background processes from hanging on interactive prompts.
  - Made plugin data directory provisioning strictly lazy to prevent unwanted directory scaffolding.
  - Updated PyInstaller packaging spec with dynamic discovery of all 35 plugin packages.
- **Documentation Alignment (Step 6)**:
  - Reconciled `README.md`, `SECURITY.md`, and `docs/` with actual microkernel implementation and plugin inventory.
  - Formally documented `~/.harness/settings.json` architecture in `review/DECISIONS.md` (Decision D17).

---

## [0.2.0] - 2026-10-15

### Major Milestone: Modular Microkernel Architecture & Remediation

Release `0.2.0` represents a complete architectural overhaul and remediation of Harness-Zero, transitioning from an experimental monolith to a resilient, microkernel-and-plugin architecture with strict boundary enforcement, dual-origin web sandboxing, and developer SDK tooling.

---

### Added

#### Core Microkernel & SDK (`KRN-01` – `KRN-12`)
- **Microkernel Core (`harness.kernel`)**: Decoupled core runtime containing:
  - `TurnRunner` & `KernelServices`: Standardized agent turn loop supporting multi-turn memory, tool execution, and reactive background inbox wakeups.
  - `PolicyGate` & `WorkspaceJail`: Ordered security gates (Order 10: Workspace Jail, Order 50: Risk Classifier, Order 60: Tirith Security, Order 900: Permission Modes).
  - `ContextAssembler`: Multi-tier prefix-invariant context assembler with token estimation and compaction support.
  - `SettingsStore` & `Secrets`: Schema-validated settings store at `~/.harness/settings.json` (mode 0600) with in-memory CLI overrides (`set_override`) and secure credential management via `Secrets` (mode 0600).
  - `SessionManager`: Thread-safe session lifecycle and transcript storage tracking turns, model calls, and tool executions.
  - `EventBus`: Central pub/sub message bus with a 500-event ring buffer and `Last-Event-ID` replay.
- **Typed `harness.sdk`**:
  - `Protocol` interfaces: `Tool`, `Provider`, `ContextSource`, `Policy`, `Executor`, `Strategy`, `Bridge`, `Store`.
  - Testing kits in `harness.sdk.testing`: `ToolKit`, `ProviderKit`, `ContextKit`, `PolicyKit`, `ExecutorKit`, `StrategyKit`, `BridgeKit`, `StoreKit`, `HookKit`.
  - Conformance test runners validating streaming deltas, AST invariance, boundary checks, and execution idempotency.
- **Boundary Verification**: Automated tests and doctor audits enforcing that plugins only import `harness.sdk` and standard library, and the kernel imports no plugins.

#### Plugin Ecosystem (35 Built-in Plugins)
- Standardized under `harness/plugins/` across 8 component categories:
  - **Providers (5)**: `openai-compat` (universal OpenAI/DeepSeek/vLLM/OpenRouter), `provider-anthropic` (Messages API with prompt caching & thinking blocks), `provider-gemini` (Google Gemini with native tool calling), `provider-ollama` (local models with native tool calling), `provider-mock` (deterministic `ScriptedProvider`).
  - **Tools (7)**: `fs-tools` (`view_file`, `write_to_file`, `edit_file`), `search-tools` (`search_codebase` via ripgrep and python fallback), `shell-tools` (`run_command`, `manage_task`), `tool-artifacts` (`create_artifact`), `tool-memory` (`remember`, `search_memory`), `tool-skills` (`view_skill`), `tool-screenshot-macos` (`capture_screen`).
  - **Policies (4)**: `policy-workspace-jail` (path traversal guard), `policy-risk-classifier` (risk classes: READ, WRITE, EXEC, DESTRUCTIVE), `policy-tirith` (secret leak, SSRF, and destructive command auditing), `policy-permission-modes` (`plan`, `ask`, `allow`, `bypass`).
  - **Context Sources (6)**: `context-identity`, `context-instructions` (`AGENTS.md`, `CLAUDE.md`, etc.), `context-tool-guidance`, `context-skills-catalog`, `context-repo-map` (AST symbol outline), `context-memories`.
  - **Executors (2)**: `local-executor` (process group isolation, `DEVNULL` stdin, pager configuration, streaming stdout/stderr), `executor-worktree` (git worktree isolation for collision-free parallel edits).
  - **Stores (4)**: `store-jsonl-transcript` (append-only JSONL dual transcript store), `store-fts5-search` (SQLite FTS5 full-text index), `store-fs-artifacts` (isolated filesystem artifact storage), `store-cost-ledger` (per-model token economics and spend tracking).
  - **Strategies (2)**: `strategy-single-loop` (standard reactive autonomous loop), `strategy-architect-editor` (two-phase architect/editor coding with diff evaluation).
  - **Hooks & Bridges (5)**: `hook-checkpoints` (shadow git snapshots before mutating tools), `bridge-shell-hooks` (Claude Code-compatible shell hooks), `bridge-mcp-client` (stdio JSON-RPC MCP client), `bridge-mcp-server` (`harness mcp` stdio server), `bridge-acp` (`harness acp` Agent Client Protocol stdio server).
- **Plugin Manifests**: Each plugin configured via `harness_plugin.toml` with declared capabilities, settings schemas, and dependencies.

#### Web Studio & Dual-Origin Sandbox (`harness/frontends/web`)
- **Modular Frontend Architecture**: Built on vanilla ES modules (`app.js`, `api.js`, `events.js`, `cards.js`, `settings.js`, `styles.css`) with zero build step and zero external CDN dependencies.
- **Strict Content Security Policy (CSP)**: `default-src 'self'`, `script-src 'self'` (zero `'unsafe-inline'`, zero `'unsafe-eval'`), and frame isolation.
- **Dual-Origin SOP Sandbox**:
  - Control & API Daemon on `:8000` (`/commands/*`, `/events`, `/health`).
  - Artifact Sandbox on `:8001` (`/artifacts/*`) enforcing `default-src 'none'; script-src 'none'` to isolate agent-generated HTML/SVG from tokens and local storage.
- **Secure Authentication & CSRF Protection**:
  - Bearer token / `X-Harness-Token` authentication for API calls.
  - `HttpOnly` session cookie and short-lived single-use tickets (`POST /auth/ticket`) for EventSource SSE streaming (no tokens in query strings).
  - Host header rebinding validation (HTTP 421) and Origin / Sec-Fetch-Site checking (HTTP 403).
- **Interactive UI Cards & Streaming**:
  - Delegated event handling for interactive tool approval cards, forms, and proceed banners.
  - Streaming character markdown and reasoning thinking blocks.
  - Schema-driven settings panel reading kernel configurations with secrets masked.
  - Real-time token economics tracker and context assembler inspector.

#### Developer Tooling & CLI (`harness/cli.py`)
- **CLI Commands**:
  - `harness run`: Single autonomous turn execution with `--provider`, `--model`, and `--mock-script` (applied in-memory before plugin boot).
  - `harness ui`: Web studio server daemon launcher.
  - `harness mcp` & `harness acp`: Stdio protocol bridges for external hosts and editor integration.
  - `harness plugin new`: Scaffolding generator for any of the 8 component kinds.
  - `harness plugin doctor`: Comprehensive manifest, boundary, and capability audit across plugins (`--all`).
  - `harness plugin test`: Conformance test kit runner.
  - `harness events schema`: JSON Schema exporter for the unified event protocol.

---

### Changed
- **Config & Workspace Hermeticity**: Configuration cleanly honors `HARNESS_CONFIG_DIR`, defaulting to `$HOME/.harness` with 0600 file permissions and 0700 directory permissions.
- **Lazy Plugin Data Directories**: Kernel creates `.harness/plugins/<name>/` lazily on first access rather than generating 35 empty directories on startup.
- **Executor Hardening**: Local executor sets `GIT_PAGER=cat`, `GIT_TERMINAL_PROMPT=0`, `PAGER=cat`, and defaults non-interactive stdin to `DEVNULL` to prevent command hangs.
- **Turn Result Serialization**: Commands dispatch serialization cleanly handles nested dataclasses (`TurnResult`, `ExecResult`).

---

### Removed & Quarantined
- **Deleted Dead v1 Monolith**:
  - Deleted obsolete packages: `harness/core/`, `harness/memory/`, `harness/models/`, `harness/sandboxing/`, `harness/skills/`, `harness/artifacts/`, and `harness/desktop/`.
  - Deleted 10 legacy root-level test files that existed solely to cover obsolete v1 code.
  - Removed superseded monolithic `harness/experimental/architect_editor.py`.
- **Quarantined in `harness/experimental/`**:
  - `battle_arena.py`: Multi-agent git worktree battle evaluator with self-contained `WorktreeManager`.
  - `spawn_ledger.py`: Hierarchical subagent spawn tracking prototype.
  - `state_graph.py`: Cyclic state graph workflow engine.
  - `concurrency_gate.py`: Concurrent task slot gate prototype.
  - `browser_cdp.py`: Chrome DevTools Protocol engine prototype.

---

## [0.1.0] - 2026-10-14
- Initial experimental prototype release.
