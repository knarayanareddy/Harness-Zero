# WS-03 — Loop hygiene bundle: pruner, spill-to-file, repeat-call reminder, cooperative timeout, read-before-edit

| | |
|---|---|
| Status | specified |
| Depends on | WS-01 (each guard ships as a scenario) |
| Effort | 2 days |
| Principles | P1, P2, P6 |
| dsh reference (@86a40220) | `packages/features/tool-result-pruner/` (head 4,096 / tail 1,024 chars at 8,192 threshold, applied at the model boundary), `packages/features/tool-result-store/` (spill oversized output to a file the model can page through), `packages/features/repeat-tool-call-reminder/`, `packages/core/tool-execution/` (cooperative timeout + abort signal), `packages/features/file-edit-guard/` (read-before-edit, stale-read detection) |
| Harness-Zero touchpoints | `harness/kernel/gate.py:613-623` (step 7: `asyncio.timeout(spec.timeout_s)`, no cancellation token reaches the handler), `:625-660` (step 8: POST_TOOL_USE hooks; `additional_context` is **appended** to result, `block` replaces), `:663-690` (step 9: sanitise + byte truncation `kernel.limits.tool_result_bytes` 65536, suffix `[TRUNCATED DUE TO SIZE LIMIT]`), `harness/kernel/runner.py:65-72` (`max_iterations` 20, no repeat detection), `harness/sdk/types.py:327-337` (`ToolSpec.timeout_s` 600), `harness/sdk/context.py:97-127` (`ToolContext` — no deadline/cancel), `harness/sdk/types.py:822-840` (`HookEvent`, `HookOutcome`), `harness/plugins/tools/fs/tools.py` (`view_file` L16, `write_to_file` L45, `edit_file` L68), `harness/plugins/hooks/checkpoints/` (only existing hook plugin — the pattern to copy), `harness/sdk/paths.py` (`harness_dir`, `sessions_dir`) |

## 1. Outcome

Five independent guards, each its own plugin under `harness/plugins/hooks/`, each individually switchable, each with a kernel-level test and a scenario:

| guard | model sees | when |
|---|---|---|
| **pruner** | the head and tail of an oversized result plus a one-line notice with the omitted char count and, if spilled, the path | result > `threshold_chars` (default 8,192), before truncation |
| **spill** | `Full output saved to .harness/sessions/<sid>/tool-output/<call_id>.txt (N chars). Use view_file with offset/limit to read more.` | result > `spill_threshold_chars` (default = pruner threshold) |
| **repeat reminder** | `Note: this is the 3rd identical call to run_command with the same arguments in this turn. The result has not changed. Consider a different approach or explain why repeating is needed.` | identical `(name, canonical args)` ≥ `repeat_after` (default 3) within one turn |
| **cooperative timeout** | `Tool 'run_command' exceeded 600s and was cancelled; partial output (if any) follows.` | handler exceeds `spec.timeout_s`; the handler receives a cancel signal first and gets `grace_s` (default 5) to return partial output |
| **read-before-edit** | `edit_file blocked: app.py has not been read in this session (or changed since it was read at step 4). Read it first.` | `edit_file`/`write_to_file` on an existing file never `view_file`d in the session, or whose content hash changed since the last read |

Everything the model sees is also what the UI shows (P1): the pruned text is the record; the full text exists only in the spill file.

## 2. Current state (verified)

- Oversized results are cut at 64 KiB bytes with a suffix — the tail (where test failures and stack traces live) is the part that is lost. There is no spill, so the information is gone for the model.
- No repeat detection: a model that retries the same failing command burns the whole `max_iterations` budget silently.
- `asyncio.timeout` cancels the handler coroutine abruptly; nothing reaches the subprocess (an executor `Process` may keep running), no partial output is kept.
- `edit_file` works on files the model has never read; the review and Addendum A didn't touch this, but `dsh`'s file-edit guard is the single most effective "don't clobber" measure in practice.
- Hook dispatch: step 8 appends `additional_context` text to the result — adequate for the reminder, and `block` is adequate for read-before-edit at step 2 (`PRE_TOOL_USE`). The pruner/spill, however, must run at **step 9 before truncation** and needs the *final* result, so a new hook event is required rather than abusing POST_TOOL_USE ordering.

