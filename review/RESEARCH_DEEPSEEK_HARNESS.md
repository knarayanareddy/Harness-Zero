# Research: DeepSeek Harness (`dsh`) vs Harness-Zero — what is additive, what is a better implementation

**Subject:** `github.com/deepseek-ai/deepseek-harness` @ `86a40220` (2026-10-15, `0.2.1-alpha.4`, MIT), cloned and read on 2026-10-15.
**Baseline:** Harness-Zero `f32faf1` + PR #4 (memory/MCP fixes). Everything stated about Harness-Zero below is from the three reviews in this folder, i.e. verified by running it; everything stated about `dsh` is from its source tree and package READMEs (file pointers in Appendix A), not from launch coverage.

---

## 0. Verdict in five sentences

1. `dsh` is the same idea Harness-Zero set out to build — "everything is a plugin", an event-sourced session log, a guarded tool pipeline, approvals, sandboxing, MCP/ACP/SDK bridges, a web GUI — executed at a scale Harness-Zero will not reach: ~60 package families (340 package READMEs), 1,672 unit-spec files, 316 e2e specs, 625 recorded-session fixture files, 90 catalogued events, and 64 generate/verify scripts gating docs and structure in CI.
2. For the **kernel** concepts, `dsh` is mostly a *better implementation of what Harness-Zero already has*, not a different design; the differences are discipline (log as single source of truth, generated catalogs, replay tests) rather than cleverness.
3. For the **product surface** it is largely *additive*: loop-hygiene guards, spill/prune of tool output, permission presets bundled with OS sandboxes, deliverables, goals, background jobs, scheduling, plan mode, `ask_user`, subagent providers (including Claude Code/Codex), LSP, browser/computer use.
4. The single most valuable thing to copy is not a feature but **two practices**: (a) keyless record/replay tests that boot the *real* app against recorded model transcripts and compare committed goldens, and (b) docs/catalogs generated from the code and freshness-gated in CI — these are precisely the two mechanisms whose absence let Harness-Zero ship a dead UI and a fabricated CHANGELOG three releases in a row.
5. Do **not** port the architecture wholesale or rewrite Harness-Zero in TypeScript to sit on `dsh`; that is a different project. The honest fork in the road is in §5.

---

## 1. What `dsh` is (facts)

