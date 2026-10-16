# Tool Result Pruner (`hook-tool-result-pruner`)

## Summary
The kernel caps a result at `kernel.limits.tool_result_bytes` by keeping the **head** and appending
`[TRUNCATED DUE TO SIZE LIMIT]`. For `pytest`, `npm test`, or a build log that is exactly the wrong
half: the failure summary is at the end. This guard runs at `TOOL_RESULT_FINALIZE` (order 900) and
replaces the result with head + an explicit omission notice + tail, so the cap can no longer silently
eat the diagnosis.

## Configuration
| Key | Default | Effect |
| --- | --- | --- |
| `plugins.hook-tool-result-pruner.enabled` | `true` | Master switch. |
| `plugins.hook-tool-result-pruner.threshold_chars` | `8192` | Shorter results are left alone entirely. |
| `plugins.hook-tool-result-pruner.head_chars` | `4096` | Kept from the start. |
| `plugins.hook-tool-result-pruner.tail_chars` | `1024` | Kept from the end. |
| `plugins.hook-tool-result-pruner.exempt_tools` | `["view_file"]` | Paginated reads are what the model asked for. |

Both budgets are snapped to line boundaries — a half line of a stack frame costs as much to read as a
whole one and carries less.

## Extension points
`TOOL_RESULT_FINALIZE`, `order = 900`, returns `HookOutcome(action="mutate", result=...)`, publishes
the live-only event `tool.result.pruned`, and adds `metadata.pruned / pruned_chars / pruned_lines`.
It reads `metadata.spill_relpath` from the spill guard (order 800) to point at the full file.

## Model experience
The omitted middle is announced, never hidden — exactly:

```
[... 18342 chars omitted (214 lines); full output: .harness/sessions/<sid>/tool-output/<call_id>.txt ...]
```

Without the spill guard loaded the notice simply omits the `; full output: …` part, and the model
knows the rest is gone rather than guessing.

## Known limitations
* Character budgets, not tokens: a result of dense Unicode can still cost more than the budget
  suggests, and the kernel's byte cap remains the real guard.
* It cannot prune a result that is *small but useless* — that is a context-source problem, not this
  hook's.
* Turning it off and leaving the kernel cap alone restores head-only truncation; that is the intended
  way to A/B the guard, and the scenario tests assert the difference.
