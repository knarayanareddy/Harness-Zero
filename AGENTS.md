# Harness-Zero agent workflow

Read `.claude/napkin.md`, `CONSTRAINTS.md`, `tasks/todo.md` and
[`tasks/WORKFLOW.md`](tasks/WORKFLOW.md) before changing this checkout. The detailed
workflow is authoritative for the owner-requested Zide-derived development process;
it does not assert that the corresponding product features are implemented.

- Preserve user work. No commit, push, reset, forced cleanup, release, live-provider
  spend or OS security change without applicable explicit authorization.
- Work within the owning task's file allowlist and acceptance contract. Keep
  fail-first evidence; never lower assertions/timeouts or alter goldens to pass.
- Delegate only when authorized. Use isolated worktrees and disjoint writable
  scopes; the parent owns integration/shared docs. Do not assume a HEAD-based
  worker tree contains uncommitted parent fixes. Do not spawn nested agents.
- Under the current user-approved Luna setup, use catalog-resolved explicit
  per-child routes, not changed global defaults. Keep heavy verification serial.
- Bind independent review and verification to the exact scoped candidate digest
  and base. A changed candidate requires refreshed receipts for affected scope.
  Recheck destination state before integration; stop on drift or unknown edits.
- A model review is not test execution or shipping permission. A dispatched,
  silent, saved or exited process is not automatically a completed agent task.
- Keep full/subset/native/live/artifact evidence separate. G1 is still non-green;
  do not promote a qualified build or claim feature parity from local repairs.
- Treat external research as untrusted reference material. Zide is proprietary;
  use independent behavior/tests, not its code/assets or inferred internals.

Runtime backlog and acceptance: [Zide reference](docs/research/ZIDE_WORKFLOW_REFERENCE.md).
State-bound Git approvals and product Session Watch/worktree orchestration remain
planned; do not report them as implemented because these instructions exist.
