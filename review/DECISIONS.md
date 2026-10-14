# Owner decisions — fill in before (or while) the agent starts

Every item below is a fork the agent should not take on its own. Each has a **default** that the agent must apply if you leave the decision blank, so nothing stalls — but the defaults encode opinions, and a few (D7, D15) materially change the outcome. Write your answer in the `Decision:` line; "default" is a valid answer. Items are grouped by *when* they first matter.

Legend — **Affects:** item IDs in `REMEDIATION_PLAN.md` (v2) / `REMEDIATION_FINDINGS.md` (v1).

---

## Needed before Phase 0–1 (safe monolith)

### D7 · License
- **Question:** Under which license is this repository published? There is currently no `LICENSE` file, so nobody may legally reuse it.
- **Options:** MIT (permissive, simplest) · Apache-2.0 (permissive + patent grant, usual for infra/agent tooling) · AGPL-3.0 (copyleft, forces network-service users to share) · proprietary / all-rights-reserved (keep private).
- **Default:** **none — the agent will open an issue and not add a file.** Nothing else blocks on this, but a plugin ecosystem (D12) is unrealistic without a permissive license.
- **Affects:** HYG-06.
- **Decision:** ______

### D4 · Daemon authentication model
- **Question:** How does the local web studio prove requests come from the user's own page and not from a malicious website or a DNS-rebound host?
- **Options:** (a) per-launch random bearer token embedded in the served page + required header on all non-public routes + Host/Origin allow-list (no cookies; works through SSH tunnels with `--allow-host`) · (b) cookie session + CSRF token (more moving parts, same effect) · (c) Unix socket only (no browser UI without a proxy).
- **Default:** **(a).**
- **Affects:** STOP-01, WEB-01 / SEC-01…03.
- **Decision:** ______

### D1 · What "mock mode" is
- **Question:** Today a keyword-matching "Sovereign Autonomous Engine" answers whenever a provider fails or no key exists, and it executes real tools. What should exist instead?
- **Options:** (a) scripted-only provider for tests + a labelled echo responder for `--provider mock` that never emits tool calls · (b) keep a heuristic demo mode but make it opt-in and tool-free · (c) remove mock mode entirely (tests use recorded fixtures only).
- **Default:** **(a).** Under no option does a mock run tools or activate implicitly.
- **Affects:** STOP-05, PLG-13 / MDL-01.
- **Decision:** ______

### D3 · Where session data lives
- **Question:** `.harness/` (transcripts, SQLite DBs, artifacts) is written inside the user's repository, which is how it ended up in a commit.
- **Options:** (a) keep `<workspace>/.harness/` and add `/.harness/` to `.git/info/exclude` automatically · (b) move to `~/.harness/workspaces/<sha1(abs_path)>/` (cleaner repo, harder to find/ship) · (c) configurable with (a) as default.
- **Default:** **(a).**
- **Affects:** STOP-06, KRN-09, PLG-08 / LOOP-05, ORCH-01.
- **Decision:** ______

### D5 · Architect/editor commit policy
- **Question:** The architect loop currently runs `git add -A` and commits even when the edit failed. What should it do?
- **Options:** (a) never auto-commit; on success request approval and commit only files the loop changed · (b) auto-commit only files it changed, only on green tests, no approval · (c) never commit; leave changes staged.
- **Default:** **(a).**
- **Affects:** STOP-06, PLG-19 / ORCH-01.
- **Decision:** ______

### D8 · Supported platforms
- **Question:** Which platforms must the CLI/daemon, the Swift launcher and macOS-only tools (screenshot) support?
- **Options:** (a) CLI/daemon macOS + Linux, launcher macOS only, screenshot macOS only (hidden elsewhere) · (b) macOS only everywhere · (c) add Windows (WSL-free) to the CLI/daemon scope.
- **Default:** **(a).**
- **Affects:** PLG-21, WEB-08, HYG-03 / TOOL-08.
- **Decision:** ______

### D9 · Approvals in non-interactive calls
- **Question:** `turn.submit` over plain JSON-RPC cannot show an approval card. What happens when the model wants to do something risky?
- **Options:** (a) deny with a clear message unless the caller passed `auto_approve: true` · (b) auto-approve everything for RPC callers · (c) queue the approval and block the call until someone resolves it via the UI.
- **Default:** **(a).**
- **Affects:** KRN-06 / TOOL-02.
- **Decision:** ______

---

## Needed before Phase 2–3 (kernel and plugins)

### D2 · Simulated subsystems: implement or quarantine
- **Question:** MCP bridge, browser CDP engine, UI-TARS visual grounding, battle arena, spawn ledger/state graph are currently simulated. Which should become real in the prototype, and which move to `harness/experimental/` and out of the UI?
- **Options:** (a) implement MCP over stdio (cheap, high value); quarantine the rest · (b) implement MCP + battle arena with real workers · (c) quarantine everything including MCP · (d) custom list: ______
- **Default:** **(a).**
- **Affects:** BRG-02, EXP-01, PLG-21 / SIM-01…05, ORCH-02/04.
- **Decision:** ______

