# Harness-Zero: The Universal Sovereign Autonomous Agent Engine

> **Reverse-Engineered Blueprints & Engineering Specifications Synthesized from:**
> Google Antigravity · OpenAI ChatGPT Desktop (Codex) · Anthropic Claude Cowork (Operon) · Hermes · Pi · Aider · LangGraph · LiteLLM · Alibaba Qwen-Agent

---

## 1. What is Harness-Zero?

**Harness-Zero** is a next-generation, local-first, universal autonomous coding agent harness. It addresses the fragmentation across proprietary AI applications by uniting the greatest engineering superpowers into a modular kernel and SDK architecture:

```mermaid
flowchart TD
    subgraph UI ["1. Presentation & Execution Surfaces"]
        CLI["Pi Minimalist Terminal CLI (harness interactive / harness run)"]
        WEB["Claude Modular Studio Web UI (Port 8000)"]
        SOP_SANDBOX["Dual-Port SOP Webview Sandbox (Port 8001)"]
        ACP["Agent Client Protocol Server (harness acp)"]
        MCP_SRV["MCP Stdio Server Bridge (harness mcp)"]
    end

    subgraph Core ["2. Modular Kernel & SDK"]
        PROMPT["Pi <1k Token Minimal Prompt + 5-Tier Prefix Cache (90%+ Hits)"]
        BREAKER["Kernel Circuit Breakers & Failover Engine"]
        MODELS["Multi-Provider Switching (/model across 15+ LLMs)"]
        BYOK["Local Model Engine (Ollama / vLLM / MLX / Open Weights)"]
        BUS["Kernel EventBus with Replay Buffer & SSE Stream"]
    end

    subgraph Plugins ["3. Modular Plugin Ecosystem (35 Builtins)"]
        PROVIDERS["Providers (OpenAI, Anthropic, Gemini, Ollama, Mock)"]
        TOOLS["Tools (FS, Search, Shell, Memory, Screen, Skills, Artifacts)"]
        POLICIES["Policies (Jail, Risk Classifier, Tirith, Modes)"]
        CONTEXT["Context Sources (Identity, Repo Map, Instructions, Memories)"]
        EXECUTORS["Executors (Local Subprocess, Git Worktrees)"]
        BRIDGES["Bridges (Shell Hooks, MCP Client, MCP Server, ACP)"]
    end

    subgraph Memory ["4. Codebase Intelligence & Memory"]
        FTS5["SQLite FTS5 Byte-Offset O(1) History Seek"]
        TRANSCRIPT["Dual-Log transcript.jsonl with Pointer Truncation"]
        REPOMAP["Whole-Codebase AST Symbol Outline & PageRank Map"]
        DIFF_LOOP["Aider Self-Healing Search/Replace Linter Loop"]
    end

    subgraph Experimental ["5. Quarantined Experimental Prototypes"]
        EXP_ARENA["Worktree Competitive Battle Arena (harness/experimental/)"]
        EXP_SPAWN["Hierarchical Subagent Spawn Ledger (harness/experimental/)"]
        EXP_GRAPH["Cyclic StateGraph Workflows (harness/experimental/)"]
        EXP_CDP["Chrome DevTools Protocol Browser Engine (harness/experimental/)"]
    end

    UI <--> Core
    Core <--> Plugins
    Plugins <--> Memory
```

---

## 2. Implementation Status

