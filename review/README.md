# `review/` — Harness-Zero review, architecture and build plan

Everything in this folder was produced by an end-to-end review of the repository at commit `a0afc75` (branch `arena/4bab20a8-harness-zero`), plus a follow-up review of the v0.2.0 rebuild (`7185083`). **No source files were changed.** The folder is self-contained: documents reference each other by bare filename and reference repository files by their path from the repo root.

## Latest: `CODE_REVIEW_v0.2.1.md` (third pass, after the §9 remediation)

The v0.2.0 review's six remediation steps were executed (8 commits, tag `v0.2.1`). Backend: every P0 is fixed and verified (loop memory, persistence, resume, `submit_turn`, previews, approvals, secrets, SSE tickets, strict CSP, dead tree deleted). Frontend: still does not work in a browser — the client only listens for unnamed SSE messages while the server sends named events, so no kernel event has ever reached `app.js`; the model dropdown causes a 500; a fresh daemon has no session; and the final commit pasted the v0.1 mockup back in with its handlers replaced by fake confirmations and `noop` buttons (16 of 131 interactive elements are wired). The review includes a headless DOM reproduction (`tools/ui_smoke.mjs`), a demonstration that a 3-line client fix makes the chat work, a placeholder inventory, a trend table across all three reviews, and a short ordered fix list. **Read this first.** An addendum (A.1–A.3) covers two more subsystems verified through the real kernel afterwards — the workspace memory (`remember`/`search_memory`/context injection used four different databases) and the MCP client bridge (handler signature mismatch; no external tool could execute) — both fixed with kernel-level regression tests, plus the options for an Obsidian integration.

## Executed: `dsh-integration/FLOW_TEST_FINDINGS.md` + `dsh-integration/scenarios/` (the flow-of-information test)

The first workstream of the pack, built and run instead of only specified: twenty record/replay scenarios drive a prompt through the real kernel, the real web daemon and the shipped browser client in jsdom, with the model replayed from a recording. Running them found and fixed six P0 blockers in the shipped tree — the client could not submit (`submit_turn` rejected its payload), could not receive (named SSE frames never reached the listener), rendered streamed text once per subscriber (EventBus coalescing mutated the shared event), crashed on any thinking delta (`ThinkingBlock` field mismatch), could not grant an approval (`approval_id` vs `request_id`) and lost the conversation on every page refresh — plus the silent iteration cap and tool cards that said `ALLOWED` on denied calls. Each fix is a minimal change to an enumerated `harness/` file with a regression scenario or test; the repo suite goes from 353 to 357 passing. The findings file ends with a per-subsystem matrix (plugins, skills, memory, diff/checkpoints, auth, guardrails, sessions, MCP, providers) of what the suite proves and what it does not. Three lower-severity findings (phantom Ollama models, unsigned thinking re-send, thinking never persisted) are left to their workstreams with the evidence recorded.

## Integration pack: `dsh-integration/` (how to bring the DeepSeek Harness practices into this tree)

Executable follow-up to the research note, written for the build agent. `dsh-integration/README.md` is the entry point: six invariants with a CI gate each (`00_PRINCIPLES.md`), a machine-readable plan with dependencies, effort and acceptance commands (`plan.yaml`), the standard plugin layout every new plugin must follow (`templates/PLUGIN_TEMPLATE.md`), and eleven workstream specs `WS-01` … `WS-11` (record/replay scenario tests, generated catalogs + freshness gates, loop hygiene, session log as single source of truth, feature-owned UI, permission presets + OS sandbox, workspace changes + `present`, `ask_user`, MCP streamable-http, jobs + goals, subagents + ACP client). Each spec gives the verified current state (file:line), the design in Harness-Zero vocabulary, the exact files to add/modify with skeletons, fail-first tests, acceptance commands, non-goals and a ready-to-paste agent brief. The specs are docs only; the §8 critical path they depend on is now done and proven on this branch (see the section above).

## Research: `RESEARCH_DEEPSEEK_HARNESS.md` (DeepSeek Harness vs Harness-Zero)

Source-level comparison with `deepseek-ai/deepseek-harness` (`dsh`, MIT, TypeScript, `0.2.1-alpha.4`): what it does better for the features Harness-Zero already has (event-sourced session log as the single source of truth, tool pipeline waterfalls, permission presets, plugin-owned UI, record/replay tests, generated catalogs) and which of its features are additive (output pruning/spill, loop guards, read-before-edit, OS sandboxing, deliverables, jobs, goals, `ask_user`, subagent providers, streamable-http MCP). Ends with a ranked adoption list for Harness-Zero and the three realistic options (continue and borrow, compose via ACP, rebase onto `dsh`).

## Previous: `CODE_REVIEW_v0.2.0.md` (review of the rebuild)

