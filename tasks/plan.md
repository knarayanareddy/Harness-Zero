# Active increments: T01–T21 with isolated Luna repair lanes

## 2026-10-31 qualification closure — current owner request

On 2026-11-01 the owner additionally authorized committing and publishing the
reviewed coding work to GitHub. Publication uses a qualification checkpoint
branch, not a release or promotion to `main`. Earlier no-commit/no-push notes
describe their original task boundaries, not this subsequent explicit request.
The seven filmmaking files remain excluded under the earlier coding-only choice.

The owner now requests repair of the non-green G1, recovery and endurance gates.
This section supersedes older paused-work statements for the bounded work below;
it does not authorize release, push, new provider spend, unrelated process stops,
OS security changes, weaker assertions or automatic qualification retries.
Starting base: `a2b0dd8fe0eb308129ed9ac25e2ef47362621158`. The seven untracked
filmmaking-skill files remain excluded and untouched. Retain all failed evidence.

### QA31-A: repair verification prerequisites

- Scope: new ignored qualification environment/worktree and evidence only; parent
  owns this plan and task status. No runtime, assertions or goldens may change.
- Diagnose the actual missing Python 3.11 build backend and interpreter overrides.
  Prepare isolated Python 3.11.17/3.12.15 environments matching CI's editable dev
  installation, pytest 9.1.1, ACP SDK 0.12.1 and locked Node 22 dependencies.
- Acceptance: readiness succeeds with no PYTHONPATH/PYTHONHOME/DYLD override;
  interpreter, child Python, source imports and dependencies match the selected
  four-skill checkout. Run the previously failed packaging case once after the
  evidenced prerequisite repair. A subsequent full CI-source lane requires a
  frozen reviewed source and recorded invocation; preserve every failure.
- Checkpoint: toolchain readiness is not G1 or artifact qualification.

### QA31-B: localize recovery before proposing a repair

- Scope: read-only runtime/source and retained Q05 traces initially; new private
  diagnostic/control files only after a bounded design is reviewed. No corpus
  retry, assertion/deadline change or production patch during investigation.
- Separate the 14/100 boot stall from the older 9/100 second-turn failure.
  Identify the actual boot call chain and the unobserved blocking seams. Compare
  retained successful/failed traces and the nonreproducing watchdog diagnostic.
- Acceptance: a causal reproducer or a documented evidence gap, then a bounded
  observation plan whose negative controls prove it captures the relevant stall
  within the original deadline. Any runtime fix needs fail-first regression,
  positive control, independent exact-candidate review and affected parent tests.
- Checkpoint: passing diagnostics alone never clears Q05.

### QA31-C: qualify the final reviewed source

- First clean-environment source attempt is retained as failed: each Python
  baseline reports 1,312 passed, 7 failed, 5 skipped; plugin tests report
  443 passed, 4 failed. All four RepoMap failures run below an absolute `.venv`
  ancestor; the implementation incorrectly filters absolute rather than
  workspace-relative path parts. Three scenario request mismatches may be
  consequences and must be confirmed against their actual diffs.
- Bounded repair allowlist: `harness/plugins/context/repo_map/map.py` and
  `tests/plugins/test_repo_map_plugin.py`, in isolated worktree
  `qa31-repomap-relative-20261031`. Require fail-first real ancestor/descendant
  path controls, preserve existing ignore semantics inside the workspace, then
  both baseline focused modules, unchanged affected scenario goldens, scoped
  Ruff and independent exact-candidate review before parent integration.
  No dependency, timing, AST extraction or unrelated confinement redesign.
- Depends on QA31-A/B and an agreed controlled workload window. Heavy checks are
  serial; never stop unrelated workloads to create the window.
- Acceptance: both baseline default matrices and applicable source-CI commands
  pass on the final source, then Q03 100/100 and Q05 100/100 with independent
  acceptance/cleanup checks. Stop the fixed-count attempt on its first failure;
  do not top up, blindly retry or combine partial runs. Unavailable Linux/native
  evidence stays explicitly unverified rather than becoming a macOS pass.
