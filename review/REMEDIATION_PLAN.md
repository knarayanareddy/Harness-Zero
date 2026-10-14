# Harness-Zero — Remediation & Build Plan v2 (kernel-first)

**Baseline commit:** `a0afc75` on `main` · **Supersedes:** v1 work order (now `REMEDIATION_FINDINGS.md`, kept as the per-finding reference) · **Design reference:** `MODULAR_ARCHITECTURE.md` · **Evidence:** `CODE_REVIEW.md`

**What changed from v1.** v1 patched the monolith in place. v2 keeps v1's Phase 0 (hygiene) and the security fixes as an immediate "stop the bleeding" phase, then builds the modular kernel and SDK as a vertical slice beside the legacy code, ports the salvageable subsystems as first-party plugins against conformance kits, cuts the CLI and web studio over to the event stream, adds protocol bridges (Claude Code-compatible hooks, MCP client/server, ACP), quarantines everything simulated, and ships plugin tooling so a user's own agent can port features from other harnesses. Every v2 item lists the v1 finding IDs it **absorbs**; their repros, root causes, acceptance criteria and tests stay authoritative.

---

## 0. How to use this document

- Conventions, ground rules and environment are identical to `REMEDIATION_FINDINGS.md` §0 (IDs in commits/PRs, one item per PR, hermetic tests, never fake, never widen the workspace, no silent `except`).
- **Architecture rules are binding:** `MODULAR_ARCHITECTURE.md` §0 principles, §9.4 boundary tests, §15 anti-patterns. A PR that violates a boundary test is not mergeable regardless of its feature value.
- Severity: `P0` blocks a safe prototype · `P1` blocks a working prototype · `P2` required for honesty/maintainability · `P3` optional. Size: `S` < 1 h · `M` 1–4 h · `L` 4–12 h · `XL` > 12 h.
- When an item says "Decision Dn" and the owner has not answered, apply the stated default and record it in the PR description.

---

## 1. Definition of done

### 1.1 Product scenarios (unchanged from v1 — `REMEDIATION_FINDINGS.md` §1.3)
S1 safe daemon · S2 configure & converse with real usage/cost · S3 surgical edit, no commit · S4 approval gate (deny/approve) · S5 workspace jail · S6 background wake-up without polling · S7 cross-session memory · S8 architect loop with file-scoped, approved commit · S9 resume after restart · S10 hermetic CI · S11 honest surfaces.

### 1.2 Modularity scenarios (new)
| # | Scenario | Steps | Expected |
|---|---|---|---|
| **S12** | Local plugin | Create `~/.harness/plugins/hello/` with `harness_plugin.toml` + a `Tool` that writes `hello.txt`; `harness plugin list` | Listed with `trust=local`, `capabilities=[fs:write]`; model can call it; calling it with `../hello.txt` is denied by the gate (not by the plugin). |
| **S13** | Claude Code hook reuse | Put an existing `PreToolUse` hook (exit 2 on `rm`) under `plugins.shell-hooks.hooks`; ask the agent to delete a directory | Tool blocked before execution; reason from stderr shown as a `notice` card and returned to the model. |
| **S14** | MCP server | Configure the fixture stdio echo server; ask the agent to use it | Tools appear as `mcp__echo__*` with real schemas; call round-trips; `harness plugin info mcp-client` shows `connected`; killing the server shows `error`, not "connected". |
| **S15** | Conformance | `harness plugin test --all` | Green for every builtin plugin; `tests/kernel/test_import_boundaries.py` passes; `harness events schema` matches `docs/events.schema.json`. |
| **S16** | Provider swap mid-session | Two tool-using turns on `openai_compat`, switch to `anthropic` in settings, third turn | Third turn succeeds (history with `tool_use`/`tool_result` converts; no 400s); usage attributed to the right provider. |
| **S17** | Cache invariance | Two consecutive turns; inspect `context.assembled` | STATIC blocks byte-identical; live (Anthropic): second turn reports `cache_read_tokens > 0`. |
| **S18** | Scaffold-to-green | `harness plugin new --kind context my_rules`; implement a 10-line `ContextSource`; `harness plugin test ./my_rules` | Green on first run after implementing `blocks()`; appears in the next turn's `context.assembled`. |

**Prototype v0.2.0 = S1–S18 pass + §6 gate passes + every P0/P1 below closed or deferred in writing by the owner.**

---

## 2. Decisions (D1–D9 unchanged from v1; new D10–D16)

