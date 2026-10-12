# Custom Agent Harness: Architectural Blueprints & Implementation Specs
*Derived from the Reverse Engineering of Google Antigravity, OpenAI ChatGPT Desktop, Anthropic Claude Cowork, Hermes, Pi, Aider, LangGraph, Browser-Use, Stagehand, LiteLLM, UI-TARS, and Qwen-Agent*

---

## 1. Project Overview

This repository contains the complete technical specifications, protocol contracts, schemas, and runnable code for constructing the **Omni-Harness Autonomous Agent Engine**.

### Universal BYOK & Local-First Freedom
The harness is 100% vendor-agnostic and supports:
* **Bring Your Own Key (BYOK)**: OpenAI, Anthropic, Google Gemini, DeepSeek, OpenRouter, and Zhipu GLM.
* **100% Offline Local Inference**: Ollama, vLLM, LM Studio, and Apple MLX running open-weight models (`Qwen-2.5-Coder`, `DeepSeek-R1-Distill`, `Llama-3.3`).

---

## 2. Complete 22-Document Specification Suite

| Specification File | Focus Area | Key Architectural Patterns Covered |
| :--- | :--- | :--- |
| [`00_README_AND_MANIFEST.md`](file:///Users/brightech/.gemini/antigravity/scratch/custom-harness-specs/00_README_AND_MANIFEST.md) | Master Index | System overview, architectural goals, and quick-start roadmap. |
| [`01_SYSTEM_TOPOLOGY_AND_ENGINE_DESIGN.md`](file:///Users/brightech/.gemini/antigravity/scratch/custom-harness-specs/01_SYSTEM_TOPOLOGY_AND_ENGINE_DESIGN.md) | System Topology | Process separation, Go vs Node vs Rust vs Bun daemons, daemon supervision. |
| [`02_IPC_PROTOCOLS_AND_EVENT_STREAMING.md`](file:///Users/brightech/.gemini/antigravity/scratch/custom-harness-specs/02_IPC_PROTOCOLS_AND_EVENT_STREAMING.md) | Wire Protocols | ConnectRPC, JSON-RPC 2.0, SSE frames, reactive non-polling wakeup engine. |
| [`03_CONTEXT_ENGINE_AND_TRANSCRIPT_STORAGE.md`](file:///Users/brightech/.gemini/antigravity/scratch/custom-harness-specs/03_CONTEXT_ENGINE_AND_TRANSCRIPT_STORAGE.md) | Context & Transcripts | `transcript.jsonl` compact schema, pointer truncation, compaction algorithms. |
| [`04_PROGRESSIVE_SKILLS_AND_PLUGIN_SYSTEM.md`](file:///Users/brightech/.gemini/antigravity/scratch/custom-harness-specs/04_PROGRESSIVE_SKILLS_AND_PLUGIN_SYSTEM.md) | Skills & Plugins | Progressive disclosure via YAML frontmatter, plugin manifests, MCP lifecycle. |
| [`05_EXECUTION_SANDBOX_AND_GIT_WORKTREES.md`](file:///Users/brightech/.gemini/antigravity/scratch/custom-harness-specs/05_EXECUTION_SANDBOX_AND_GIT_WORKTREES.md) | Sandboxing & Workspaces | Subagent isolation with Git worktrees, terminal virtualization, supervisor loop. |
| [`06_COMPUTER_USE_AND_DESKTOP_INTEGRATION.md`](file:///Users/brightech/.gemini/antigravity/scratch/custom-harness-specs/06_COMPUTER_USE_AND_DESKTOP_INTEGRATION.md) | Desktop Automation | `@oai/sky` API, macOS Accessibility (`AXUIElement`), ScreenCaptureKit, REPL runner. |
| [`07_GENERATIVE_UI_AND_ARTIFACT_SYSTEM.md`](file:///Users/brightech/.gemini/antigravity/scratch/custom-harness-specs/07_GENERATIVE_UI_AND_ARTIFACT_SYSTEM.md) | Generative UI & Artifacts | Document versioning, metadata schema, sandboxed iframe rendering, live diffs. |
| [`08_BUILD_BLUEPRINT_AND_STARTER_IMPLEMENTATION.md`](file:///Users/brightech/.gemini/antigravity/scratch/custom-harness-specs/08_BUILD_BLUEPRINT_AND_STARTER_IMPLEMENTATION.md) | Complete Implementation | Phased roadmap, project layout, and fully functional runnable Python scaffold. |
| [`09_SEARCH_INDEXING_AND_SQLITE_FTS5_ENGINE.md`](file:///Users/brightech/.gemini/antigravity/scratch/custom-harness-specs/09_SEARCH_INDEXING_AND_SQLITE_FTS5_ENGINE.md) | Long-Term Memory | Embedded SQLite FTS5, byte-offset instant seek into transcripts, BM25 ranking. |
| [`10_PROMPT_CACHING_AND_PREFIX_INVARIANCE.md`](file:///Users/brightech/.gemini/antigravity/scratch/custom-harness-specs/10_PROMPT_CACHING_AND_PREFIX_INVARIANCE.md) | Token Economics & Speed | Achieving 90%+ prompt cache hit rates via strict deterministic prefix invariants. |
| [`11_MULTI_AGENT_ARENA_AND_BATTLE_EVALUATOR.md`](file:///Users/brightech/.gemini/antigravity/scratch/custom-harness-specs/11_MULTI_AGENT_ARENA_AND_BATTLE_EVALUATOR.md) | Competitive Exploration | Antigravity Battle Mode, Rust CellActor pattern, automated scoring and merging. |
| [`12_TOOL_SAFETY_GUARDRAILS_AND_PERMISSIONS.md`](file:///Users/brightech/.gemini/antigravity/scratch/custom-harness-specs/12_TOOL_SAFETY_GUARDRAILS_AND_PERMISSIONS.md) | Security & Permissions | Human-in-the-loop state machine, destructive regex classifiers, path guards. |
| [`13_CLAUDE_COWORK_OPERON_ARCHITECTURE.md`](file:///Users/brightech/.gemini/antigravity/scratch/custom-harness-specs/13_CLAUDE_COWORK_OPERON_ARCHITECTURE.md) | Claude Cowork (Operon) | Bun compiled daemon, Dual-Port origin separation (Port 8000/8001), CIDR guards. |
| [`14_THE_ULTIMATE_HYBRID_HARNESS_SYNTHESIS.md`](file:///Users/brightech/.gemini/antigravity/scratch/custom-harness-specs/14_THE_ULTIMATE_HYBRID_HARNESS_SYNTHESIS.md) | Master Omni-Blueprint | Feature mashup matrix combining all engines into one unified architecture. |
| [`15_HERMES_MULTI_AGENT_AND_TIRITH_SECURITY.md`](file:///Users/brightech/.gemini/antigravity/scratch/custom-harness-specs/15_HERMES_MULTI_AGENT_AND_TIRITH_SECURITY.md) | Hermes Engine | Hierarchical spawn trees, SQLite state machine with WAL, and Tirith security. |
| [`16_PI_MINIMALIST_AGENT_AND_CHORD_RUNTIME.md`](file:///Users/brightech/.gemini/antigravity/scratch/custom-harness-specs/16_PI_MINIMALIST_AGENT_AND_CHORD_RUNTIME.md) | Pi Engine | Ultra-compact (<1k token) prompt, universal `pi-ai` multi-provider layer, and model switching. |
| [`17_AIDER_REPOMAP_AND_ARCHITECT_EDITOR_LOOP.md`](file:///Users/brightech/.gemini/antigravity/scratch/custom-harness-specs/17_AIDER_REPOMAP_AND_ARCHITECT_EDITOR_LOOP.md) | Aider Engine | Tree-sitter PageRank repo map, Architect/Editor dual-model pattern, self-healing. |
| [`18_BROWSER_COMPUTER_USE_AND_CDP_STAGEHAND_ENGINE.md`](file:///Users/brightech/.gemini/antigravity/scratch/custom-harness-specs/18_BROWSER_COMPUTER_USE_AND_CDP_STAGEHAND_ENGINE.md) | Browser Computer Use | Chrome DevTools Protocol (CDP), pruned accessibility trees, Stagehand `act`/`observe`/`extract`. |
| [`19_STATE_GRAPH_ORCHESTRATION_AND_AGENT_HANDOFFS.md`](file:///Users/brightech/.gemini/antigravity/scratch/custom-harness-specs/19_STATE_GRAPH_ORCHESTRATION_AND_AGENT_HANDOFFS.md) | Graph Workflows | LangGraph cyclic StateGraphs, persistent checkpoints for time-travel, and agent handoffs. |
| [`20_INTELLIGENT_MODEL_ROUTING_AND_FAILOVER_CIRCUITS.md`](file:///Users/brightech/.gemini/antigravity/scratch/custom-harness-specs/20_INTELLIGENT_MODEL_ROUTING_AND_FAILOVER_CIRCUITS.md) | Model Routing | LiteLLM / OpenRouter pre-flight intent classification, tiered pools, and circuit breakers. |
| [`21_BYOK_LOCAL_MODELS_AND_OPEN_HARNESS_SYNTHESIS.md`](file:///Users/brightech/.gemini/antigravity/scratch/custom-harness-specs/21_BYOK_LOCAL_MODELS_AND_OPEN_HARNESS_SYNTHESIS.md) | BYOK & Local Models | Ollama/vLLM local inference, UI-TARS visual $(x,y)$ coordinate grounding, and Qwen XML tool adapter. |