| Feature / Subsystem | Status | Description |
| :--- | :--- | :--- |
| **Modular Kernel & SDK** | **Implemented** | Strict boundary enforcement (`harness.sdk`), event bus, capability gates. |
| **Universal BYOK Providers** | **Implemented** | Native streaming, tool calling, and prompt caching for OpenAI, Anthropic, Gemini, Ollama, and Mock. |
| **5-Tier Prefix-Invariant Caching** | **Implemented** | Static, session, and turn stability layout delivering 90%+ cache hits on Anthropic and OpenAI. |
| **Dual-Origin Web Studio** | **Implemented** | Port 8000 API, Port 8001 SOP preview iframe, token authentication, card event stream (`harness ui`). |
| **Tirith Security Capsule & Jail** | **Implemented** | Path jail (Order 10), risk classification (Order 50), secret leak auditing and SSRF protection (Order 60). |
| **Permission Modes State Machine** | **Implemented** | `plan`, `ask`, `allow`, and `bypass` execution modes with live interactive card approvals. |
| **Git Worktree Isolation** | **Implemented** | Scratch branch isolation (`git worktree add -b`) preventing collisions across subagents. |
| **SQLite FTS5 Memory** | *Partial* | FTS5 search store and memory recall tools implemented; automated transcript indexing wired via store plugins. |
| **Codebase Symbol Repo Map** | **Implemented** | AST symbol extraction and ranking packing large repos into compact prompt context. |
| **Native macOS Computer Use** | *Partial* | Screen capture tool via macOS `screencapture` implemented; native `AXUIElement` accessibility automation planned for desktop bridge. |
| **Claude Code Shell Hooks Bridge** | **Implemented** | HookKit-conformant execution of Claude Code scripts on tool events with stdin JSON. |
| **MCP Client & Server Bridges** | **Implemented** | Stdio JSON-RPC 2.0 MCP client (auto-discovering tools/resources) and stdio MCP server (`harness mcp`). |
| **Agent Client Protocol (ACP)** | **Implemented** | Stdio server frontend for editor and IDE integration (`harness acp`). |
| **Plugin Scaffolding & Doctor** | **Implemented** | `harness plugin new`, `harness plugin test`, and `harness plugin doctor` diagnostic tooling. |
| **Multi-Agent Battle Arena** | *Experimental* | Competitive worktree evaluation quarantined in `harness/experimental/` pending `spawn_child`. |
| **Hierarchical Spawn Ledger** | *Experimental* | Subagent spawn tracking quarantined in `harness/experimental/` awaiting subagent spawn tool. |
| **Cyclic StateGraph Engine** | *Experimental* | State machine with rollback checkpointer quarantined in `harness/experimental/`. |
| **Browser CDP Engine** | *Experimental* | Chrome DevTools Protocol engine quarantined in `harness/experimental/browser_cdp.py`. |

---

## 3. Getting Started

### Installation

```bash
# Clone the repository
git clone https://github.com/knarayanareddy/Harness-Zero.git
cd Harness-Zero

# Create virtual environment and install in editable mode
uv venv
source .venv/bin/activate
uv pip install -e ".[dev]"
```

### Running the Web Studio (`harness ui`)

Launch the local Sovereign Agent Studio web interface with dual-origin sandbox isolation:

```bash
harness ui --port 8000 --sandbox-port 8001
```

- **Authentication**: Generates a random cryptographic session token written to `.harness/daemon.json` (0600 permissions).
- **Dual-Origin Security**: Core API and event bus stream on `http://127.0.0.1:8000`; untrusted HTML and SVG artifacts render inside a sandboxed iframe on `http://127.0.0.1:8001`, cryptographically preventing XSS prompt-injection attacks.
- **Host Header Protection**: Blocks DNS rebinding attacks; add external origins via `--allow-host`.
- **Sessions are kernel-owned**: the daemon resumes the workspace's last conversation on start and creates a session on the first prompt; the client never creates one at boot.
- **Styling**: two stylesheets, `static/styles.css` (components) and `static/vendor/tw.css` (utilities compiled from the templates). After editing class names in `index.html` / `*.js`, run `npm run build:css` and commit the result; `python tests/frontends/ui_audit.py --strict` checks the page stays coherent (CI runs both).

### Running Interactive CLI Turns

```bash
# Start an interactive pair-programming session (resumes the last conversation; --new starts fresh):
harness interactive

# Run a single autonomous command in a new session (--continue appends to the last conversation):
harness run "Audit repository dependencies and run tests" --workspace . --provider anthropic --model claude-3-7-sonnet
```