| ID | Question | Default |
|---|---|---|
| D1–D9 | mock policy, quarantine, session storage, auth, architect commits, providers, license, platforms, non-interactive approvals | as in `REMEDIATION_FINDINGS.md` §2 |
| **D10** | Are third-party plugins allowed in-process? | **No.** Third-party = bridges (MCP, shell hooks, ACP). `kernel.plugins.allow_inprocess_third_party=false`. |
| **D11** | Web frontend technology | Vanilla ES modules, no build step, vendored `marked` + `DOMPurify`; components are plain functions in `cards.js`. (Alt: Preact/Lit vendored.) |
| **D12** | Plugin distribution | Python entry points (`harness.plugins`) + plugin directories. No registry service; `docs/PLUGIN_CATALOG.md` is the catalogue. |
| **D13** | Repo map implementation | Default AST/regex symbol outline; `tree-sitter` as optional extra `harness[repomap]`. |
| **D14** | Web transport | Keep aiohttp (dual-app pattern already works); SSE for events; ACP later. |
| **D15** | Legacy cutover | **Hard cutover** once the kernel slice passes S1–S6 via the CLI (KRN-12). No long dual-engine period; legacy modules deleted in the same PR. |
| **D16** | Model-facing tool names | Keep current names (`view_file`, `write_to_file`, `edit_file`, `run_command`, `manage_task`, `search_codebase`, `search_memory`, `view_skill`). `shell_hooks` maps Claude Code matchers (`Read/Write/Edit/Bash/Grep`) to them. |

---

## 3. Execution order & milestones

```
Phase 0  HYG-01 … HYG-07                                   hygiene & hermetic tests          (M0 prerequisites)
Phase 1  STOP-01 … STOP-08                                  close every CRITICAL on the monolith   → M0 "safe monolith"
Phase 2  KRN-01 → 02 → 03 → 04 → 05 → 06 → 07 → 08 → 09 → 10 → 11   SDK + kernel + kits + CLI frontend
Phase 3a PLG-01 … PLG-09                                    slice plugins                      → M1 "slice passes S1–S6 via CLI"
         KRN-12                                             cutover, delete legacy             → M2
Phase 3b PLG-10 … PLG-22                                    remaining ports
Phase 4  WEB-01 … WEB-08                                    studio on the event stream         → M3 "S1–S11 in the web UI"
Phase 5  BRG-01 … BRG-04                                    hooks, MCP client/server, ACP      → M4 "S12–S14"
Phase 6  EXP-01 … EXP-02                                    quarantine & deletions
Phase 7  SDK-01 … SDK-03, DOC-01 … DOC-03, REL-01           tooling, catalogue, docs, release  → M5 "v0.2.0, S15–S18"
```
Indicative effort: Phase 0 ≈ 1 day · Phase 1 ≈ 2 days · Phase 2 ≈ 5–7 days · Phase 3a ≈ 4–5 days · Phase 3b ≈ 5–6 days · Phase 4 ≈ 4–5 days · Phase 5 ≈ 4 days · Phase 6 ≈ 0.5 day · Phase 7 ≈ 3 days (agent-days of focused work, sequential).

PR batching: `HYG-01+02`, `HYG-03+04+05`, `STOP-01`, `STOP-02`, `STOP-03+04`, `STOP-05+06`, `STOP-07+08`, then one PR per KRN/PLG/WEB/BRG item.

---

## 4. Task catalogue

### Phase 0 — Hygiene (unchanged)
**HYG-01 … HYG-07** exactly as specified in `REMEDIATION_FINDINGS.md` §4 Phase 0. Do them on the current tree now; all of them survive the migration (the `conftest.py` fixtures, `HARNESS_CONFIG_DIR`, ruff/CI, `.gitignore`, `packaging/`, dataclass `TurnResult`). HYG-07's `TurnResult` becomes `sdk.types.TurnResult` in KRN-01.

---

### Phase 1 — STOP: close every CRITICAL on the monolith (all carry over 1:1)

Rationale: these are small, they close every CRITICAL finding, and each lands in code that moves unchanged into the new layout (middlewares → `frontends/web/server.py`; sanitiser → `cards.js`; jail → `kernel/jail.py`). After Phase 1 the monolith is safe to keep running while the kernel is built beside it.

#### STOP-01 · P0 · M · Daemon auth, CSRF, Host — *absorbs SEC-01, SEC-02, SEC-03*
Implement the three middlewares and token embedding exactly per findings; write `daemon.json` (0600); re-implement `harness url` from it. **Acceptance:** S1. **Tests:** `tests/test_auth.py` iterating all routes.

#### STOP-02 · P0 · M · XSS, CSP, vendored JS, sandbox origin — *absorbs SEC-04, SEC-05, UI-02*
`renderMarkdown()` with vendored DOMPurify; CSP on both origins; `.html` artifacts only via sandboxed iframe whose origin comes from `/health`; remove CDN + Google Fonts. **Acceptance:** scripted XSS payload inert; UI works offline.

#### STOP-03 · P0 · S · Traversal — *absorbs SEC-06*
`session_id` regex, atomic `switch_session`, artifact/media name validation. Place the validators in a new `harness/kernel/validators.py` (first kernel file) and import from the legacy daemon.

