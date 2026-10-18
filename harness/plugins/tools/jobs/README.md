# tools-jobs

## Summary

Provides durable, session-scoped background processes. A job is launched through the configured executor and security sandbox, its start/output/exit facts are recorded in the session transcript, and its captured output is available from a bounded session spill file. After a daemon restart, jobs that were still marked active are closed as `lost` and included in a model-visible notice on the session's next turn.

## Configuration

<!-- GENERATED table — `harness catalog readme tools-jobs` -->
| Key | Default | Meaning |
|---|---|---|
| `plugins.tools-jobs.max_jobs` | `20` | Maximum retained jobs per session; oldest exited jobs are pruned before a new job starts. |
| `plugins.tools-jobs.ring_bytes` | `65536` | In-memory output tail retained per job before reading the session spill file. |
| `plugins.tools-jobs.spill_max_bytes` | `52428800` | Maximum bytes retained in each session-scoped job output spill file. |

The spill file lives at `.harness/sessions/<session_id>/jobs/<job_id>.log`. Older bytes are trimmed when the configured limit is reached; `job_output` reports whether its result was truncated and the absolute output offsets that remain available. The in-memory ring is used for compact status summaries and the final twenty-line tail.

## Extension points

The plugin registers a `jobs` service and exposes a session-bound `JobsView` through `ToolContext.jobs`. Other plugins can use that view without supplying a session id themselves. Jobs launch through the SDK `Executor`/`Process` contracts, so the active executor and its sandbox policy remain authoritative. The `plugins.tools-jobs.*` settings tune retention; the plugin also contributes the `jobs` panel and its list/output/kill commands.

## Tools and commands

- `job_start {command, cwd?, label?}` starts a background process through the configured executor. `cwd` must resolve inside the workspace. This is an execution-risk tool and follows normal permission and sandbox policy.
- `job_output {job_id, tail_lines?, since_offset?}` reads the recent tail or bytes after an absolute output offset.
- `job_list {}` lists jobs in the current session.
- `job_kill {job_id}` stops a running job through the normal execution-risk gate.
- Web panel commands `plugins.tools-jobs.list`, `plugins.tools-jobs.output`, and `plugins.tools-jobs.kill` back the Jobs pane. The panel's stop action uses the normal approval broker; it is blocked outright while `kernel.security.destructive_blocking` is enabled.
- `manage_task` remains available as a deprecated one-minor-version compatibility alias for status, wait, kill, stdin input, and list operations.

## Model experience

`job_start` returns after process creation instead of waiting for completion, so a long-running job does not hold the turn open. The model can use `job_list` and `job_output` to inspect it; if it finishes between turns, a model-visible notice names the job and includes its tail. `job_kill` is a normal execution action and follows the active approval policy.

## Durable lifecycle

The transcript receives `job.started`, coalesced `job.output`, and `job.exited` records. Output chunks are flushed as live events no more than 250 ms apart during normal streaming, and each durable output record includes the byte range and spill path rather than duplicating unbounded output into the transcript. A terminal record includes the exit code, reason, duration, and last twenty output lines.

The per-session `index.jsonl` stores the latest recoverable job state. At daemon startup, active snapshots are closed with reason `lost`; their existing spill output is retained and the next turn receives one notice per unreported job. Completed jobs are retained up to `max_jobs`, after which the oldest exited job and its spill file are pruned.

## Known limitations

Jobs do not bypass the configured executor, permission gate, or sandbox. A command that requires network or broader filesystem access must be run under a preset that permits it. Job commands and output are visible to users with access to the workspace session. Job IDs and session IDs are validated before they are used as paths, and session storage rejects symlink escapes.

The service cannot reattach a live process handle after daemon reconstruction; persisted active jobs are therefore marked lost rather than reported as running. `manage_task` can send stdin only while the live process handle exists; stdin is not replayed after restart. No cron scheduling or cross-session job control is provided.

## Verification

```bash
pytest tests/kernel/test_jobs_through_kernel.py tests/kernel/test_jobs_survive_restart.py -q
```
