# Development checkpoint — 2026-10-23

**Partial implementation; release qualification incomplete. This is a source
checkpoint, not an installable release or a claim that the full suite is green.**

The maintained roadmap has20 of39 task acceptance items checked. This is a task
count, not a time estimate. Detailed local results and remaining gates are in
`tasks/todo.md`, `review_regressions.json` and the per-task testing documents.

## Current verified parent boundary

- The integrated ACP/MCP/privacy/CLI group passes115 checks on Python3.11 and
  Python3.12. Exact worker and parent composition source/patch identities are
  independently reviewed. See `T19_ACP_KERNEL_BINDING.md`.
- ACP binding currently speaks internal legacy0.1. Official ACPv1/client/editor
  interoperability remains T20; this checkpoint does not claim IDE compatibility.
- Parent Studio source contains the accepted T23 event/action-isolation slice.
  T24's history/reconnect/record-identity changes and tests remain in an isolated
  worktree, source-reviewed but awaiting integration and browser verification,
  and are deliberately not included here. Cross-barrier event ordering remains
  a separate known qualification gap.
- Earlier full-suite evidence remains historical and non-green. Passing focused
  groups does not replace a fresh full qualification run.

## Remaining release gates

G1's strict headless-question timing gate, T24 real-browser/reconnect and visual
verification, official client interoperability, frozen Studio resource repair,
required Linux/native and live-provider lanes, final artifact rebuild/install
verification, and the minimum two-hour reliability soak remain open. Signing and
notarization are separate, unverified claims. No main-branch merge or release is
authorized by this checkpoint.

Raw logs, JUnit files, local session data, screenshots, environments, build output
and isolated worktrees are not part of this source checkpoint. Local ignored
evidence is retained; the public testing documents preserve its scope and results.