#### STOP-04 · P0 · S · Secrets, uploads, fabricated state — *absorbs SEC-07, SEC-08, SEC-09*
0600 config writes, delete `_load_hermes_env`, `client_max_size`, delete fake `account`, MCP status `not_configured`.

#### STOP-05 · P0 · M · No silent mock; no hardcoded path write — *absorbs MDL-01 (minimal), HYG-03 (router part)*
Remove the final mock fallback and keyword heuristics from `router.py`; missing key / provider error → `ProviderError` surfaced to CLI and SSE; `--provider` default `None`; `--provider mock` honoured and tool-free. The full per-provider split happens in PLG-01/10/11/12.

#### STOP-06 · P0 · S · Never commit the dirty tree — *absorbs ORCH-01 (emergency subset), LOOP-05 (exclude)*
`auto_commit=False`; never commit when `success` is False; `.git/info/exclude` gets `/.harness/`; targets must be explicit (no `glob("*.py")[:10]`). Full rewrite in PLG-19.

#### STOP-07 · P0 · M · Jail the four tools — *absorbs TOOL-01*
Create `harness/kernel/jail.py` (`WorkspaceJail`, final API per architecture §4) and apply it in `view_file`, `write_to_file`, `edit_file`, `run_command Cwd`, `DiffApplier`, `/file/*`. **Acceptance:** S5 against the legacy coordinator.

#### STOP-08 · P0 · S · Executor emergency hardening — *absorbs TOOL-05 (1–5)*
Env scrub, `DEVNULL` stdin, chunked reads, kill race, no `mkdir`. Tirith wiring and output sanitisation come with PLG-17/KRN-06.

**M0 gate:** every CRITICAL in `CODE_REVIEW.md` §3 has a passing regression test; `HOME=$(mktemp -d) pytest -q` green.

---

### Phase 2 — KRN: SDK, kernel, kits, CLI frontend

#### KRN-01 · P1 · M · SDK types, errors, manifest — *arch §3, §8; absorbs HYG-07*
`harness/sdk/{__init__,types,errors,manifest}.py`. All dataclasses with `to_dict/from_dict`; `PluginManifest.load(path)` validates names, `api_version`, `provides`, `capabilities`, compat ranges. **Tests:** round-trips; invalid manifests produce actionable messages; `SDK_API_VERSION == "1"`.

#### KRN-02 · P1 · M · Contracts, context interfaces, events, cards — *arch §4, §6, §7*
`contracts.py` (runtime-checkable Protocols), `context.py`, `events.py` (dataclass per event + envelope + `schema()`), `cards.py` (kinds + validators + `fallback()`), CLI `harness events schema`. **Tests:** every event serialises; schema generation deterministic; card validators reject missing required fields; `docs/events.schema.json` committed and drift-checked.

#### KRN-03 · P1 · S · EventBus — *arch §6 semantics*
Fan-out, bounded queues, delta coalescing, `interactive` flag, `lagging` warning. **Tests:** 10 000 deltas into a slow subscriber coalesce without dropping non-delta events; ordering preserved.

#### KRN-04 · P1 · M · SettingsStore + Secrets — *arch §5.7; absorbs TOOL-06, SEC-07*
Namespaced JSON, schema registry (`kernel.*` + `plugins.<name>.*`), validation on write, atomic replace, change subscriptions, `schema_version` + migrations; `Secrets` with 0600 file, `has()` only, env precedence off by default. **Tests:** invalid write rejected; subscription fires; migration from legacy `~/.harness/config.json` imports provider/model/keys once.

#### KRN-05 · P1 · L · Registry + boundary tests — *arch §8, §9*
Discovery (builtin → entry points → `~/.harness/plugins` → workspace, trust tiers), manifest validation, dependency order (`requires`), capability clamping, enable/disable, collision errors, `plugin.loaded/error` events, `PluginContext` implementation. Add `tests/kernel/test_import_boundaries.py` and `test_no_direct_subprocess.py` (AST scans). **Tests:** wrong `api_version` refused with message; duplicate tool names fail with both origins; clamped capability raises `CapabilityDenied`; a throwing `setup()` does not abort boot.

#### KRN-06 · P0 · L · Jail + PolicyGate + ApprovalBroker — *arch §5.3; absorbs TOOL-01, TOOL-02, TOOL-05 (6), D9*
Move `kernel/jail.py` (STOP-07) under the gate; implement steps 1–9 incl. JSON-Schema argument validation, `workspace-path` normalisation, policy combination, permission modes hook-in, approvals (events, timeout, non-interactive rule), capability-gated `ToolContext`, timeouts, exception capture, output sanitisation (`sanitize_output` ported from Tirith as a kernel utility), result truncation with full text persisted. **Tests:** `test_gate_invariants.py` (every invariant listed in §5.3); approval deny/approve/timeout; plan mode; bypass not grantable via manifest.