## 3. Design

### 3.1 Kernel changes (small, SDK-visible)

1. **New hook event** `HookEvent.TOOL_RESULT_FINALIZE` fired in step 9 **before** sanitise/truncate with payload `{"call": ToolCall, "result": ToolResult, "session_id", "step_index"}`; outcome `mutate` may replace `result` (new field `HookOutcome.result: ToolResult | None`). Hooks at this event run in `order` (add `order: int = 500` to the `Hook` protocol with default; pruner = 900 so it runs last).
2. **Cooperative cancellation**: `ToolContext` gains `deadline: float | None` (monotonic) and `cancel_event: asyncio.Event`. Step 7 becomes: start handler; on timeout set `cancel_event`, wait `grace_s`; if still running, cancel the task. Executors' `Process.terminate()` is called for any process registered via `ctx.background()` or tracked by the local executor for this `call_id`. Partial output captured through `emit_output_delta` is attached to the timeout error result.
3. **Turn-scoped scratch** for hooks: `hook_ctx.turn_state: dict` (per turn, cleared by the runner) so the repeat counter and the read-ledger don't need globals.
4. **Read ledger as a record metadata**: `view_file` results get `metadata["read_sha256"]` + `metadata["path"]`; the read-before-edit hook scans the session's transcript records (`ctx.session.read()`) for the last read of that path — no private store, P1-compliant and survives restarts.

### 3.2 Plugins (one per guard)

```
harness/plugins/hooks/
├── tool_result_pruner/     # TOOL_RESULT_FINALIZE, order 900
├── tool_result_spill/      # TOOL_RESULT_FINALIZE, order 800 (spills first so the pruner can reference the path)
├── repeat_call_reminder/   # POST_TOOL_USE (additional_context)
└── read_before_edit/       # PRE_TOOL_USE (block)
```

Cooperative timeout is kernel behaviour (no plugin) with settings `kernel.limits.tool_grace_s` (5).

