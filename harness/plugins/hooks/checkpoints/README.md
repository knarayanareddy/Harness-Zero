# Checkpoints (`hook-checkpoints`)

Keeps a private shadow Git repository at `<workspace>/.harness/checkpoints/.git`; it never commits to or stages the user's Git repository. The hook snapshots at turn start, before state-mutating tools, and at turn end. A bounded `workspace.changes` record is emitted for every started turn, including error and cancellation paths.

The plugin registers these namespaced commands:

- `plugins.hook-checkpoints.changes {session_id}` — read that session's change and restore records.
- `plugins.hook-checkpoints.diff {path, base, head}` — fetch one path's unified diff, bounded by `max_diff_bytes`; binary files have no text patch.
- `plugins.hook-checkpoints.restore {session_id, sha}` — snapshot the current workspace, then require explicit approval before resetting to a verified shadow commit.

It also provides the destructive `checkpoint_restore` tool; the tool is policy-gated and accepts the current turn's `turn-start` checkpoint as an alias. The Diffs panel exposes per-file diff fetch and the approval-gated restore command.

Settings under `plugins.hook-checkpoints`:

- `max_files` (200): maximum paths in a turn summary.
- `max_diff_bytes` (262,144): maximum bytes returned for one on-demand diff.
- `max_workspace_bytes` (67,108,864): Git-visible workspace-size limit for snapshots. Over-limit snapshots are skipped and a durable warning is emitted; the user prompt continues.