#### KRN-07 · P1 · M · ContextAssembler + Compaction — *arch §5.4, §5.5; absorbs LOOP-02, CONF-04*
Parallel source collection with timeouts; tier ordering; ephemeral TURN message; history conversion to `ToolUseBlock`/`ToolResultBlock` pairs from the last compaction; token estimator; compaction via provider with the fixed prompt; `PRE_COMPACT` hooks. **Tests:** invariance across two assemblies; compaction appends exactly one summary at the boundary and the next request starts with it; a failing source is skipped and logged.

#### KRN-08 · P1 · L · TurnRunner, KernelServices, Inbox, breaker, spend cap — *arch §5.2, §5.6, §5.8; absorbs LOOP-01, LOOP-04, MDL-06 (kernel), MDL-07*
`call_model` streams and accumulates, records usage/cost, enforces `spend_cap_usd`; `execute_tools` parallel for READ-only batches; `BackgroundInbox` + `wait_for_wakeup`; per-(provider, model) breaker (closed/open/half-open) raising `ProviderError(kind="unavailable")` when open; cancellation semantics; `spawn_child` depth-limited (max 2) with its own transcript sub-dir. **Tests (ScriptedProvider):** two-tool turn; wake-up consumed without polling; cancel mid-stream records notification; breaker opens after N failures; spend cap stops the turn.

#### KRN-09 · P1 · M · Session + Commands — *arch §5.1; absorbs SEC-06 (final home), LOOP-05/06 wiring*
`Session` (dirs, stores, exclude entry, locks, hooks `SESSION_START`), `Commands` (`submit_turn`, `cancel_turn`, `resolve_approval`, `sessions.*`, `settings.get/set`, `models.list`, `files.read/write` through the gate as the *user* principal, `plugins.*`, `approvals.list`, `economics`), `TurnInProgress` on concurrent submit. **Tests:** session id validation; concurrent submit → 409-equivalent; restart → sessions listed and history reloaded.

#### KRN-10 · P1 · L · Conformance kits + ScriptedProvider — *arch §11*
`harness/sdk/testing/*` as pytest plugins (`pytest11` entry point), `recorded`/`live` modes, `ScriptedProvider` with request assertions. **Tests:** kits run against deliberately broken fixture plugins and fail with the expected messages (meta-tests).

#### KRN-11 · P1 · M · CLI frontend on the kernel — *arch §1, §7*
`frontends/cli/` renders events and all card kinds (fallback included), prompts for approvals and forms, `harness run|interactive --kernel` (temporary flag until KRN-12), `-v/--log-file`, `plugin list|info|enable|disable`. **Tests:** `FrontendKit` snapshot for every card kind; approval prompt path.

---

### Phase 3a — PLG: slice plugins (minimum to run the product via CLI)

Each plugin: directory per architecture §2, `harness_plugin.toml`, `settings.schema.json` when configurable, `README.md`, `tests/test_conformance.py` (kit subclass) + feature tests.

