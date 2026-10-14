# Changelog

All notable changes to the **Harness-Zero** project are documented in this file.

The format is based on [Keep a Changelog](https://keepachangelog.com/en/1.1.0/),
and this project adheres to [Semantic Versioning](https://semver.org/spec/v2.0.0.html).

---

## [0.2.0] - 2026-10-14

### Major Milestone: Modular Architecture & Remediation

Release `0.2.0` represents a complete architectural overhaul of Harness-Zero, transitioning from an experimental prototype monolith to an extensible, microkernel-and-plugin architecture with strict boundary enforcement, dual-origin web sandboxing, and developer SDK tooling.

---

### Added

#### Core Kernel & SDK (`KRN-01` – `KRN-12`)
- **Microkernel Core**: Modular kernel (`harness.kernel`) decoupling the session runner, event bus, context engine, tool dispatcher, approval store, and storage engine.
- **`harness.sdk` API**: Stable, typed abstract base classes and testing kits for 8 component kinds:
  - `BaseTool` & `ToolKit`
  - `BaseProvider` & `ProviderKit`
  - `BaseContextEngine` & `ContextKit`
  - `BasePolicy` & `PolicyKit`
  - `BaseExecutor` & `ExecutorKit`
  - `BaseStrategy` & `StrategyKit`
  - `BaseBridge` & `BridgeKit`
  - `BaseStore` & `StoreKit`
- **Testing Kits**: Built-in validation suites in `harness.sdk.testing` allowing plugins to test streaming deltas, AST invariance, boundary checks, and execution idempotency.
- **Import Boundary Verification**: Kernel tests enforcing that plugins only import `harness.sdk`, the SDK imports no external harness modules, and the kernel imports no plugins.

#### Plugin Ecosystem (`PLG-01` – `PLG-22`)
- **35 Builtin Plugins**: Sliced and standardized into `builtin/plugins/` across 8 component categories:
  - **Providers**: Claude, OpenAI, Ollama, OpenRouter, Mock, VLLM.
  - **Tools**: Bash, File System, Search (FTS5/grep), Git, Browser, Desktop.
  - **Context**: RepoMap, AST, Sliding Window, Sticky Notes, Summarizer.
  - **Policies**: Approval Policy, Tirith Security Capsule, Rate Limiting.
  - **Executors**: Local Subprocess, Sandboxed Executor.
  - **Strategies**: ReAct Loop, CodeAct Loop, Architect-Editor Pair.
  - **Bridges**: Aider CLI, OpenDevin, MCP Server, Cowork Operon.
  - **Stores**: SQLite FTS5 Event Store, In-Memory Ephemeral Store.
- **Plugin Manifests**: Strict schema validation (`manifest.json`) verifying declared capabilities against the `Capability` enum (`approval`, `approval:bypass`, `exec`, `fs:read`, `fs:write`, `net`, `secrets`, `ui`).

#### Web Frontend & Dual-Origin Sandbox (`WEB-01` – `WEB-08`)
- **Dual-Origin SOP Sandbox**:
  - Control Daemon running on `http://127.0.0.1:8000` (API, WebSocket, Admin UI).
  - Artifact Sandbox running on `http://127.0.0.1:8001` (`/sandbox/` rendering user/agent HTML/SVG/JS with zero access to admin tokens or local storage).
- **Security Middleware**: Bearer token authentication, Host header enforcement, Origin checking, and single-use WebSocket authentication tickets.
- **Interactive UI**:
  - Chat composer with streaming markdown, code syntax highlighting, and cancel execution button.
  - Human-in-the-loop modal dialogs for tool approval requests.
  - Dynamic plugin management tabs (System, Extensions, Developer, Skills, Connectors, Plugins).
  - Interactive search and filter controls.

#### Developer Tooling & CLI (`SDK-01`, `SDK-02`)
- **Scaffolding Tooling (`harness plugin new`)**: Generates fully tested starter packages for any of the 8 component kinds with manifests, entrypoints, and automated SDK test kits.
- **Doctor & Diagnostic Tooling (`harness plugin doctor`)**: Audits plugin manifests, capability validity, and enforces zero kernel import boundary violations across all registered plugins.
- **Test Runner (`harness plugin test`)**: Invokes component-specific SDK test kits for single plugins or the full catalog (`--all`).

#### Protocol Bridges (`BRG-01` – `BRG-04`)
- Standardized bridge abstractions (`harness.bridges`) for Aider, OpenDevin, MCP, and Cowork Operon runtimes.

#### Documentation (`SDK-03`, `DOC-01`, `DOC-02`, `DOC-03`)
- **Porting Recipes (`docs/PORTING_RECIPES.md`)**: Comprehensive guide for porting legacy tools, providers, and loops to modern SDK plugins.
- **Plugin Catalog (`docs/PLUGIN_CATALOG.md`)**: Full inventory of all 35 builtin plugins with capabilities, settings, and lifecycle statuses.
- **Architecture Contracts (`docs/architecture/CONTRACTS.md`)**: Detailed API specifications for all 8 SDK base classes and test kits.
- **Security Policy (`SECURITY.md`)**: Threat model, trust boundaries, dual-origin architecture, Tirith rules, and disclosure policy.
- **README Overhaul**: Implementation status matrix (Implemented vs Experimental vs Planned), quickstart, and test count corrections.

---

### Changed
- **Config Hermeticity**: Configuration and state directories honor `HARNESS_CONFIG_DIR`, defaulting cleanly to `$HOME/.harness` with zero cross-test pollution.
- **Workspace Jail**: Path operations strictly validated using `safe_path()` to prevent directory traversal and symlink escapes.
- **CLI Commands**: Unified under modern subcommands (`harness run`, `harness ui`, `harness plugin`, `harness events`, `harness session`).

---

### Quarantined & Removed (`EXP-01`, `EXP-02`)
- **Quarantined**:
  - `battle_arena.py` (multi-agent battle evaluator) moved to `harness/experimental/`.
  - `spawn_ledger.py` (agent hierarchy spawn tracker) moved to `harness/experimental/`.
  - `state_graph.py` (state-graph workflow executor) moved to `harness/experimental/`.
  - `concurrency_gate.py` (cross-process mutex lock) moved to `harness/experimental/`.
  - `browser_cdp.py` (Stagehand/CDP browser engine with mock fallbacks removed) moved to `harness/experimental/`.
- **Removed**:
  - Legacy monolithic `harness/web/` directory (superseded by `harness/web/`).
  - Unused `harness/models/circuits.py` (dead router with hardcoded model lists).
  - Obsolete test verification script `scripts/comprehensive_features_verification.py`.
  - Unimplemented visual click predictor references from macOS bridge and skills.

---

## [0.1.0] - 2026-10-14
- Initial experimental prototype release.