| | `dsh` | Harness-Zero |
|---|---|---|
| Language / runtime | TypeScript, Node 22.19+/24+, pnpm workspace; Electron desktop; Python SDK that spawns the CLI | Python 3.11, aiohttp |
| Plugin framework | [Cordis](https://github.com/cordiverse/cordis): plugins contribute services, typed events, *reversible* effects; registrations unwind on unload | own registry (`kernel/registry.py`), manifests, capability clamps |
| Composition | **profiles** (`web`, `headless`, `sdk`, `sdk-minimal`, `acp`) = ordered **bundles** + user `cordis.patch.yml`; `dsh --dump-config` prints the exact plugin tree; HMR for config | `~/.harness/settings.json` + plugin dirs; no profile/patch layering |
| Code size | 988 k lines TS under `packages/` + `apps/` (includes vendored/generated) | 21 k lines Python |
| Tests | 1,672 `*.spec.ts`, 316 e2e, web lane with built-client interaction snapshots, `snapshots/` = 625 recorded JSONL fixture files replayed keylessly (`DSH_SNAPSHOT=replay`) | 353 pytest (after PR #4); no JS executed in CI |
| Docs | per-package README contract (purpose/config/extension points/"Model Experience") enforced by `verify-package-readme-*`; generated `tool-catalog`, `config-catalog` (4,771 lines), `event-producer-consumer` map (90 events with producer file:line), `module-graph`, `persistence-catalog`, `session-format-catalog`; `doc-sync` gate in CI | hand-written; CHANGELOG 0.2.1 fabricated names (review §1) |
| Maturity | developer preview, "THERE WILL BE COMPATIBILITY-BREAKING CHANGES"; no security audit (SAFETY.md) | v0.2.1 |
| Coupling | first-class DeepSeek account/API-key plugins, DeepSeek web search; **`session-log-deepseek` is mounted in the shipped profiles with `enabled: true`** — it appends an incremental session-log field (≤ 8 MiB) to official DeepSeek API requests until you set `enabled: false` or flip the Web switch (`ui-settings-session-log`); any OpenAI-compatible endpoint via `llm-pi-ai` | provider plugins: anthropic, gemini, ollama, openai-compat, mock |

---

## 2. Side-by-side on the axes Harness-Zero already has

Legend for the last column — **Better impl.**: same feature, `dsh` does it in a way Harness-Zero should adopt. **Additive**: Harness-Zero does not have it. **Parity**: comparable. **Skip**: not worth it for Harness-Zero.

| Axis | Harness-Zero (verified state) | `dsh` | Verdict |
|---|---|---|---|
| **Session log** | `transcript.jsonl` + `transcript_full.jsonl` per session + SQLite FTS + EventBus (30 event types). Loop history is rebuilt from the session store; UI is fed a *separate* SSE stream of bus events. Format unversioned. | One append-only `SessionEvent` log is the **single source of truth**: `deriveMessages()` projects model history from it; UI, resume, fork, replay, export, telemetry all fold the same log. Rule: **"Model-visible means logged"** — a new model-visible input requires a new session event. ~20 event kinds in `SessionEventMap` (durable: `turn/*`, `step/*`, `system|user|assistant/message`, `assistant/attempt`, `tool/*`), versioned format with frozen migrators v0→v4 (`session/session-format-*`), checkpoint policy (fail-closed durability before a model request / top-level tool effect), projection registry with persisted cache. | **Better impl.** The UI contract bug (review §3) could not exist in this design: the browser renders the same events the model history is derived from. |
| **Turn/step model** | `TurnRunner` + strategy plugin loop (`max_iterations` 20); inbox for mid-turn input; `agent/pre-step`-like hooks absent | `turn/start → step/* → turn/end`; `agent/pre-step` waterfall may rewrite/reject claimed input; `agent/request` waterfall; `agent/turn-stopping`; one inbox; "request series" for compaction boundaries | Better impl. (naming + interception points); not urgent |
| **Tool pipeline** | `PolicyGate`: policy plugins (order), approvals via inbox, executor, 64 KB truncation, secret sanitisation, previews, cards | `tool/call` logged **before** execution → `tools/pre-execute` waterfall (hooks, permission, sandbox) → monotonic guards → one-shot `ctx.approval` (absent/unanswerable ⇒ deny) → `tools/execute` around-wrapper (timeout, retry, metrics) → body → `projectContent` → `tools/post-execute` (accept/block/replace/add context) → `finalizeContent` → `tools/result` → `tool/result` event. Diagram generated from code (`docs/tool-execution-pipeline.md`). | Parity in shape; Better impl. in the *pre/post waterfall* seams and in logging the call before running it |
| **Approvals / permissions** | 4 modes (`bypass/plan/ask/allow`) + risk classes; approvals round-trip (verified) | `interaction/permission-presets`: a preset = **sandbox mode + approval policy** (`workspace-write`, `danger-full-access`, derived `custom`), pinned per session, `/permission` picker in UI; `user-approval` is a channel-neutral one-shot seam; `user-questions` waterfall backs the `ask_user_question` tool | **Better impl.** (bundle the two knobs) + **Additive** (`ask_user_question`) |
| **Sandbox** | workspace jail (path policy) + `tirith` regex command audit; no process confinement | `sandbox/sandbox-local`: Linux `bwrap` then Landlock (own Node addon), macOS Seatbelt, Windows restricted-token ACL; "enforcement is a reported fact" (`full`/`partial`), fails closed with `SANDBOX_UNAVAILABLE`; `fs-sandbox` confines file tools to the session write roots; fs + subprocess share one "execution world" so an SSH/remote provider moves Bash, PTY and LSP together | **Additive** (OS confinement) |
| **Context budget** | compaction at 80 % of budget via LLM summary; no tool-output policy beyond 64 KB cut | `compaction-basic` (threshold/headroom/retain ratios, overflow recovery, per-model policies), `compaction-tool-result-pruner` (head 4,096 / tail 1,024 chars at 8,192), `compaction-image-offload`, `spill-policy` (shared inline-token budget; oversized text saved to a session-private file with a locator the model can `read`/`grep`), `token-meter` | **Additive** (prune + spill) |
| **Loop hygiene** | none | `guard/repeat-tool-reminder` (advisory nudge after N identical calls, per agent, reset by user message), `guard/timeout-policy` (cooperative deadline → clear model error), `fs-observation-policy` (**read-before-edit**, rejects edits to a file changed since the read) | **Additive**; cheap and high value |
| **Memory** | `remember`/`search_memory`/`memories` context (working after PR #4), FTS over turns, `AGENTS.md`/`CLAUDE.md` loader | Deliberately **no facts table**. Memory = instruction chain (`$DSH_HOME/AGENTS.md` → project `AGENTS.md`/`CLAUDE.md` + `*.local.md` overlays, 64 KiB budget), skills, `session-query` (FTS5 over the *whole* session corpus, lineage, filters), `@session` references (cross-session snapshots as untrusted context), `@file` references | Parity on FTS; **Additive**: `@session`/`@file` references, instruction overlays. Keep `remember` — it is the piece `dsh` lacks and the Obsidian bridge builds on it. |
| **Skills** | `SKILL.md` catalog (progressive disclosure), `view_skill` | `skill` registry with multiple providers; `skill-filesystem` scans project/custom/user roots, `SKILL.md` bundles *or* flat `<name>.md`, body re-read on every load; `skill-office`; `verify-skill-invocation-metadata` gate | Parity (format compatible); minor Better impl. (roots, hot reload) |
| **MCP** | stdio client, `mcp__<server>__<tool>`, risk from annotations (fixed in PR #4), `read_resource` | `mcp-client`: stdio **and streamable-http**, per-call timeout 60 s, cancellation, reconnect with backoff (500 ms→30 s, 10 attempts), env scrubbing, same naming shape; `mcp-resources` with explicit server selection and agent-scoped access | Better impl. (transport, reconnect, cancel) |
| **Subagents** | child turns (depth ≤ 2), worktree isolation | `subagent` seam with providers: fork-in-process (seeded with parent's turns), spawn-in-process, out-of-process via SDK, **ACP child**, **Claude Code**, **Codex**; `send_message`/`interrupt_agent`/`list_agents`; experimental agent teams; `workflow` tool runs a model-written JS orchestration script in a sandboxed Node runtime (PTC) | **Additive** |
| **Bridges / SDK** | ACP server, MCP server, shell hooks, CLI | ACP server (automation-only), stdio JSON-RPC SDK (TS client + Python client that ships the runtime as a wheel), remote BFF/RPC gateway (`api/`) | Parity on ACP; Additive: SDK |
| **Web UI** | one page; 16 of 131 interactive elements wired; dead settings tabs; no JS tests | Browser half is itself a Cordis plugin tree (`client/*`): `ui-conversation` renders **conversation nodes registered per session-event type**; each feature ships its own `ui-settings-*` tab (`ui-settings-shell`, `-subagent`, `-models`, …) — **a settings page exists only because the plugin that owns the setting registered it**; `ui-approval`, `ui-deliverables`, `ui-jobs`, `ui-goal`, `ui-plan`, `ui-permission-presets`; connection layer with reconnecting event stream and a "browser-trust fence" (Host must be loopback / bind IP / allow-listed); `verify-client-*` gates | **Better impl.** — this is the structural answer to the placeholder problem |
| **Deliverables** | artifacts store + `create_artifact` | `workspace-changes` summarises each top-level turn's changed files from git working-tree snapshots and announces a `workspace/changes` event; `present` tool declares files as deliverables; UI shows a changed-files card + review tab | **Additive**; Harness-Zero's checkpoint shadow repo already has the data |
| **Long-running work** | — | `jobs` (background jobs with output ring, `job_output/list/kill`), `schedule` (durable reminders), `goal` (one durable objective per session + round driver), `plan-mode` (`/plan`, reviewed exit), `todo_write` | **Additive** |
| **Web access** | — | `web` seam with fetch (same-origin redirects, text-only) and search providers (DeepSeek, Exa, Perplexity) | Additive (medium) |
| **Testing infra** | `ScriptedProvider`, mock script JSON, 2 kernel round-trip tests | `llm-replay` (boot the real agent keylessly against recorded Session JSONL; `replay.override.json` injects failures/hangs/retries), `llm-mock-server` (fault-injecting Messages-compatible server), `agent-loop-testkit`, `client-runtime` (jsdom slot runtime for browser specs), `session-snapshot` (manifests, identity redaction, workspace comparison; adapters for headless/SDK/ACP/Web), `loader-smoke` | **Better impl. — adopt first** |
| **Docs discipline** | hand-written, drifted | generated catalogs + `verify-doc-refs`, `verify-md-links`, `verify-subsystem-pages`, `verify-package-readme-*`, `doc-sync` gate | **Better impl. — adopt first** |
| **Self-modification** | — | `extensions/*`: agent can inspect the live plugin graph and write/mount plugins | Skip for now (risk) |
| **Desktop / Office / browser / computer use / LSP / SSH** | screenshot tool (macOS) | Electron app; Office→PDF; Playwright/DevTools MCP; computer-use; LSP seam + `lsp` tool; SSH providers | Skip / later |

---

## 3. Additive features worth taking, ranked for Harness-Zero

Ranked by (value to the stated goal — a harness whose features actually work) ÷ (effort in the Python codebase). Effort assumes the §8 critical path of the v0.2.1 review is done first.

| # | Take | Why it matters here | Effort |
|---|---|---|---|
| 1 | **Record/replay golden tests** (`test-support/llm-replay`, `session-snapshot`) | Harness-Zero already has the pieces: `ScriptedProvider`, `HARNESS_MOCK_SCRIPT`, `transcript_full.jsonl`. Formalise: `harness record` writes the provider stream per model call to `tests/snapshots/<scenario>/`; `pytest` boots the real kernel (and the web daemon + `ui_smoke.mjs`) with the recorded stream and diffs the resulting transcript/DOM against committed goldens. This is the test that would have failed on every one of the three releases. | 2–3 days |
| 2 | **Generated catalogs + freshness gates** (`scripts/gen-tool-catalog.ts`, `gen-config-catalog.ts`, `verify-doc-refs.ts`, `run-gates.ts`) | `harness events schema` already exists and is diffed in CI. Add `harness plugin catalog --md` (from manifests + registered tool specs + settings schema) → `docs/PLUGIN_CATALOG.md`, and `harness ui contract` (every `data-*` handler/command id in `index.html` must resolve to a registered command/event) → fail CI on drift. Replace the hand-written CHANGELOG feature claims with links into generated catalogs. | 1–2 days |
| 3 | **Loop-hygiene bundle**: tool-result pruner (head/tail), spill-to-file with locator, repeat-call reminder, cooperative tool timeout, read-before-edit | All are ≤ 250-line plugins in `dsh`; Harness-Zero has the hook points (`PolicyGate` steps 8–9, `ToolResult.preview`, fs tools). Directly improves real-model sessions (fewer context blow-ups, fewer loops, no blind overwrites). | 2 days |
| 4 | **Feature-owned UI registration** (`client/ui-settings-*`, `ui-conversation` node registry) | Python side: add `settings_schema` + `ui` sections to `PluginManifest`; the web client renders settings tabs and cards **only** from what plugins register (Harness-Zero already renders 5 schema groups — make that the *only* path and delete the static pages). Conversation rendering: one renderer per event type in `docs/events.schema.json`, fail the static-contract test if an event type has no renderer or a renderer has no event. | 2–3 days (after §8 step 2) |
| 5 | **Permission presets** = sandbox mode + approval policy, pinned per session, `/permission` command | Makes the four modes legible to a user and prepares for (6). | 1 day |
| 6 | **OS process confinement** behind the executor seam: `bwrap` (Linux), `sandbox-exec`/Seatbelt profile (macOS) with write roots derived from the workspace; report `full/partial/unavailable`, fail closed | The one security feature Harness-Zero lacks entirely; `tirith` regex auditing is not a sandbox. Reuse `dsh`'s bwrap/Seatbelt argument construction (`sandbox-local/src`) as the reference. | 3–4 days |
| 7 | **Workspace-changes card** per turn from the checkpoint shadow repo (+ `present` tool) | Cheap, visible, and gives the UI a *real* "Deliverables" view to replace a placeholder. | 1 day |
| 8 | **`ask_user_question` tool** over the existing approval/inbox channel | Lets the model ask instead of guessing; the inbox and approval cards already exist. | ½ day |
| 9 | **MCP**: streamable-http transport, per-call timeout + cancellation, reconnect/backoff | Needed for hosted MCP servers (and most Obsidian MCP servers run over HTTP). | 1–2 days |
| 10 | **Background jobs** (`job_output/list/kill`) and **goal** (one durable objective + round driver) | Product features; add once the UI renders real events. | 2–3 days each |
| 11 | **Subagent providers**: ACP child (run *another* agent — e.g. `dsh`, Claude Code, Codex — as a subagent through ACP), fork-in-process seeded with the parent's turns | Harness-Zero already speaks ACP as a *server*; add the client side. Interop note: both speak ACP over stdio (`dsh` `subagent-acp` as client, `harness acp` as server), so composing the two should need no porting — protocol-version compatibility is a one-hour test, not verified here. | 2 days |
| 12 | `@file` / `@session` references in the composer; instruction overlays (`AGENTS.local.md`) | Nice-to-have once the composer works. | 1 day |

Not recommended now: `extensions/*` self-modification (agent writes plugins into a product that cannot yet test its UI), agent teams, PTC/workflow scripts, Office/desktop/browser/computer-use (all large and orthogonal to the goal).

---

## 4. Better implementations of things Harness-Zero already has (what exactly to change)

1. **Make the session log the only truth.** Today the runner, the FTS index, the UI SSE feed and the transcript files are four consumers of three sources. Adopt the rule *model-visible ⇒ logged*: (a) enumerate the durable event set (user/assistant/tool/system message, tool call/result, turn/step start/end, request header, approval, compaction boundary) in `events.schema.json` with a `durable: true` flag; (b) `TurnRunner` derives `ModelRequest.messages` **only** from a `derive_messages(log)` function (unit-testable, like `dsh`'s `deriveMessages()`); (c) the web client renders **only** durable events plus a small set of live ones (`model.text.delta`, `tool.output.delta`); (d) version the log header now (`format: 1`) so migration is possible later; (e) checkpoint (fsync) before each model request and before each mutating tool — `session-checkpoint-policy` is 83 lines.
2. **Log the tool call before running it**, run `pre_execute` hooks as a waterfall that can allow/deny/ask/rewrite, and `post_execute` that can replace content or add context (Harness-Zero's shell hooks bridge already has the inputs; make it the general mechanism).
3. **Approval is a one-shot seam that fails closed**: absent approver ⇒ deny (Harness-Zero does this in the CLI; make the web path identical and test it headlessly).
4. **Tool naming/presentation**: `dsh` lets a preset choose which *form* of its tools a model sees (`agent-tool-presentation`), i.e. Claude-Code-style `str_replace_editor` vs read/write/edit. Harness-Zero's `architect_editor` strategy wants this.
5. **Browser-trust fence**: before auth, reject requests whose `Host` is not loopback / the bind IP / an allow-list (`client/connection/src/api-request-trust.ts`). Harness-Zero checks `Origin` on command posts; add the `Host` check and apply it to SSE and static too.
6. **Settings are owned by plugins**: `PluginSettingsView` already namespaces reads; make *writes* and *UI* follow the same ownership so a settings key cannot exist without an owner (this also kills the fabricated legacy `/settings` document).
7. **README contract per plugin** (purpose, configuration table, extension points, "Model Experience", known limitations) enforced by a script. Harness-Zero's `docs/PLUGIN_CATALOG.md` had two wrong tool names until today; a generated catalog cannot.

---

## 5. The honest fork in the road

Harness-Zero's stated aim is a *working* harness with pluggable providers, tools and a UI. `dsh` is that, MIT-licensed, today — with the caveats that it is a developer preview with breaking changes, DeepSeek-centric defaults, no security audit, and a very large TypeScript tree.

| Option | What it means | When it is right |
|---|---|---|
| **A. Continue Harness-Zero, borrow practices (§3 #1–#4, §4)** | Keep the Python kernel (which is now genuinely sound), fix the UI as planned, and import the *disciplines* first, features second. | If the goal includes owning the kernel, Python-native plugins, or learning by building. |
| **B. Compose**: run `dsh` for the product surface, keep Harness-Zero as an ACP agent inside it (or the reverse) | No porting. `dsh subagent-acp` → `harness acp` (compatibility to be tested); Harness-Zero's tools/policies stay; `dsh` supplies UI, sandbox, jobs, replay tests. Mind the default session-log upload when `dsh` talks to DeepSeek's API (§1, Coupling). | If you want a usable UI in days, not weeks, and are fine with two runtimes. |
| **C. Rebase onto `dsh`** (write Harness-Zero's ideas as Cordis plugins in TypeScript) | A rewrite; Harness-Zero becomes a plugin bundle (`dsh-bundle`) rather than a harness. | Only if Python is not a requirement and you accept tracking a fast-moving preview. |

Recommendation: **A for the kernel, B as the fastest route to a real daily-use surface**, and reassess C only after `dsh` tags a non-preview release. Whichever option, the two practices in §3 #1–#2 are the ones that stop "placeholder" releases.

Licensing: MIT — code may be copied with attribution; keep a `THIRD_PARTY_NOTICES.md` entry for anything ported verbatim (e.g. bwrap/Seatbelt profile construction).

---

## Appendix A — Where to read in `dsh` (paths under the repo root @ `86a40220`)

| Topic | Files |
|---|---|
| Architecture, turn flow, extension map | `docs/architecture.md`, `docs/agent-lifecycle.md`, `docs/tool-execution-pipeline.md` (generated), `docs/event-producer-consumer.md` (generated) |
| Session log, `deriveMessages()`, fork, durability | `packages/core/session/src/index.ts` (1,453 lines), `docs/subsystems/session.md`, `packages/session/session-checkpoint-policy/src/index.ts` (83), `packages/session/session-format-*` |
| Tool registry + pipeline | `packages/core/tools/src/index.ts` (1,993), `docs/subsystems/tools.md` |
| Loop hygiene | `packages/compaction/compaction-tool-result-pruner/src/index.ts` (185), `packages/spill/spill-policy/src/index.ts` (156), `packages/guard/repeat-tool-reminder/src/index.ts` (240), `packages/guard/timeout-policy/src/index.ts` (81), `packages/fs/fs-observation-policy/src/index.ts` (130) |
| Permissions + sandbox | `packages/interaction/permission-presets/src/index.ts` (481), `packages/interaction/user-approval/`, `packages/sandbox/sandbox-local/src/index.ts` (583), `packages/sandbox/sandbox-policy/`, `packages/fs/fs-sandbox/` |
| Memory-adjacent | `packages/context/agent-instructions/`, `packages/context/session-reference/`, `packages/session-query/session-query-sqlite/`, `packages/skill/skill-filesystem/` |
| MCP | `packages/mcp/mcp-client/src/index.ts` (203) + README (transport/reconnect tables), `packages/mcp/mcp-resources/` |
| Deliverables / jobs / goal / ask-user | `packages/deliverables/workspace-changes/src/index.ts` (183), `packages/jobs/jobs-local/src/index.ts` (717), `packages/goal/goal/src/index.ts` (653), `packages/interaction/tool-ask-user/src/index.ts` (119) |
| Subagents / interop | `packages/subagent/subagent-acp/`, `subagent-fork-in-process/`, `subagent-claude-code/`, `subagent-codex/`; `packages/acp/acp/` |
| Testing infrastructure | `packages/test-support/llm-replay/src/index.ts` (1,209), `session-snapshot/src/index.ts` (119), `llm-mock-server/`, `client-runtime/`, `snapshots/` (625 JSONL fixtures), `vitest.web.config.ts` (web lane comment block explains record/replay) |
| Generated docs + gates | `scripts/gen-tool-catalog.ts` (898), `gen-config-catalog.ts` (1,074), `gen-doc-graphs.ts`, `verify-doc-refs.ts` (45), `verify-client-*.ts`, `verify-package-readme-*.ts`, `run-gates.ts` (1,697), CI: `.github/workflows/ci.yml` → `pnpm run check:ci` |
| Web client structure | `packages/client/README.md`, `client/ui-conversation/`, `client/ui-settings-*`, `client/connection/src/api-request-trust.ts` |

## Appendix B — Method

`git clone --depth 1` of the repository; read `README.md`, `SAFETY.md`, `docs/architecture.md`, the package-group README, and the YAML `description:` front matter of the 340 package READMEs; opened the READMEs and `src/index.ts` of the packages cited above; counted spec/e2e files and snapshot fixtures with `find`; extracted CI commands from `.github/workflows`. No `dsh` code was executed (Node ≥ 21 build with native addons; not needed for a source-level comparison). Harness-Zero facts are from `review/CODE_REVIEW*.md` and the live checks run for PR #4.
