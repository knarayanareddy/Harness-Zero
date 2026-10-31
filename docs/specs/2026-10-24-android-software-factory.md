# Android Software Factory Profile

**Status:** in-review  
**Owner:** product/engineering owner  
**Date:** 2026-10-24

## Problem

We want Harness-Zero to produce high-quality native Android release candidates through a repeatable human/agent workflow. The user approved a HumanLayer-inspired task → artifact → checkpoint → worktree → review process, with Android-specific implementation and verification stages.

This is a new product request, not a claim about current runtime behavior. `docs/ARCHITECTURE.md` describes the existing command/plugin/session kernel and worktree executor. The canonical task/worktree/child orchestration and product review/status path remain planned under T37/T38 in `tasks/WORKFLOW.md` and `docs/research/ZIDE_WORKFLOW_REFERENCE.md`. Current G1 and R22 qualification also remain non-green/open. No Android SDK, Android CLI, emulator, or device lane has been verified in this checkout.

## Goals

- Define an Android factory workflow that is a profile on the existing Harness-Zero kernel, not a second agent runtime or a clone of HumanLayer's desktop/cloud product.
- Keep product/design decisions, implementation, verification, review, and release approval as distinct, inspectable phases.
- Make every “pass” depend on real command/device evidence tied to the exact worktree candidate and artifact.
- Support agent-assisted native Android work, defaulting to Kotlin and Jetpack Compose for new projects while adapting to existing app stacks.[8]
- Produce a release-candidate artifact and evidence report; require a human-controlled protected signing and Play production release step.

## Non-goals

- Rebuilding HumanLayer's desktop app, cloud sync, billing, team administration, or generic remote-daemon product.
- Adding a second task/session/worktree executor before T38's canonical parent/child path is implemented and accepted.
- Automatically choosing product features, signing with production credentials, uploading to Play, or rolling out to production.
- Assuming a remote device provider is always available. Firebase Test Lab is scheduled to shut down in 2027 and the documented Google Cloud replacement is Preview; provider selection must remain replaceable.[25][31]
- Requiring a multi-module architecture, domain layer, DI framework, or fixed third-party stack for every app.

## Approach

Use the canonical task/worktree/child path owned by T38 and the task/review/status UX owned by T37. A coordinator prepares and advances task artifacts; a task-scoped worker implements one vertical slice in an isolated worktree; deterministic tools own build/test outcomes; a separate read-only reviewer assesses the exact candidate; the human owns product approval and protected release actions. This reuses HumanLayer's documented task/artifact/worktree/checkpoint pattern without adopting its full product stack.[33][34][35]

The minimum workflow is: **intake/brief → research and UX/technical design → slice outline → worktree implementation → build/lint/unit/UI tests → emulator/device journeys → accessibility/security/performance/artifact checks → exact-candidate review → human release-candidate acceptance**. Missing requirements or unavailable required tools stop or park the task; they do not become inferred success.

Once T37/T38 supplies task ownership, persist reviewable artifacts through the canonical task/session artifact path: product brief and acceptance journeys, design/technical decisions, slice outline, verification receipts, device evidence, artifact manifest, review findings, and explicit human decisions. Until then, do not create a parallel task or approval store. A candidate change invalidates affected test/review receipts.

Start with four on-demand skill areas: product brief and journey design; Compose/Android architecture and vertical slices; testing and device QA; security/accessibility/Play release readiness. Reuse official Android skills/CLI where suitable, pin or record tool versions, and add custom instructions only for evaluated gaps.[17][18][21]

## Workflow checkpoints

1. **Brief checkpoint:** human approves user problem, scope, success signals, supported Android range/device classes, and data/permission assumptions.
2. **Design checkpoint:** human approves material product, architecture, security, or data-model decisions before implementation.
3. **Slice checkpoint:** each worker receives a bounded slice, exact base/worktree identity, writable scope, acceptance cases, verification commands, and budget.
4. **Verification checkpoint:** exit status and retained logs/screenshots determine build/test status. Missing emulator, SDK, or remote provider is `blocked`/`unverified`, not pass.
5. **Review checkpoint:** reviewer receipt names the exact candidate digest and scope; a changed candidate requires fresh affected checks and review.
6. **Release checkpoint:** human accepts the release-candidate evidence. Production signing keys are unavailable to agents; signing, Play upload, and rollout require a separate explicit human approval.

## Acceptance criteria

These criteria become implementation gates; commands named below must be added by the owning implementation task and must not be reported as existing yet.

