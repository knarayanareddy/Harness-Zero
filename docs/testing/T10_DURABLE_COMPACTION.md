# T10 — durable compaction and next-request consumption

Status: locally verified slices. Original R15 passes; full/native qualification
remains non-green. No commit, push, live provider or release.

## Mechanism and limits

- The runner passes actual messages, including tool blocks, and the canonical
  log. PRE_COMPACT receives actual log/settings/bus/turn state. After validating
  sections and projected size, compaction awaits the summary append before
  publishing completion with its committed record. An uncommitted draft is not
  announced as completed. Failed writes and invalid/oversized/incomplete summaries
  do not replace the projection or announce success.
- `metadata.covers_through_step` identifies the summarized prefix independently
  of the summary's append position. Normal current prompts and unresolved calls
  stay outside coverage, and appear once in the next request. Subsequent summaries
  consume earlier summaries plus newer work. Raw audit bytes are never removed.
  Legacy summaries without coverage metadata retain physical-boundary slicing.
- An oversized first/text-only prompt has no usable older prefix. That explicit
  fallback summarizes the prompt, preserves its raw record and identifies it with
  `summarized_prompt_step` so assembly does not reinsert the huge text. New prompts
  are not suppressed. Current images and unresolved calls cannot use this fallback.
  This is semantic compression, not guaranteed lossless factual retention.
- Size uses the existing message/system character heuristic, not exact vendor
  tokenization or a new bound on tool-schema tokens. Over-budget retained data
  fails rather than being silently discarded.
- Turn cancellation races the owned compaction task and closes a cooperative held
  stream without false completion. T07's disk/cache ownership remains intact.
  No new hard bound for permanently stalled I/O/uncooperative in-process code.
- Successful summary UsageEvents share the existing inference ledger, unknown
  pricing and post-call spend-cap path; turn totals include summary usage. A cap
  exceeded by summarization stops before summary commit/answer request. This is
  not hard dollar reservation. Partial failed/cancelled stream usage and summary
  circuit-breaker qualification remain broader inference limitations.

## Regression discovered during verification

A completed background task could leave a queued wakeup but no running task.
Both single-loop strategies then ended without consuming it. Deterministic tests
reproduced both paths; they now consume an immediate queued completion even with
no live task, without waiting or sleep-polling. Detached Jobs/restart controls pass.
This small regression slice does not change Jobs ownership.

## Evidence (`.venv/evidence/`, local and ignored)

Red artifacts: `t10-persistence-red.xml` (discarded summary), `t10-cancel-red.xml`
(Stop not propagated within one second), `t10-summary-validation-red.xml`
(malformed summary padded with fabricated sections), `t10-hook-context-red.xml`
(missing log), `t10-first-prompt-red.xml` (oversized first prompt),
`t10-summary-spend-red.xml` (two omitted-usage/cap cases), `t10-wakeup-red.xml`
(both strategies), and `t10-completion-contract-red.xml` (schema/draft facts).

| Completed check | Observed | Artifact |
|---|---|---|
| Broader Python 3.12 / 3.11 subsets before final event declaration/draft guard | **123 passed each**, console 40.67s / 41.73s | `t10-complete-py312.xml`, `t10-complete-py311.xml` |
| Final event/schema/catalog/default-hook subset | **55 passed, 1 platform catalogue skip**, 79.69s | `t10-events-green.xml` |
| Final Python 3.12 compaction file with slow-wait diagnostics | **21 passed**, 92.72s; not a performance pass | `t10-hang-diagnostic.xml` |
| Final Python 3.11 compaction/contract subset | **30 passed**, 35.71s | `t10-focused-py311.xml` |
| Original R01/R03/R15/R16/R17 | **5 passed**, 14 unrelated probes deselected | `t10-five-probes.xml` |
| Runner/Jobs after queued-wakeup repair | **18 passed** | `t10-wakeup-green.xml` |

The strict local TCP HTTP/SSE peer sees actual adapter requests: summary followed
by bounded answer context, current prompt once, old raw text absent. Tests also
cover repeated compaction, reconstructed stores/logs, unresolved-call coverage,
failed summary writes, malformed/oversized/empty/incomplete output, hook/provider
failures, deadlines, cancellation and summary spend-cap enforcement.

### Incomplete/non-green diagnostics are not passes

`t10-final-py312.xml` records **111 passed, 1 failed** before the queued-wakeup
repair, retained unchanged. Later combined attempts (`t10-delivery-py312`,
`t10-final-scope-py312`, `t10-focused-py312`) hit outer runner limits without
complete reports. One partial stream had another failure at the source-of-truth
test position; its full reason was not captured. The unchanged test passed in
`t10-source-diagnostic.xml`; that does not establish its cause or fix. These are
not a final uninterrupted suite pass. Two red-reporting attempts timed out before
the completed completion-contract red artifact. No assertion/golden/product
deadline was relaxed. T09 documents the corrected compaction-only timing oracle.

## Surface inventory

Runtime: compactor, runner, pure context projection and both strategies.
CompactionCompleted now declares `carries_record=true`; regenerated
`docs/events.schema.json` differs by that one flag. Fresh catalogue generation
confirms event metadata/markdown unchanged; unrelated macOS-only tool/coverage
rows are not regenerated. Session-log architecture, changelog, regression ledger
and both roadmaps describe the same behavior. No signed artifact/live-provider
verification or universal lossless-retention claim is made.
