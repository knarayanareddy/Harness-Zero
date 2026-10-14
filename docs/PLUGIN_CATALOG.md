# Harness-Zero Builtin Plugin Catalog

This catalog documents all **35 built-in plugins** included with Harness-Zero (§11, §12). Every plugin is modular, adheres strictly to the SDK contract boundaries, and runs hermetic conformance tests.

---

## 1. Discovery Roots and Loading Priority

Plugins are discovered across standard filesystem roots and loaded in strict order of trust:

| Priority | Discovery Root | Trust Tier | Default Capabilities |
| :--- | :--- | :--- | :--- |
| **1** | `harness/plugins/**` | `builtin` | Declared in manifest |
| **2** | `~/.harness/plugins/**` | `local` | Clamped to non-escalating |
| **3** | `entry_points("harness.plugins")` | `local` | Python package extensions |
| **4** | `<workspace>/.harness/plugins/**` | `workspace` | Requires `trusted_workspace_plugins: true` |

---

## 2. Complete Plugin Inventory (35 Builtin Plugins)

| Plugin ID | Component Kind | Capabilities | Summary | Status |
| :--- | :--- | :--- | :--- | :--- |
| `openai-compat` | `provider` | `net`, `secrets` | Universal OpenAI-compatible LLM provider (OpenAI, DeepSeek, OpenRouter, vLLM) | Production |
| `provider-anthropic` | `provider` | `net`, `secrets` | Anthropic Messages API provider with prompt caching and thinking blocks | Production |
| `provider-gemini` | `provider` | `net`, `secrets` | Google Gemini generateContent and function calling provider | Production |
| `provider-ollama` | `provider` | `net` | Local Ollama provider with native tool calling and XML fallback protocol | Production |
| `provider-mock` | `provider` | *(none)* | Deterministic ScriptedProvider for hermetic testing and offline CI | Production |
| `fs-tools` | `tool` | `fs:read`, `fs:write` | Filesystem inspection, write, and surgical search/replace editing tools | Production |
| `search-tools` | `tool` | `fs:read`, `exec` | High-performance codebase regex search with ripgrep and fallback | Production |
| `shell-tools` | `tool` | `exec` | Shell command execution and background task management tools | Production |
| `tool-artifacts` | `tool` | `fs:write` | Creation and manifest tracking of markdown and HTML artifacts | Production |
| `tool-memory` | `tool` | *(none)* | Cross-session facts and architectural memory recall tools | Production |
| `tool-skills` | `tool` | *(none)* | On-demand inspection and activation of progressive skill playbooks | Production |
| `tool-screenshot-macos` | `tool` | `fs:read`, `exec` | macOS desktop screen capture tool returning ImageBlocks | Production |
| `policy-workspace-jail` | `policy` | `fs:read` | Workspace jail policy denying path traversal outside the workspace | Production |
| `policy-risk-classifier` | `policy` | *(none)* | Evaluates command risk and assigns RiskClass (READ, WRITE, EXEC, DESTRUCTIVE) | Production |
| `policy-tirith` | `policy` | *(none)* | Tirith security capsule auditing secret leaks, SSRF targets, and destructive commands | Production |
| `policy-permission-modes` | `policy` | *(none)* | Human-in-the-loop permission modes: `plan`, `ask`, `allow`, and `bypass` | Production |
| `context-identity` | `context` | *(none)* | Core system prompt identity and behavioral guidelines context source | Production |
| `context-instructions` | `context` | `fs:read` | Reads project instruction files (AGENTS.md, CLAUDE.md, INSTRUCTIONS.md) | Production |
| `context-tool-guidance` | `context` | *(none)* | Tool calling guidance and parameter formatting advice | Production |
| `context-skills-catalog` | `context` | *(none)* | Progressive skills catalog summary for high-level capability awareness | Production |
| `context-repo-map` | `context` | `fs:read` | AST symbol mapping and dense architectural context of the workspace | Production |
| `context-memories` | `context` | *(none)* | Injects persistent memories and architectural decisions into session prompt | Production |
| `local-executor` | `executor` | `exec` | Local process executor with streaming stdout/stderr, kill, and wait | Production |
| `executor-worktree` | `executor` | `exec` | Async Git worktree executor with zero-collision branch sandboxing | Production |
| `store-jsonl-transcript` | `store` | `fs:read`, `fs:write` | Append-only dual-log JSONL transcript store with pointer truncation | Production |
| `store-fts5-search` | `store` | *(none)* | SQLite FTS5 full-text search index and microsecond memory seek | Production |
| `store-fs-artifacts` | `store` | *(none)* | Filesystem artifact storage with traversal protection and manifest metadata | Production |
| `store-cost-ledger` | `store` | `fs:read`, `fs:write` | Token economics and cost ledger with per-model pricing tables | Production |
| `strategy-single-loop` | `strategy` | *(none)* | Default single-loop agent strategy with reactive wakeups and STOP hooks | Production |
| `strategy-architect-editor` | `strategy` | `fs:read`, `fs:write`, `exec` | Two-phase Architect/Editor coding strategy with diffs and test evaluation | Production |
| `hook-checkpoints` | `hook`, `tool` | `fs:write`, `exec` | Shadow Git checkpoints before mutating tools with rollback restore | Production |
| `bridge-shell-hooks` | `hook` | `exec` | Claude Code-compatible shell hooks bridge (PreToolUse, PostToolUse, Stop) | Production |
| `bridge-mcp-client` | `tool` | `exec`, `net` | Stdio JSON-RPC 2.0 MCP client exposing external servers as native tools | Production |
| `bridge-mcp-server` | `bridge` | `exec` | Stdio JSON-RPC 2.0 MCP server exposing registered Harness tools to external hosts | Production |
| `bridge-acp` | `bridge` | *(none)* | Agent Client Protocol stdio server frontend for editor integrations | Production |

