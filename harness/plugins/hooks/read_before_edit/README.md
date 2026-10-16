# Read Before Edit (`hook-read-before-edit`)

## Summary
`edit_file` only works if the SEARCH block matches the file byte for byte. A model editing a file it
has not read is guessing, and a model editing a file that changed since it read it is editing
yesterday's copy. This guard checks the read ledger at `PRE_TOOL_USE` and refuses the edit with an
instruction to read first (or warns, in `mode=warn`).

## Configuration
| Key | Default | Effect |
| --- | --- | --- |
| `plugins.hook-read-before-edit.mode` | `block` | `block` refuses, `warn` annotates, `off` idles — one switch, so there is no second boolean to disagree with it. |
| `plugins.hook-read-before-edit.exempt_globs` | `[]` | Workspace-relative globs to ignore. |
| `plugins.hook-read-before-edit.guarded_tools` | `["edit_file", "write_to_file"]` | Tools subject to the check. |
| `plugins.hook-read-before-edit.read_tools` | `["view_file"]` | Reads that satisfy the check. |

## Extension points
One `PRE_TOOL_USE` hook returning `HookOutcome(action="block", reason=…)`. The kernel already renders
that as a `tool.result` with `metadata.blocked_by = "hook-read-before-edit"`, so no extra event is
needed.

## Model experience
On a file never read:

```
edit_file blocked: app.py has not been read in this session. Read it first.
```

On a file whose mtime moved past the read (another agent, `git checkout`, a `sed` the model ran):

```
edit_file blocked: app.py has not been read in this session (or changed since it was read at step 4). Read it first.
```

`write_to_file` of a *new* path is always allowed — there is nothing to have read — and a successful
write or edit counts as a read of the new content, so editing right after writing is not blocked.

## Known limitations
* The ledger is the transcript, so a read that happened outside the kernel (a `cat` inside
  `run_command`) does not count. `exempt_globs` is the escape hatch.
* Staleness uses mtime with a 1 s slack; a filesystem with coarser timestamps can under-report a
  change made in the same second as the read.
* It guards by path. A symlink into a different file, or an edit through a tool not listed in
  `guarded_tools`, is out of reach here — the workspace jail remains the security boundary.
