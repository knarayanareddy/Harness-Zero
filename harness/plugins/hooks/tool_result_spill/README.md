# Tool Result Spill (`hook-tool-result-spill`)

## Summary
Results longer than `threshold_chars` are written verbatim to
`.harness/sessions/<session>/tool-output/<call_id>.txt` and the model is told where the file is. It
runs at `TOOL_RESULT_FINALIZE` (order 800) — after the handler, before the kernel's truncation — so
the copy on disk is the *complete* output, not what survived the cap.

## Configuration
| Key | Default | Effect |
| --- | --- | --- |
| `plugins.hook-tool-result-spill.enabled` | `true` | Master switch for this guard. |
| `plugins.hook-tool-result-spill.threshold_chars` | `8192` | Spill anything longer. |
| `plugins.hook-result-spill.max_files_per_session` | `200` | Spool stays bounded; oldest files are deleted. |
| `plugins.hook-tool-result-spill.exempt_tools` | `["view_file"]` | `view_file` is already an explicit read of a range. |

Disable with `harness settings set kernel.plugins.disabled '["hook-tool-result-spill"]'` to drop the
plugin entirely, or flip `enabled` to keep it loaded but idle.

## Extension points
Fires one `TOOL_RESULT_FINALIZE` hook and returns `HookOutcome(action="mutate")` with the appended
note. It sets `metadata.spill_path` / `spill_relpath` / `spill_chars`, which is the contract the
pruner reads — any other guard can too.

## Model experience
Appended block, exactly:

```
Full output saved to .harness/sessions/<sid>/tool-output/<call_id>.txt (N chars). Use view_file with offset/limit to read more.
```

The path is workspace-relative, so the `view_file` call the note suggests is a legal call, not an
invitation to escape the jail.

## Known limitations
* The spool is plain files under the session directory: nothing reaps it when a session is deleted,
  and there is no per-turn budget, only the per-session file count.
* A hook that runs after this one and rewrites the result entirely (the pruner does) removes the
  appended note; the path survives in its own notice instead.