- Refresh affected installed/frozen artifacts against that source; keep exact
  binary, source-only, native, external-editor and live-provider evidence separate.

### QA31-D: uninterrupted endurance

- Depends on QA31-C, the final reviewed source and a reserved execution window.
- Acceptance: one uninterrupted >=7,200-second, >=100-turn offline mixed-session
  run with durable one-shot supervision, no restarter, terminal/resource report,
  transcript/interaction/job checks and verified owned cleanup. Resource growth
  requires investigation, not an automatic pass. An interrupted run is incomplete.
- Parent updates the ledger only from actual receipts. Full release qualification
  still requires all applicable platform/artifact/editor/live gates and explicit
  release authority; this work does not grant either.

User-authorized scope: baseline diagnosis, preserved review reproductions,
boundary/quality contracts, T04 search confinement, T05/T06 cancellation and
sequential T07 crash recovery, T08 dangling-call repair and T09/T10 compaction. No commit, push or
deployment. T06 supervisor design was explicitly approved. Native/global
qualification stays blocked; locally verified slices advance sequentially.

T11's wrapper and native nested-cwd/relative-write acceptance now pass on this
Mac. The earlier availability failure was a profile bug: dyld required read access
to the exact root directory, not its descendants. Outside-file denial controls
also pass. No new runner or security-setting change is needed. Linux/global
qualification remains incomplete. Subsequent integration progress is recorded below.

T12–T17 provider/media/XML/export repairs are now locally integrated. G1's fresh
full suite remains non-green; targeted fixes are not a substitute for its required
full qualification. Continue existing defect repairs, not feature promotion.
The user explicitly selected GPT-6 Luna for two isolated workers plus read-only
review. Current lanes are T18a real MCP gate binding and T21 late MCP registration.
T18's baseline is an explicitly hashed copy of the current parent working source,
not an assumed HEAD snapshot. Parent owns patch integration, shared docs/ledgers
and serial heavy verification; no commits, default-model rerouting or publication.
Baseline: `bfa52425c1ff37536f31dc74df2ed66dcd953e58`.

## Deliverables

- Owner-approved workflow addendum (2026-10-22):
  [Zide reference and acceptance](../docs/research/ZIDE_WORKFLOW_REFERENCE.md),
  [current development workflow](WORKFLOW.md), and root `AGENTS.md` activate
  isolated scopes, exact-candidate review/verification, truthful status and
  destination rechecks now. Runtime ZIDE-01–04 remain planned under existing
  T23/T24/T37/T38 and approval/policy ownership; no milestone gate is weakened.

- T01: `scripts/verify_environment.py`, diagnostic tests, qualified toolchains,
  retained full-run evidence, `docs/testing/BASELINE.md`.
- T02: 19 explicit fail-first probes, evidence-tier/repair-owner ledger and
  machine-readable index. Runtime regression promotion belongs to each repair.
- T03: root `CONSTRAINTS.md`, BC-01–BC-11 contract addendum, session-log durability
  limits and `docs/testing/VERIFICATION_MATRIX.md`.
- T04: descriptor-confined search, permanent gate/race regressions, reduced
  capability declaration and generated catalogue updates. Local checks and
  installed-wheel smoke pass. Regression execution finished through an
  interrupted run plus foreground remainder; qualification remains blocked,
  not a green uninterrupted full-suite result. T01 stays red.
- T05: forwarded turn cancellation, pending-interaction cleanup/late-answer
  guards and queued-handler recheck. Scoped tests/scenarios pass; independent
  review findings reproduced/fixed and final source-only re-review complete.
  Active-process cancellation and STOP repair remain T06.

T01 full runs finished with all failures/skips classified; qualification remains
non-green until B01–B04 and required native/new-Linux evidence are resolved.
T02/T03 may establish evidence/contracts while that
gate stays open; this does not authorize progression to repairs automatically.

## Historical T01–T03 checkpoint

Inspect all 19 intended failures, pass scaffolding and lint, classify full-run
failures/skips, report historical remote separately from new local execution,
and leave the active runtime unchanged. Task status lives in `todo.md`.
