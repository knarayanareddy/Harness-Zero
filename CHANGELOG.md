# Changelog

All notable changes to the **Harness-Zero** project are documented in this file.

The format is based on [Keep a Changelog](https://keepachangelog.com/en/1.1.0/),
and this project adheres to [Semantic Versioning](https://semver.org/spec/v2.0.0.html).

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