### Protocol Bridges

```bash
# Run stdio MCP Server (exposing Harness tools to external hosts):
harness mcp

# Run Agent Client Protocol (ACP) stdio server for IDEs:
harness acp
```

### Managing & Testing Plugins

```bash
# Scaffold a new plugin:
harness plugin new --kind context my_rules

# Run conformance tests on a plugin:
harness plugin test ./my_rules

# Run conformance tests on all 35 builtin plugins:
harness plugin test --all

# Run diagnostic health checks on plugins:
harness plugin doctor --all
```

---

## 4. Verification & Testing

Harness-Zero uses hermetic, isolated test fixtures that never pollute `$HOME` or external environments. One
invocation runs everything — kernel, plugins, frontends, e2e acceptance and the 24 record/replay
flow-of-information scenarios that drive the shipped web client in jsdom against a real daemon
(`tests/scenarios/`, needs `node` + `npm ci`):

```bash
# The complete suite (≈5 minutes; CI runs exactly this on Linux and macOS, Python 3.11 and 3.12):
export HOME=$(mktemp -d); export HARNESS_CONFIG_DIR=$HOME/.harness   # two statements: $HOME must be re-evaluated
SCENARIO_DOM_WAIT_MS=6000 pytest -q

# Subsets:
pytest tests/kernel -q                                   # kernel units, roundtrips, boundary enforcement
pytest tests/scenarios -q                                # flow-of-information scenarios only (SCENARIO_DOM=0 without node)
harness plugin test --all                                # plugin conformance (= pytest tests/plugins)
harness catalog gen                                      # rewrite docs/catalog/* after changing a plugin, tool, setting or event

# Lint, catalog/settings/UI freshness, compiled CSS freshness and the web UI audit (all CI gates):
ruff check .
harness catalog --check --section drift --section settings --section ui   # docs/catalog/*, declared settings, client contract
npm run build:css && git diff --exit-code -- harness/frontends/web/static/vendor/tw.css
python tests/frontends/ui_audit.py --strict
```

Test counts are not repeated here on purpose — `pytest -q` prints the current number; `tests/test_docs_and_versions.py`
keeps this README, `docs/ARCHITECTURE.md` and the CHANGELOG honest.

---

## 5. Documentation Suite

Documents that describe **this** system, in the order to read them:

- [`docs/ARCHITECTURE.md`](docs/ARCHITECTURE.md): process model, kernel modules, the command catalogue, the event catalogue with payload keys, session lifecycle, web authentication, where the tests live (checked against the code by `tests/test_docs_and_versions.py`).
- [`docs/architecture/CONTRACTS.md`](docs/architecture/CONTRACTS.md): the SDK protocols plugins implement (`Provider`, `ToolHandler`, `Policy`, stores, strategies, hooks), domain types and boundary invariants.
- [`docs/catalog/`](docs/catalog/PLUGINS.md): the generated catalogues — plugins, tools, settings, events, commands and the web client contract (`harness catalog gen`, gated by `harness catalog --check`).
- [`docs/PORTING_RECIPES.md`](docs/PORTING_RECIPES.md): authoring new plugins, with conformance kits.
- [`docs/events.schema.json`](docs/events.schema.json): generated event schema (`harness events schema`); [`CHANGELOG.md`](CHANGELOG.md) for what changed when.

History and background (not required to use or extend the system):

- [`docs/research/`](docs/research/00_README_AND_MANIFEST.md): the 22 reverse-engineering syntheses of other agent systems that the design was derived from.
- [`review/`](review/README.md): the four code reviews, decisions, the DeepSeek-Harness research and integration pack, and `CONSOLIDATION_LOG.md` — the per-phase record (P1–P6, then the WS phases) of how the tree reached its current shape.
- [`additions/`](additions/README.md): the executed flow-of-information test's findings (F-1 … F-12) and a portable patch; its runnable parts now live in `tests/scenarios/` and `tests/frontends/ui_audit.py`.