#### PLG-01 · P1 · L · `providers/openai_compat` — *absorbs MDL-01..06 (OpenAI/OpenRouter/DeepSeek parts), MDL-08*
One adapter parameterised by `base_url`/key name (OpenAI, OpenRouter, DeepSeek; Ollama's OpenAI endpoint also works here). Native `tool_calls`/`role:"tool"`; streaming deltas incl. tool-call argument deltas; usage with `cached_tokens`; reasoning-model params (MDL-04); `list_models` via `/v1/models` with 24 h cache; error mapping; shared `httpx.AsyncClient` with `Timeout(connect=10, read=settings.request_timeout_s, write=30, pool=10)`; fallback only per MDL-05 policy. **Tests:** ProviderKit recorded fixtures (`tests/fixtures/providers/openai_compat/*.json`).

#### PLG-02 · P1 · M · `executors/local` — *absorbs TOOL-05 (final)*
`ExecutorKit` green; `Process` with streamed output, `send_input`, `kill`, `wait`; `isolation="none"`.

#### PLG-03 · P1 · M · `tools/fs` (`view_file`, `write_to_file`, `edit_file`) — *absorbs TOOL-01, TOOL-07 (edit part)*
`workspace-path` formatted schema fields; `edit_file` uses file-headed SEARCH/REPLACE blocks (shared `diffs.py`) and returns `changed_files`; emits `diff` cards; `write_to_file` is a plain file write (artifact semantics move to PLG-16).

#### PLG-04 · P1 · M · `tools/shell` (`run_command`, `manage_task`) — *absorbs TOOL-03, LOOP-01 (tool side)*
Runs via `ctx.executor`; streams `tool.output.delta`; `WaitMsBeforeAsync` → `ctx.background()`; `manage_task status|wait|kill|send_input`; declared `risk_class=EXEC`.

#### PLG-05 · P1 · S · `tools/search` (`search_codebase`) — *absorbs HYG-03 (rg), §6.6*
`rg` if present else Python fallback with identical regex semantics; results capped and paginated.

#### PLG-06 · P0 · M · `policy/{workspace_jail, risk_classifier, permission_modes}` — *absorbs TOOL-02 (modes), TOOL-04*
`workspace_jail` (order 10) normalises path arguments and denies escapes with `risk=DESTRUCTIVE`; `risk_classifier` (order 50) ports/extends guardrail patterns and raises risk for shell commands (table-driven tests: 30 destructive / 20 benign); `permission_modes` (order 900) implements `plan|ask|allow|bypass` per §5.3 step 4.

#### PLG-07 · P1 · M · `context/{identity, tool_guidance, history, instructions_files}` — *absorbs LOOP-07 (part), CONF-04*
`identity` STATIC (ported Tier-1 text, no vendor claims); `tool_guidance` STATIC (usage rules only; XML protocol block only when provider lacks native tools — emitted by the ollama plugin, not here); `history` is kernel-internal (not a plugin) — this item wires it; `instructions_files` STATIC from `AGENTS.md`/`CLAUDE.md`/`.harness/INSTRUCTIONS.md` root→cwd chain.

#### PLG-08 · P1 · M · `stores/{jsonl_transcript, cost_ledger}` — *absorbs LOOP-05 (tolerant reads), MDL-06 (ledger)*
Transcript keeps the full/compact dual-log and byte offsets; adds `call_id/tool_name/is_error`; `list_sessions` from an index file; cost ledger with per-provider pricing table (`None` = unknown) persisted to `economics.json`.

#### PLG-09 · P1 · M · `strategies/single_loop` — *arch §5.2*
The default loop via `KernelServices` only; STOP-hook re-entry (max 2); wake-up wait policy. **Tests:** StrategyKit + S6 scripted.

#### KRN-12 · P1 · L · Cutover and legacy deletion — *Decision D15*
Preconditions: S1–S6 pass via `harness run --kernel` with PLG-01..09. Then: CLI defaults to the kernel; delete `core/coordinator.py`, `core/daemon.py` (web frontend is rebuilt in Phase 4 — until WEB-01 lands, `harness ui` prints "studio unavailable during migration"; **or** keep legacy daemon alive behind `--legacy-ui` for one milestone, owner's call, default: delete and land WEB-01 next), `models/router.py`, `models/prefix_cache.py`, `sandboxing/{executor,guardrails,diff_applier}.py` (ported), legacy tests. Update `pyproject` (`harness.plugins` entry points for builtins are not needed — builtin discovery is by directory). **Gate:** full hermetic suite green; boundary tests green; `grep -rn "coordinator\|HarnessDaemon" harness/ tests/` empty.

---

### Phase 3b — PLG: remaining ports

#### PLG-10 · P1 · L · `providers/anthropic` — *absorbs MDL-03 (Anthropic), MDL-04*
`system` as content blocks with `cache_control` at tier boundaries (≤ 4 breakpoints); `tool_use`/`tool_result`; `thinking` blocks when enabled; `cache_read/creation` usage; streaming (`content_block_delta`, `input_json_delta`). Live kit check for S17.

#### PLG-11 · P2 · M · `providers/gemini` — *absorbs MDL-03 (Gemini)*
`systemInstruction`, `functionDeclarations`, `functionCall/functionResponse`, header key, role merging, `usageMetadata`. Best-effort (D6).

#### PLG-12 · P1 · M · `providers/ollama` — *absorbs local provider + XML fallback*
Native tools first; on 400 retry with XML `<tool_call>` protocol (parser ported from `reasoning_stream.py`), emitting its own `tool_guidance` block; `/api/tags` for models.

#### PLG-13 · P1 · S · Delete heuristic mock; `providers/mock` = `ScriptedProvider` registration — *absorbs MDL-01 (final), D1*
`--provider mock` registers a `ScriptedProvider` that echoes `[mock]` text with no tool calls unless a script file is supplied (`--mock-script path.json`).

#### PLG-14 · P1 · M · `stores/fts5_search` + `tools/memory` (`search_memory`, `remember`) + `context/memories` — *absorbs LOOP-06, LOOP-07*
Workspace-scoped `memory.db`, `scope` param default `workspace`, re-index from transcripts on first start, memories context SESSION block from `remember` entries.

#### PLG-15 · P1 · S · `context/skills_catalog` + `tools/skills` (`view_skill`) — *absorbs registry port*
Progressive disclosure preserved; `builtin/skills` + `<workspace>/.harness/skills`.

#### PLG-16 · P1 · M · `stores/fs_artifacts` + `tools/artifacts` (`create_artifact`) — *absorbs TOOL-07, SEC-05/06 (store side)*
Validated names; manifest with `user_facing`/`request_feedback`; `artifact` cards; HTML never rendered on the API origin (served by WEB-06).

#### PLG-17 · P1 · S · `policy/tirith` — *absorbs TOOL-05 (6), Tirith wiring*
`audit_command` as a Policy (order 60): BLOCK → deny, WARN → raise risk to DESTRUCTIVE; network targets extended with RFC1918/127.0.0.0/8/169.254/16; output sanitisation already in the gate.

#### PLG-18 · P2 · M · `executors/worktree` — *absorbs ORCH-03*
`isolation="worktree"`; base commit recorded; prune/stale handling; async git; used by `spawn_child(isolation="worktree")`.

#### PLG-19 · P1 · L · `strategies/architect_editor` — *absorbs ORCH-01*
Architect/editor roles from settings; file-headed diffs via `fs` tool (`edit_file`) so edits are jailed, policy-checked and carded; test loop via `run_command`; on green → `ask("commit", …)` → `git add -- <changed_files>`; refuses when index not clean; result `changed_files`. UI/CLI collect `files` + `test_command` (`form` card).

#### PLG-20 · P2 · M · `context/repo_map` — *absorbs LOOP-07 (repo map), D13*
SESSION block ≤ `max_tokens`; `fingerprint()`; optional tree-sitter extra; off for > 2 000 files unless forced.

#### PLG-21 · P2 · S · `tools/screenshot_macos` — *absorbs TOOL-08, SIM-03*
Darwin-only registration; `take_screenshot` returns an `ImageBlock`; no visual-grounding stub.

#### PLG-22 · P3 · M · `hooks/checkpoints` (Cline-style) — *new*
Shadow repo snapshots before WRITE/EXEC tools; `checkpoint_restore` tool (DESTRUCTIVE); `diff` cards. Serves as the reference `Hook` plugin and recipe §13.2.

---

### Phase 4 — WEB: studio on the event stream (`harness/frontends/web`)

#### WEB-01 · P1 · L · Server = Commands + SSE — *absorbs daemon routes, SEC-01..03 (moved), LOOP-04, UI-09*
`server.py`: middlewares from STOP-01; `GET /events` long-lived SSE of the EventBus (heartbeat 15 s, `Last-Event-ID` replay of the last 500); `POST /commands/<name>` JSON → `Commands`; `/health`; static serving; sandbox app on the second port with CSP from STOP-02. No route logic beyond translation.

#### WEB-02 · P1 · L · Split `index.html` into ES modules — *absorbs UI-01, D11*
`api.js` (token wrapper), `events.js` (SSE client, reconnection), `cards.js` (renderers + fallback), `app.js` (timeline/state), `settings.js`, `styles.css`, `vendor/{marked,dompurify}`; no inline scripts → CSP `script-src 'self'`.

#### WEB-03 · P1 · M · Timeline from events — *absorbs UI-05 (remove fake streaming)*
Render `model.text.delta` live; tool cards from `tool.call/decision/result`; `tool.output.delta` into a terminal pane; `task.*`; `compaction.*`; `cost.updated`; `turn.*` states; no artificial delays.

#### WEB-04 · P0 · M · Approvals & forms — *absorbs UI-03, CONF-03*
`approval` cards → `resolve_approval`; `form` cards → `form.submit`; Proceed banner only for `artifact.request_feedback`; non-interactive detection (`interactive=true` subscriber flag).

#### WEB-05 · P1 · M · Settings generated from schemas — *absorbs UI-06, TOOL-06 (UI side)*
Tabs = schema groups (`x-ui.group`); widgets from `x-ui.widget`/types; secrets fields write to `Secrets` and display `has()` only; no hand-written decorative controls remain.

#### WEB-06 · P1 · S · Models, modes, sandbox origin — *absorbs UI-04, UI-07, SEC-05 (UI side)*
Model picker from `models.list`; strategy picker from registry (`single_loop`, `architect_editor`); artifact iframes use the sandbox origin from `/health`.

#### WEB-07 · P2 · S · Context inspector & economics — *absorbs UI-08, S17*
Panel showing `context.assembled` blocks with stability and tokens; economics from `cost.updated` with "unknown pricing" state.

#### WEB-08 · P2 · S · Launcher — *absorbs HYG-03 (launcher), CONF-10*
Workspace picker, remembered workspaces, no `$HOME` default; passes `--allow-host` only when needed; remove `NSAllowsArbitraryLoads` if the studio is local-only.

---

### Phase 5 — BRG: bridges

#### BRG-01 · P1 · M · `bridges/shell_hooks` (Claude Code-compatible) — *arch §10*
Config format, stdin payloads, exit-code semantics, stdout JSON, matcher aliases (D16), timeouts, `hook.fired` events. **Tests:** HookKit + fixture scripts for block/mutate/context. **Acceptance:** S13.

#### BRG-02 · P1 · L · `bridges/mcp_client` — *absorbs SIM-01, SEC-09 (status), CONF-09*
stdio JSON-RPC (`initialize` → `notifications/initialized` → `tools/list` → `tools/call`, `resources/*`, `prompts/*`), restart with backoff, real status, namespaced `ToolSpec`s with annotation→risk mapping, `read_resource` tool, prompts as slash-commands; servers only from settings (none by default). **Tests:** fixture `tests/fixtures/mcp_echo_server.py`; disconnect → `error` status. **Acceptance:** S14.

#### BRG-03 · P2 · M · `bridges/mcp_server`
Expose registered tools (policy-filtered, `expose[]` allow-list) over stdio so other hosts can use Harness tools and policies. **Tests:** round-trip with the client bridge against itself.

#### BRG-04 · P3 · L · `bridges/acp`
Agent Client Protocol frontend over stdio for editors; permission requests ↔ approvals. Behind `harness acp`.

---

### Phase 6 — EXP: quarantine & deletions

#### EXP-01 · P1 · S · Quarantine — *absorbs ORCH-02, ORCH-04, SIM-02, SIM-03*
Move `battle_arena.py`, `spawn_ledger.py`, `state_graph.py`, `concurrency_gate.py`, `browser/stagehand_engine.py` (as `browser_cdp.py`, mock fallbacks removed), to `harness/experimental/` with a README stating status; remove their UI/skill/README references; delete `predict_visual_click`.

#### EXP-02 · P1 · S · Deletions — *absorbs SIM-04, SIM-05, Appendix B of findings*
Delete `scripts/comprehensive_features_verification.py`, legacy login-token code, fake settings, hardcoded model lists, artificial sleeps, dead breaker branches.

---

### Phase 7 — SDK tooling, docs, release

#### SDK-01 · P1 · M · `harness plugin new` scaffolder
Templates per kind (manifest, `plugin.py` with the Protocol stubbed, `settings.schema.json`, README skeleton, `tests/test_conformance.py` wired to the right kit). **Acceptance:** S18.

#### SDK-02 · P1 · M · `harness plugin test|doctor|list|info|enable|disable`
`test` runs the plugin's tests with the kits in `recorded` mode (`--live` opt-in); `doctor` reports boundary violations, deprecated usages, missing manifest fields, capability over-requests. **Acceptance:** S15.

#### SDK-03 · P1 · M · Porting recipes & catalogue
`docs/PORTING_RECIPES.md` (expanded §13 with code), `docs/PLUGIN_CATALOG.md` (every builtin: kind, capabilities, settings, status), `docs/architecture/` (contracts rendered from docstrings).

#### DOC-01 · P1 · M · README & docs honesty — *absorbs DOC-01 (v1)*
Status table per feature (Implemented / Experimental / Planned), modular architecture overview, security model summary, relative links (52 absolute links removed), correct counts, `harness ui` documented.

#### DOC-02 · P1 · S · `SECURITY.md` — *absorbs DOC-02 (v1)*
Threat model incl. plugin trust tiers and what in-process plugins can do.

#### DOC-03 · P2 · S · `CHANGELOG.md` — *absorbs DOC-03 (v1)*

#### REL-01 · P2 · S · Release `0.2.0`
Version bump, CI matrix (3.11/3.12 × macOS/Linux), `harness plugin test --all` in CI, events schema drift check, hermetic-home check, tag.

---

## 5. Where each v1 finding lands

| v1 ID | v2 home |
|---|---|
| HYG-01…07 | Phase 0 (unchanged); HYG-07 → KRN-01 |
| SEC-01/02/03 | STOP-01 → WEB-01 |
| SEC-04/05 | STOP-02 → WEB-02, PLG-16 |
| SEC-06 | STOP-03 → KRN-09 |
| SEC-07/08/09 | STOP-04 → KRN-04, WEB-01, BRG-02 |
| TOOL-01 | STOP-07 → KRN-06, PLG-03/06 |
| TOOL-02 | KRN-06, PLG-06, WEB-04 |
| TOOL-03 | PLG-04 |
| TOOL-04 | PLG-06 |
| TOOL-05 | STOP-08 → PLG-02, KRN-06, PLG-17 |
| TOOL-06 | KRN-04, WEB-05 |
| TOOL-07 | PLG-03, PLG-16 |
| TOOL-08 | PLG-21 |
| MDL-01 | STOP-05 → PLG-13 |
| MDL-02/04/05/08 | PLG-01/10/11/12 |
| MDL-03 | KRN-07 (history blocks) + PLG-01/10/11/12 |
| MDL-06 | KRN-08 + PLG-08 |
| MDL-07 | KRN-08 |
| LOOP-01 | KRN-08 + PLG-04 + PLG-09 |
| LOOP-02 | KRN-07 |
| LOOP-03 | KRN-07 (media → `ImageBlock`) + providers |
| LOOP-04 | KRN-08/09 + WEB-01 |
| LOOP-05 | STOP-06 → KRN-09, PLG-08 |
| LOOP-06 | PLG-14 |
| LOOP-07 | PLG-07, PLG-14, PLG-20 |
| LOOP-08 | KRN-11 (`-v`), `log` events everywhere |
| ORCH-01 | STOP-06 → PLG-19 |
| ORCH-02/04 | EXP-01 (+ `spawn_child` in KRN-08) |
| ORCH-03 | PLG-18 |
| UI-01…09 | WEB-02…07 (UI-02 also STOP-02) |
| SIM-01 | BRG-02 |
| SIM-02/03 | EXP-01, PLG-21 |
| SIM-04/05 | EXP-02 |
| DOC-01/02/03 | DOC-01/02/03 |
| CONF-01…13 | resolved structurally: CONF-01 (PLG-16/WEB-06), 02 (KRN-09/PLG-19), 03 (KRN-06/WEB-04), 04 (KRN-07), 05 (STOP-01), 06 (STOP-02/KRN-04), 07 (STOP-05/PLG-13), 08 (HYG-04/PLG-13), 09 (PLG-14/BRG-02), 10 (WEB-08), 11 (KRN-06/KRN-04), 12 (KRN-06 tests), 13 (KRN-07) |

---

## 6. Verification gate v2 (run before every merge; all in CI)

```bash
export HOME=$(mktemp -d) HARNESS_CONFIG_DIR=$HOME/.harness
pytest -q                                  # kernel + integration + plugin tests, recorded mode
ruff check .
pytest -q tests/kernel/test_import_boundaries.py tests/kernel/test_no_direct_subprocess.py tests/kernel/test_gate_invariants.py
harness plugin test --all                  # conformance kits for every builtin plugin
harness events schema | diff - docs/events.schema.json
pytest -q tests/e2e                        # S1–S18 scripted (ScriptedProvider); live variants with HARNESS_LIVE=1
test -z "$(git status --porcelain)"        # tests wrote nothing into the repo
# security probes exactly as REMEDIATION_FINDINGS.md §7 step 2 (401/415/421/400, CSP on sandbox)
grep -rn "brightech\|Sovereign\|\"connected\"" harness/ tests/ README.md | grep -v experimental   # expect empty
```

---

## 7. Migration risks and mitigations

| Risk | Mitigation |
|---|---|
| Big-bang rewrite stalls with nothing shippable | Phase 1 makes the monolith safe first (M0); the slice (Phase 2–3a) is judged only by S1–S6; hard cutover gate (KRN-12) is binary. |
| Agent "improves" the kernel while porting plugins | Boundary tests + `harness plugin doctor` in CI; kernel changes require a KRN-labelled PR. |
| Over-abstraction | Second-implementation rule (§0.5 of the architecture); contracts frozen at `api_version=1` after KRN-12; additions only. |
| Cache efficiency regressions from new context assembly | `ContextKit` invariance checks + S17 live check on Anthropic. |
| Provider API drift (model IDs, tool formats) | `list_models` live; recorded fixtures refreshed by a `make fixtures` task with `HARNESS_LIVE=1`; no hardcoded IDs. |
| Web studio rewrite takes longer than expected | CLI frontend reaches M1/M2 first; WEB-01…04 are the critical path for M3, WEB-05…08 can trail. |
| Third-party plugin safety expectations | D10 default (bridges only), capability clamping, documented in `SECURITY.md`. |

---

## Appendix — PR sequence (suggested titles)

1. `chore(HYG-01,02): hermetic test fixtures and dynamic ports`
2. `chore(HYG-03,04,05): remove author paths, fix .gitignore, move packaging`
3. `chore(HYG-06,07): ruff + CI, TurnResult dataclass`
4. `fix(STOP-01): token auth, content-type/origin checks, host guard`
5. `fix(STOP-02): sanitised markdown, CSP, vendored JS, sandbox origin`
6. `fix(STOP-03,04): traversal validators, secret perms, upload cap, remove fake state`
7. `fix(STOP-05,06): surface provider errors, no mock fallback, no dirty-tree commits`
8. `fix(STOP-07,08): workspace jail for tools, executor hardening`
9. `feat(KRN-01..03): sdk types/contracts/events/cards, event bus`
10. `feat(KRN-04,05): settings+secrets, plugin registry, boundary tests`
11. `feat(KRN-06): policy gate, jail, approvals`
12. `feat(KRN-07,08): context assembler, compaction, turn runner, inbox`
13. `feat(KRN-09..11): session/commands, conformance kits, CLI frontend`
14. `feat(PLG-01..09): slice plugins` (one PR each)
15. `refactor(KRN-12): cut over to kernel, delete legacy`
16. `feat(PLG-10..22)` (one PR each) · `feat(WEB-01..08)` · `feat(BRG-01..04)` · `chore(EXP-01,02)` · `feat(SDK-01..03)` · `docs(DOC-01..03)` · `release(REL-01): 0.2.0`
