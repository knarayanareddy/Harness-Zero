# Zide workflow reference and integration decisions

Reference ID: ZIDE-2026-10-22. Sources reviewed: 2026-10-22.
Status: development-workflow adoption authorized; runtime features remain planned.

## Decision

Adopt task isolation, exact-candidate independent review, truthful session status,
and state-bound Git approval requirements. Keep the canonical Harness-Zero kernel
and current correctness/packaging sequence. Do not build a full IDE clone or
claim that our development tooling is already a shipped Harness-Zero feature.

The operational process is [tasks/WORKFLOW.md](../../tasks/WORKFLOW.md).
The main backlog keeps T01–T39; the ZIDE identifiers below are supplemental
acceptance items owned by those tasks, not a renumbered or completed roadmap.

## Evidence and limits

These are **vendor-documented behaviors**, not observations from an installed app
or a source-code/security audit. No Zide binary was installed, no account or
connected repository was configured, and no performance comparison was run.
The public repository explicitly identifies Zide as proprietary. Use independently
written behavior and tests, not its source, screenshots, branding or assets.

| Source | Documented behavior relevant to this build |
|---|---|
| [Features](https://zide.dev/features) | Issue → agent → diff → staging → PR → CI workflow; Crosscheck sends agent commits to a chosen second model. Conversation rollback does not undo files or Git operations. |
| [QuickTree](https://docs.zide.dev/guides/quicktree/) | Prompt, model and base-branch selection create a worktree and an Assist session together. Documentation updated Oct 10, 2026. |
| [Session Watch](https://docs.zide.dev/guides/session-watch/) | Distinguishes approval/input waits, replies, working and saved sessions. CLI/terminal process activity is not interpreted as an AI approval request. Documentation updated Oct 10, 2026. |
| [Safety & Permissions](https://docs.zide.dev/guides/safety-and-permissions/) | Permissions apply to Zide Assist, not automatically to external CLI agents. Git approvals record repository state and stop the action if that state changes. Documentation updated Sep 13, 2026. |
| [Connect a CLI agent](https://docs.zide.dev/guides/connect-cli/) | Custom CLI launch profiles are supported; this is not proof of Harness-Zero MCP/ACP interoperability. Documentation updated Oct 21, 2026. |
| [Public repository](https://github.com/zide-software/Zide) | Closed-source proprietary software, not a source-reuse dependency. |
| [Privacy policy](https://zide.dev/privacy) | Selected AI inputs can leave the device for third-party providers; a native/local workspace is not a universal no-transmission guarantee. |

Our inference: these behaviors can reduce handoff ambiguity and review mistakes.
Unknown: Zide's internal enforcement, crash durability, cancellation guarantees,
actual latency, or comparative safety/quality. Do not claim superiority either way.

## Current build versus the development environment

Harness-Zero has local execution/recovery and provider repair evidence, but G1
and release qualification remain non-green. Its intended path remains Commands
→ runner → policy/tool execution → canonical transcript and scoped events.

The Luna workers, worktree dispatch and reviewer used to develop it are OpenCode
resources. Their successful use does **not** complete Harness-Zero's T38 child
runtime or establish end-user Session Watch/Crosscheck functionality.

## ZIDE-01 — isolated task launch

**Adopt now:** each authorized worker gets a unique worktree, base identity,
write allowlist, acceptance command and evidence location. One parent owns
integration and shared documents. Do not silently copy the parent's dirty tree
or assume a HEAD-based worktree contains its uncommitted fixes.

**Runtime owner:** T38's one canonical parent/child integration, after its existing
dependencies. The UI launcher must use that path, not invent another agent loop.

**Acceptance, not yet implemented:**
- [ ] Launch records bind task, model, worktree and base snapshot; failure leaves
  no apparently running session or hidden partial initialization.
- [ ] A child cannot expand its file scope, capabilities, budget or parent depth.
- [ ] Parent cancellation and restart settle the child lifecycle honestly.
- [ ] Base divergence/conflicting user edits block integration; no automatic reset,
  stash, discard or merge resolution. Missing initial commits require an explicit
  owner decision, not QuickTree-inspired implicit commit authorization.

## ZIDE-02 — independent review tied to the exact change

**Adopt now:** review the scoped candidate before integration/commit authorization.
Record its digest and the review scope. A model's opinion is not a test result or
permission to ship. Unlike Zide's documented post-commit trigger, our workflow
does not create a commit merely to request review.

**Runtime/UX owners:** T37 for the visible review/test/deliverable journey; T38
for a child result's reviewable handoff. Reuse one change identity, not a separate
conversation-side cache of what was reviewed.

**Acceptance, not yet implemented in the product:**
- [ ] Review and test receipts identify the same candidate digest, base and scope.
- [ ] Any candidate file/mode change invalidates the affected receipt and requires
  fresh review/verification; an earlier source-only review is never relabeled as
  approval of later test or runtime changes.
- [ ] Reviewer failures, unavailable models and unanswered findings remain visible;
  no silent reviewer fallback or author self-review is counted as independent.
- [ ] Review context excludes secrets and unrelated user work; using a remote
  reviewer is not described as local-only processing.

## ZIDE-03 — truthful attention and session state

**Adopt now:** status updates distinguish dispatched, changed, tested, reviewed,
integrated, blocked and delivered. Idle output is not evidence of completion;
a saved session is not evidence of a live process. Human approval remains its
own state, not a guess from terminal text.

**Runtime owners:** T23/T24 establish session scoping and reconciliation; T37
adds the attention/review UX after their and its other existing gates. Derive
status from canonical runtime facts and pending interactions, not a second store.

**Acceptance, not yet implemented as a dashboard:**
- [ ] Approval/input waits are session-scoped and actionable only for their owner.
- [ ] Reversed responses, project switches, reconnect and restart cannot paint
  another session or turn an unknown/saved process into a live one.
- [ ] A completion badge identifies verification scope and stale evidence; it
  never conflates source review, test success, integration and remote delivery.
- [ ] Keyboard access and stable focus/ordering are tested with the frozen journey.

## ZIDE-04 — invalidate stale repository approvals

**Adopt now, as an integration check:** the parent rechecks the destination base
and scoped content before applying a candidate. If another worker/user changed
it, stop and reconcile explicitly rather than overwriting it.

**Runtime owner:** a focused approval/policy-boundary security slice extending
T05's contract; T17/T18 must account for it before exposing Git-mutating tools.
This is additional acceptance, not a claim that completed T05 cancellation work
already implements repository-state binding. Existing allowed-read bridge repair
may proceed without inventing Git mutation support.

**Acceptance, runtime still pending:**
- [ ] A design defines the approved operation, arguments, repository/worktree,
  branch/ref/HEAD, index and relevant dirty/untracked content identity. HEAD alone
  must not stand in for a full reviewed-change identity.
- [ ] After approval, changed arguments, repository identity or covered state
  revoke that approval before handler entry; re-read/re-approve rather than retry.
- [ ] Synthetic tests change HEAD, index, dirty content and arguments separately;
  no stale-approved mutation starts, including after restart or sibling work.
- [ ] Validation/locking and bounded fingerprint costs are specified and tested.
  External-writer races and limitations are explicit; a single state comparison
  must not be advertised as filesystem-wide transactional isolation.
- [ ] Opaque shell commands that can invoke Git receive an explicit policy design;
  command-name string matching does not establish a safe mutation classification.

## Sequence and rejected alternatives

Current repairs and G1/G2/G3 qualification keep priority. ZIDE-04 is a security
admission condition before future approved Git mutations, not an excuse to ship
a new Git surface early. T23/T24 repair state correctness before T37 adds status
UX. T38 remains behind its existing budget, isolation and evidence gates.

Defer a full editor/LSP/Git-host client, phone Relay/remote approvals, and broad
desktop automation. Remote interaction would require its own authentication,
device/session binding and revocation design. Reject Danger Zone defaults,
automatic commits, treating reviewer output as authorization, proprietary reuse,
or counting feature parity as evidence of a better harness. No framework/kernel
rewrite, new provider route or live-spend authority follows from this note.