Settings (each in its plugin's `settings.schema.json`, namespace `plugins.<name>.`):

| key | default | |
|---|---|---|
| `plugins.hooks-tool-result-pruner.enabled` | true | |
| `…pruner.threshold_chars` | 8192 | |
| `…pruner.head_chars` / `…tail_chars` | 4096 / 1024 | |
| `…pruner.exempt_tools` | `["view_file"]` | paging tools must not be pruned |
| `plugins.hooks-tool-result-spill.enabled` | true | |
| `…spill.threshold_chars` | 8192 | |
| `…spill.max_files_per_session` | 200 | oldest removed |
| `plugins.hooks-repeat-call-reminder.repeat_after` | 3 | |
| `…reminder.block_after` | 0 (off) | `> 0` turns the N-th repeat into `block` |
| `plugins.hooks-read-before-edit.enabled` | true | |
| `…read-before-edit.mode` | `block` | `block` or `warn` (additional_context) |
| `…read-before-edit.exempt_globs` | `[]` | |

### 3.3 Pruner algorithm (deterministic, char-based, UTF-8 safe)

```
if total <= threshold: unchanged
head = text[:head_chars] cut back to the last newline; tail = text[-tail_chars:] cut forward to the first newline
notice = f"\n\n[... {omitted} chars omitted ({lines} lines){'; full output: ' + spill_path if spill_path else ''} ...]\n\n"
result.metadata["pruned"] = {"omitted_chars": omitted, "spill_path": spill_path}
```

Runs on the concatenation of `TextBlock`s; `ImageBlock`s untouched; `is_error` results use `tail_chars = max(tail_chars, 2048)` because error tails matter more.

### 3.4 Repeat reminder

Canonical key = `sha256(name + json.dumps(args, sort_keys=True))`; counter in `turn_state["repeat_counts"]`. Text includes the count and whether the previous result was identical (`sha256` of result text kept alongside). Emits `tool.repeat_detected` event (`durable: false`) for the UI badge.

### 3.5 Read-before-edit

On `PRE_TOOL_USE` for `edit_file`/`write_to_file` (tool list configurable): if target exists and no `view_file` record in this session has `metadata.path == target`, or the file's current `sha256 != metadata.read_sha256` of the latest read → `block` with the message above. `write_to_file` on a **new** file is allowed. `mode: warn` emits `additional_context` instead (and the gate still runs the tool).

## 4. Files

### ADD

```
harness/plugins/hooks/tool_result_pruner/{__init__.py,harness_plugin.toml,plugin.py,pruner.py,settings.schema.json,README.md,tests/test_conformance.py}
harness/plugins/hooks/tool_result_spill/{…same…,spill.py}
harness/plugins/hooks/repeat_call_reminder/{…same…,reminder.py}
harness/plugins/hooks/read_before_edit/{…same…,guard.py}
tests/kernel/test_tool_result_finalize_hook.py
tests/kernel/test_cooperative_timeout.py
tests/kernel/test_loop_hygiene_through_kernel.py
tests/scenarios/{huge_tool_output_pruned_and_spilled,repeat_call_reminder,tool_timeout_partial_output,edit_without_read_blocked}/…
```

### MODIFY

```
harness/sdk/types.py            # HookEvent.TOOL_RESULT_FINALIZE; HookOutcome.result; ToolContext deadline/cancel_event (context.py protocol + kernel impl)
harness/sdk/contracts.py        # Hook.order (default 500)
harness/kernel/gate.py          # step 7 cooperative timeout + grace; step 9 fires TOOL_RESULT_FINALIZE before truncation; passes turn_state in hook_ctx
harness/kernel/runner.py        # creates/clears turn_state per turn; passes to gate
harness/kernel/settings.py      # kernel.limits.tool_grace_s
harness/plugins/tools/fs/tools.py   # view_file: metadata path + read_sha256 (+ offset/limit args if missing — check)
harness/plugins/executors/local/    # Process registry per call_id so timeout can terminate; honours cancel_event
docs/events.schema.json         # via catalog gen: tool.repeat_detected, tool.result.pruned (live)
```

### Skeleton: `pruner.py`

```python
@dataclass
class PrunerSettings:
    threshold_chars: int = 8192; head_chars: int = 4096; tail_chars: int = 1024; exempt_tools: tuple[str, ...] = ("view_file",)

class ToolResultPruner:
    name = "tool_result_pruner"
    events = frozenset({HookEvent.TOOL_RESULT_FINALIZE})
    order = 900
    def __init__(self, settings: PrunerSettings): self.s = settings

    async def on(self, event, payload, ctx) -> HookOutcome:
        call, result = payload["call"], payload["result"]
        if call.name in self.s.exempt_tools: return HookOutcome(action="continue")
        text = "".join(b.text for b in result.content if isinstance(b, TextBlock))
        if len(text) <= self.s.threshold_chars: return HookOutcome(action="continue")
        pruned, omitted = prune(text, self.s, is_error=result.is_error, spill_path=result.metadata.get("spill_path"))
        new = ToolResult(content=[TextBlock(text=pruned), *[b for b in result.content if not isinstance(b, TextBlock)]],
                         is_error=result.is_error, metadata={**result.metadata, "pruned": {"omitted_chars": omitted}}, cards=result.cards)
        ctx.emit("tool.result.pruned", {"call_id": call.id, "omitted_chars": omitted})
        return HookOutcome(action="mutate", result=new)
```

### Skeleton: gate step 7 (cooperative timeout)

```python
cancel = asyncio.Event(); tool_ctx.cancel_event = cancel; tool_ctx.deadline = loop.time() + spec.timeout_s
task = asyncio.create_task(handler(current_args, tool_ctx))
try:
    result = await asyncio.wait_for(asyncio.shield(task), timeout=spec.timeout_s)
except TimeoutError:
    cancel.set()
    await self.executor_registry.terminate_for_call(call.id)          # best effort, executors implement
    try:
        result = await asyncio.wait_for(task, timeout=grace_s)         # handler may return partial output
    except (TimeoutError, asyncio.CancelledError):
        task.cancel(); result = ToolResult.error(f"Tool '{spec.name}' exceeded {spec.timeout_s}s and was cancelled")
    partial = tool_ctx.drain_output_buffer()
    if partial: result = result.with_prefix_text(f"Tool '{spec.name}' exceeded {spec.timeout_s}s; partial output:\n{partial}\n")
```

## 5. Tests (fail-first)

| test | fails on main because |
|---|---|
| `test_tool_result_finalize_hook.py::test_hook_can_replace_result_before_truncation` | event doesn't exist |
| `test_loop_hygiene_through_kernel.py::test_oversized_output_keeps_tail` | a 200 KB result containing `FAILED test_x` on the last line: model's `ToolResultBlock` must contain `FAILED test_x` — today it's truncated away |
| `::test_spill_file_is_readable_with_view_file` | model reads the spill path with `view_file` and gets the full content (through the jail: spill path is inside `<ws>/.harness`) |
| `::test_third_identical_call_gets_reminder` | no reminder |
| `::test_edit_unread_file_is_blocked` / `::test_edit_after_read_is_allowed` / `::test_edit_after_external_change_is_blocked` | no guard |
| `test_cooperative_timeout.py::test_subprocess_is_terminated_on_timeout` | `sleep 30` via `run_command` with `timeout_s=1`: process must be gone within `grace_s`; today it leaks |
| `::test_partial_output_survives_timeout` | partial output discarded |
| scenarios (4) | new |

## 6. Acceptance

```bash
pytest tests/kernel/test_loop_hygiene_through_kernel.py tests/kernel/test_cooperative_timeout.py tests/kernel/test_tool_result_finalize_hook.py -q
pytest tests/scenarios -q -k "pruned or repeat or timeout or edit_without_read"
harness plugin doctor --all && harness plugin test --all && harness catalog --check
# manual: harness run -p mock … "run `yes | head -c 300000`" → UI shows head/tail + spill notice; .harness/sessions/<sid>/tool-output/<call>.txt exists
# teeth: disable plugins.hooks-tool-result-pruner.enabled → test_oversized_output_keeps_tail fails
```

## 7. Non-goals / cautions

- The byte truncation in step 9 stays as a hard backstop (`tool_result_bytes`); the pruner should make it unreachable in practice.
- Do not prune `ImageBlock`s or card payloads.
- Spill files live under `<ws>/.harness/sessions/<sid>/tool-output/` — inside the jail so `view_file` can read them; they are **not** artifacts (no `/artifacts` exposure) and are cleaned with the session.
- Read-before-edit uses the session's transcript, so after compaction the read records are still on disk (compaction summarises the *messages*, not the store) — verify with a test, don't assume.
- Repeat detection must not count tools whose repeat is legitimate (`manage_task` polling, `wait_task`): exempt list defaults to `["manage_task"]`.

## 8. Agent brief

> Implement WS-03 from `review/dsh-integration/WS-03_loop_hygiene.md` as **two PRs**: (a) kernel seams — `HookEvent.TOOL_RESULT_FINALIZE` with `HookOutcome.result`, `Hook.order`, `turn_state`, cooperative timeout with `kernel.limits.tool_grace_s` and executor `terminate_for_call`, `view_file` read metadata — with `tests/kernel/test_tool_result_finalize_hook.py` and `test_cooperative_timeout.py` failing first; (b) the four hook plugins (`harness plugin new --kind hook …`, layout per `templates/PLUGIN_TEMPLATE.md`) with `tests/kernel/test_loop_hygiene_through_kernel.py` and the four scenarios. All model-visible texts are the exact strings in §1; put them in each plugin README "Model experience" section. Settings keys per §3.2; `harness catalog --check settings` must pass. Open PRs `WS-03a: tool result finalize hook + cooperative timeout` and `WS-03b: loop hygiene hooks`.