- [ ] **Canonical task path:** an Android task launches only through the accepted T38 parent/worktree/child path. A test proves task, child, base, branch/worktree, writable scope, and candidate identity are bound; no second executor or direct-to-main path is used.
- [ ] **Human checkpoints:** workflow tests prove no implementation begins before required brief/design approval and no release action begins before explicit release approval. Negative tests cover unanswered, rejected, cancelled, stale, and late decisions.
- [ ] **Durable artifacts:** task artifacts record brief, acceptance journeys, design/outline, per-slice state, test/device evidence, review receipt, and release decision. A new session can continue from those files without relying on an ephemeral transcript.
- [ ] **Project inspection:** the Android adapter identifies the Gradle wrapper, modules, build variants, SDK targets, and available test tasks read-only. Unsupported or ambiguous projects stop for clarification rather than receiving a guessed template.
- [ ] **Build and static gates:** on a committed Android fixture, the workflow runs the project's Gradle build and Android lint/declared static checks; a deliberately failing command blocks advancement and preserves full output/exit status.
- [ ] **Meaningful tests:** a feature fixture includes a test that fails on the pre-change behavior, plus positive and negative cases. Tests cover applicable ViewModel/repository/business behavior and navigation/user journeys; assertions cannot be removed or weakened by the worker to turn the gate green.
- [ ] **Device journeys:** a named local emulator/device runs the install, launch, and declared core journeys. The output retains device/API identity, screenshots, logs, and test results. No device or provider means blocked/unverified.
- [ ] **Quality profile:** the report records applicable adaptive-layout, accessibility, permission-denial/privacy/security, target-API, stability, and performance checks as pass/fail/not-applicable with rationale. Performance claims require measured benchmark evidence.[12][13][14]
- [ ] **Artifact integrity:** a built APK/AAB is inspected and recorded with package/version/build variant and SHA-256. Verification and review receipts bind to that artifact and the source candidate.
- [ ] **Exact review and invalidation:** a read-only reviewer receives the same base, candidate digest, scoped diff, and evidence. Any changed file/mode or rebuilt artifact invalidates the relevant receipts and reruns the affected gates.
- [ ] **Protected release:** tests prove no worker can access production signing credentials or invoke Play production rollout. Release-candidate creation is separate from signing/upload; any signing or publication requires an explicit human decision.[22][23]
- [ ] **Status truthfulness:** orchestration tests distinguish queued, running, changed, tested, review-required, blocked, failed, cancelled, integrated, and delivered states. A model completion string or clean process exit alone never produces `verified` or `released`.
- [ ] **Offline self-test:** factory workflow and policy tests run without a paid provider call, live Play account, or remote device spend; integration lanes report unavailable dependencies as unavailable, not success.

**Planned verification entry points:** new workflow contract tests under `tests/factory/`; a checked-in minimal Android fixture; the fixture's Gradle wrapper for build/lint/unit/UI tests; a local emulator lane for device journeys; and the repository's normal Python/Node gates. Exact commands and fixtures are owned by the implementation slice after prerequisites are accepted.

## Dependencies and rollout

1. Complete the existing owning prerequisites for T37/T38; do not create an alternate parent/child or worktree mechanism.
2. Keep G1, R22, and existing release-qualification status visible. Android work must not relabel them green or bypass their required lanes.
3. Prove the Android fixture end-to-end on a local emulator before adding remote device infrastructure.
4. Add remote device execution behind a provider adapter only after one real provider run, cost/access review, and retained result artifact. Recheck Google's Test Lab/DDP status at implementation time.
5. Pilot on one small app workflow; expand skill count and concurrency only when evaluation results identify a repeatable need.

Rollback is to disable the Android workflow profile and retain task artifacts/evidence. No app source should be modified by rollback. No runtime capability is enabled by this spec alone.

## Open questions

- **Owner:** choose the first Android pilot app or fixture and its minimum supported API/device classes before implementation.
- **Owner:** decide which Android CLI version and local emulator images are the supported initial toolchain after an environment probe.[21]
- **Owner:** approve whether a remote device provider is in the first release. Default recommendation: no; local emulator first, remote provider later.
- **Owner:** set provider/device-test spend limits before any paid or remote device run.

## References

[8] https://developer.android.com/topic/architecture/recommendations  
[12] https://developer.android.com/docs/quality-guidelines/core-app-quality  
[13] https://developer.android.com/docs/quality-guidelines/adaptive-app-quality  
[14] https://developer.android.com/guide/topics/ui/accessibility/testing  
[17] https://developer.android.com/tools/agents/android-skills  
[18] https://github.com/android/skills  
[21] https://developer.android.com/tools/agents/android-cli/commands  
[22] https://developer.android.com/guide/app-bundle/test  
[23] https://developer.android.com/studio/publish/app-signing  
[25] https://firebase.google.com/docs/test-lab/android/continuous  
[31] https://docs.cloud.google.com/developer-device-platform/migrate  
[33] https://docs.humanlayer.com/guide/skills-workflows  
[34] https://docs.humanlayer.com/explanation/workflow-phases  
[35] https://docs.humanlayer.com/guide/workspaces
