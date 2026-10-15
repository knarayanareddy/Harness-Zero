# `review/` — Harness-Zero review, architecture and build plan

Everything in this folder was produced by an end-to-end review of the repository at commit `a0afc75` (branch `arena/4bab20a8-harness-zero`), plus a follow-up review of the v0.2.0 rebuild (`7185083`). **No source files were changed.** The folder is self-contained: documents reference each other by bare filename and reference repository files by their path from the repo root.

## Latest: `CODE_REVIEW_v0.2.0.md` (review of the rebuild)

After the v2 plan was executed (69 commits, tag `v0.2.0`), the repository was re-reviewed end to end with the same method. Short version: the kernel/SDK/plugin skeleton, boundaries, policy gate, daemon hardening and bridges are real and verified; but the agent loop never feeds tool results or history back to the model (record-schema mismatch + unwired stores), the shipped web page mixes an orphaned modular client with a legacy inline script whose chat endpoint fails on every request, and the legacy `/settings` route re-introduces plaintext API keys and fabricated state. It contains a claim fact-check, a plan-item closure matrix, a duplicate/legacy map, and a prioritised sequence to make the tree cohesive. **Read it first if you are picking up the codebase now.**

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