---

## 3. Detailed Plugin Specifications by Group

### A. LLM Providers
- **`openai-compat`**: Speaks standard ChatCompletions API with tool calling, SSE streaming deltas, and usage events. Supports OpenAI, OpenRouter, DeepSeek, and local vLLM.
- **`provider-anthropic`**: Uses Anthropic Messages protocol (`/v1/messages`), supporting 5-tier prompt caching headers (`cache_control: {"type": "ephemeral"}`) and extended thinking blocks.
- **`provider-gemini`**: Connects to Google Generative Language API with function declarations and safety filter mapping.
- **`provider-ollama`**: Integrates with local Ollama daemon (`http://127.0.0.1:11434`), supporting local quantized models (`qwen2.5-coder`, `deepseek-r1`).
- **`provider-mock`**: Deterministic test provider (`ScriptedProvider`) for hermetic CI test suites without network egress.

### B. Workspace Tools
- **`fs-tools`**: Core file operations (`view_file`, `write_to_file`, `replace_file_content`, `list_directory`). Enforces relative workspace paths and atomic writes.
- **`search-tools`**: Codebase search (`search_codebase`) via ripgrep (`rg`) with automatic regex parsing and pure Python fallback.
- **`shell-tools`**: Asynchronous bash process runner (`run_command`) with process management (`manage_task`), background tasks, and persistent terminal support.
- **`tool-artifacts`**: Interactive artifact generator saving markdown, HTML widgets, and UI progression diagrams into `.harness/artifacts`.
- **`tool-memory`**: Explicit fact storage and recall (`search_memory`, `save_memory`).
- **`tool-skills`**: Playbook reader (`view_skill`) fetching detailed workflows from `builtin/skills` and workspace skill directories.
- **`tool-screenshot-macos`**: Native screen capture (`screencapture -x`) returning JPEG/PNG `ImageBlock` for visual debugging.

### C. Safety & Security Policies
- **`policy-workspace-jail`** (Order 10): Validates every file path against the active workspace root via `Path.resolve()`. Denies any path escaping the boundary.
- **`policy-risk-classifier`** (Order 50): Assigns risk classifications (`READ`, `WRITE`, `EXEC`, `DESTRUCTIVE`) based on command tokens, file targets, and tool spec declarations.
- **`policy-tirith`** (Order 60): Enforces Tirith security capsule rules: blocks private key leaks, audits cloud metadata IP access (AWS/GCP/Azure 169.254.169.254), and flags blast-radius disk operations.
- **`policy-permission-modes`** (Order 900): Maps decisions according to user permission configuration:
  - `plan`: Denies destructive and execute actions.
  - `ask`: Requires human confirmation for actions requiring elevated permissions.
  - `allow`: Allows safe operations.
  - `bypass`: Unattended execution for non-destructive actions.

### D. Context Sources & Prompt Assembly
- **`context-identity`**: Invariant agent persona, system role, and operating rules (`Stability.STATIC`).
- **`context-instructions`**: Reads project markdown documentation (`AGENTS.md`, `CLAUDE.md`, `.harness/INSTRUCTIONS.md`) into session context (`Stability.SESSION`).
- **`context-tool-guidance`**: Instructions on parameter formatting, diff syntax, and tool usage (`Stability.STATIC`).
- **`context-skills-catalog`**: High-level summary table of available skill playbooks (`Stability.SESSION`).
- **`context-repo-map`**: Tree-sitter and AST symbol ranking packing entire repository structures into dense outlines (`Stability.SESSION`).
- **`context-memories`**: Injects cross-session memories and user preferences into active turn context (`Stability.TURN`).

### E. Execution Sandboxes
- **`local-executor`**: Native subprocess execution with stdin/stdout streaming, timeouts, signal propagation, and clean process killing.
- **`executor-worktree`**: Dynamic Git worktree creation (`git worktree add -b`) isolating subagents and candidate solutions in scratch branches.

### F. Memory & Transcript Stores
- **`store-jsonl-transcript`**: Append-only log saving turns into `transcript.jsonl` (compact) and `transcript_full.jsonl` (detailed), enabling byte-offset pointers.
- **`store-fts5-search`**: SQLite virtual table FTS5 full-text search indexing turn contents and tool results for sub-millisecond retrieval.
- **`store-fs-artifacts`**: Manages generated artifacts with metadata tracking and sandboxed iframe isolation on Port 8001.
- **`store-cost-ledger`**: Token usage ledger recording input, output, and cache hit metrics against per-model pricing tables.

### G. Orchestration Strategies
- **`strategy-single-loop`**: Standard single-agent reasoning loop handling model generation, tool invocation, approvals, and stop hook re-entry.
- **`strategy-architect-editor`**: Dual-phase coding pattern where an Architect model plans multi-file changes and an Editor model applies verified diffs with automated test loops.

### H. Lifecycle Hooks & Bridges
- **`hook-checkpoints`**: Cline-style Git snapshot mechanism saving working tree state before mutating actions and providing `checkpoint_restore`.
- **`bridge-shell-hooks`**: Claude Code-compatible hook runner executing external shell scripts on tool events with stdin JSON and exit code filtering.
- **`bridge-mcp-client`**: Connects external MCP servers over stdio, discovers tools, maps schemas to `mcp__<server>__<tool>`, and handles bidirectional tool calls.
- **`bridge-mcp-server`**: Exposes registered Harness tools to external MCP hosts over stdio.
- **`bridge-acp`**: Speaks Agent Client Protocol over stdio for deep IDE integrations (VS Code, JetBrains).