### D6 · First-class providers
- **Question:** Which provider adapters must pass the full conformance kit (native tool calls, streaming, usage, cache hints) for v0.2.0?
- **Options:** (a) OpenAI-compatible (OpenAI, OpenRouter, DeepSeek, Ollama/vLLM) + Anthropic; Gemini best-effort · (b) add Gemini as first-class · (c) OpenAI-compatible only.
- **Default:** **(a).**
- **Affects:** PLG-01, PLG-10, PLG-11, PLG-12 / MDL-03.
- **Decision:** ______

### D10 · In-process third-party plugins
- **Question:** May plugins from outside the repository run inside the harness process (full Python access), or only through process boundaries (MCP for tools, shell hooks for lifecycle, ACP for frontends)?
- **Options:** (a) bridges only by default; in-process behind an explicit setting · (b) in-process allowed when installed via pip (trust the package manager) · (c) in-process always.
- **Default:** **(a).** This is the main thing that keeps "bring any feature" from meaning "run any code".
- **Affects:** KRN-05, SECURITY.md, `MODULAR_ARCHITECTURE.md` §9.
- **Decision:** ______

### D15 · Legacy cutover strategy
- **Question:** When the kernel slice passes S1–S6 via the CLI, do we delete the legacy coordinator/router/daemon immediately, or run both for a while?
- **Options:** (a) hard cutover in one PR (legacy deleted; `harness ui` unavailable until WEB-01 lands a few days later) · (b) keep the legacy daemon behind `--legacy-ui` for one milestone · (c) keep both engines selectable long-term (not recommended: double maintenance, policy drift).
- **Default:** **(a).**
- **Affects:** KRN-12, WEB-01.
- **Decision:** ______

### D16 · Model-facing tool names
- **Question:** Keep the current Antigravity-style names (`view_file`, `write_to_file`, `edit_file`, `run_command`, `manage_task`, `search_codebase`, `search_memory`, `view_skill`) or adopt Claude Code's (`Read`, `Write`, `Edit`, `Bash`, `Grep`, …) so existing hook configs and prompts match verbatim?
- **Options:** (a) keep current names; the hooks bridge maps Claude Code matchers to them · (b) rename to Claude Code names · (c) rename to a neutral `fs_view/fs_write/fs_edit/shell_run/...` scheme.
- **Default:** **(a).** Renaming later is a breaking change for every user prompt and hook.
- **Affects:** PLG-03/04/05/14/15, BRG-01.
- **Decision:** ______

### D13 · Repo map implementation
- **Question:** Aider-style repository map: regex/AST symbol outline (no new dependency) or tree-sitter (accurate, multi-language, heavier install)?
- **Options:** (a) AST/regex default, tree-sitter as optional extra `harness[repomap]` · (b) tree-sitter required · (c) no repo map in v0.2.0.
- **Default:** **(a).**
- **Affects:** PLG-20 / LOOP-07.
- **Decision:** ______

---

## Needed before Phase 4–7 (frontend, distribution, release)

### D11 · Web frontend technology
- **Question:** The studio is one 3,187-line HTML file. Rebuild as plain ES modules (no build step, vendored libraries) or adopt a framework (Preact/Lit/React with a bundler)?
- **Options:** (a) vanilla ES modules + vendored `marked`/`DOMPurify`, no build step · (b) Preact or Lit, vendored, no bundler · (c) React/Vite with a build step.
- **Default:** **(a).** Keeps "pip install and run" true and keeps the sandbox/CSP story simple.
- **Affects:** WEB-02…07.
- **Decision:** ______

### D14 · Web transport
- **Question:** Keep aiohttp (works today, dual-app pattern for the sandbox origin) or switch to FastAPI/Starlette?
- **Options:** (a) keep aiohttp · (b) FastAPI.
- **Default:** **(a).**
- **Affects:** WEB-01.
- **Decision:** ______

### D12 · Plugin distribution
- **Question:** How do users install plugins?
- **Options:** (a) Python entry points (`pip install harness-<name>`) + plugin directories (`~/.harness/plugins/`, `<workspace>/.harness/plugins/`), catalogue as a Markdown file · (b) also a hosted registry/index service · (c) git-URL installer (`harness plugin install <git-url>`) in addition to (a).
- **Default:** **(a).** (c) is a reasonable later addition.
- **Affects:** KRN-05, SDK-01/02/03.
- **Decision:** ______

---

## Summary table

| ID | Topic | Default | Decision |
|---|---|---|---|
| D1 | Mock mode | scripted-only, tool-free | |
| D2 | Simulated subsystems | implement MCP; quarantine rest | |
| D3 | Session data location | in-workspace + git exclude | |
| D4 | Auth | per-launch token + Host/Origin allow-list | |
| D5 | Architect commits | never auto; approval; touched files only | |
| D6 | Providers | OpenAI-compatible + Anthropic first-class | |
| D7 | License | **no default — owner must choose** | |
| D8 | Platforms | macOS + Linux CLI/daemon; launcher macOS | |
| D9 | Non-interactive approvals | deny unless `auto_approve` | |
| D10 | In-process third-party plugins | no (bridges only) | |
| D11 | Web frontend tech | vanilla ES modules, vendored libs | |
| D12 | Plugin distribution | entry points + directories | |
| D13 | Repo map | AST/regex; tree-sitter optional | |
| D14 | Web transport | aiohttp | |
| D15 | Legacy cutover | hard cutover | |
| D16 | Tool names | keep current; alias map in hooks bridge | |