After the v2 plan was executed (69 commits, tag `v0.2.0`), the repository was re-reviewed end to end with the same method. Short version: the kernel/SDK/plugin skeleton, boundaries, policy gate, daemon hardening and bridges are real and verified; but the agent loop never feeds tool results or history back to the model (record-schema mismatch + unwired stores), the shipped web page mixes an orphaned modular client with a legacy inline script whose chat endpoint fails on every request, and the legacy `/settings` route re-introduces plaintext API keys and fabricated state. It contains a claim fact-check, a plan-item closure matrix, a duplicate/legacy map, and a prioritised sequence to make the tree cohesive. Superseded by the v0.2.1 review; still the reference for the v0.2.0 closure matrix and the duplicate/legacy map.

## Start here (original review set)

| Order | File | What it is | Read it if you want to… |
|---|---|---|---|
| 1 | **`DECISIONS.md`** | The 16 owner decisions (D1–D16) with options, defaults and consequences, and a blank "Decision" field per item | …decide. This is the only file that needs your input before an agent can start. |
| 2 | **`MODULAR_ARCHITECTURE.md`** | Target design: 9 plugin contracts with full signatures, kernel behaviour, event/card protocols, plugin manifest, trust model, bridges (Claude Code hooks, MCP, ACP), conformance kits, file-by-file migration map, porting recipes | …understand what is being built and why it is future-proof |
| 3 | **`REMEDIATION_PLAN.md`** (v2) | The build sequence: 63 items in 8 phases, milestones M0–M5, definition of done (scenarios S1–S18), verification gate, migration risks, PR sequence | …hand work to an agent |
| 4 | **`REMEDIATION_FINDINGS.md`** (v1 catalogue) | Per-finding detail: file:line anchors, reproductions, root causes, fix specs with reference code, acceptance criteria, tests to add; 61 findings + 13 conflicts | …know exactly what is wrong and how each thing is fixed (v2 items cite these IDs as "absorbs: …") |
| 5 | **`CODE_REVIEW.md`** | The original review: executive summary, what is good, critical/high/medium findings with evidence, test assessment, verification log, Appendix A (Antigravity/Cowork integration audit) | …see the evidence and the verification commands that were actually run |

## Two ways to proceed

**A. Modular rebuild (recommended, what v2 plans).** Make the current code safe first (Phase 0–1, ~3 agent-days), then build the small kernel + SDK beside it, port the salvageable subsystems as plugins against conformance kits, hard-cut over, and ship plugin tooling so your own agent can port features from other harnesses. End state: a harness whose every capability is a plugin against a published, versioned, machine-testable contract.

**B. Patch the monolith.** Execute `REMEDIATION_FINDINGS.md` §3/§4 in its original order. Faster to a "working" prototype, but the three largest files remain the architecture, and the modular goal has to be done later as a second migration.

Phases 0–1 are identical in both routes, so an agent can start on them while you decide.

## How an agent should consume this folder

1. Read `DECISIONS.md`; apply the owner's answers, or the stated defaults where blank, and record which were applied in every PR description.
2. Follow `REMEDIATION_PLAN.md` §3 order. For each item, open the "absorbs" IDs in `REMEDIATION_FINDINGS.md` for repro/root cause/tests, and the cited `MODULAR_ARCHITECTURE.md` sections for contracts.
3. Obey the binding rules: `MODULAR_ARCHITECTURE.md` §0 (principles), §9.4 (boundary tests), §15 (anti-patterns); `REMEDIATION_FINDINGS.md` §0.2 (ground rules: never fake, never widen the workspace, hermetic tests, one item per PR).
4. Run the verification gate (`REMEDIATION_PLAN.md` §6) before every merge.

## Status of the codebase in one paragraph

Harness-Zero (≈6 k lines of Python, a 3.2 k-line single-file web UI, a Swift launcher, 22 design docs, 48 passing tests) has a solid transcript/FTS5/skills core and a clear design vision, but the daemon has no authentication, CSRF or Host protection; the agent's file and shell tools are not confined to the workspace and there is no approval flow; a keyword "mock engine" silently takes over on any provider error and executes real tools; architect mode commits the entire dirty tree (including secrets and session databases) even on failure; the UI renders model output as unsanitised HTML; and several advertised subsystems (MCP, browser automation, visual grounding, battle arena, login tokens, settings tabs) are simulated or decorative. All critical findings were reproduced live and are documented with commands in `CODE_REVIEW.md` §8.

## Line counts

```
DECISIONS.md               decision sheet
MODULAR_ARCHITECTURE.md    663 lines
REMEDIATION_PLAN.md        396 lines
REMEDIATION_FINDINGS.md    636 lines
CODE_REVIEW.md             359 lines
```
