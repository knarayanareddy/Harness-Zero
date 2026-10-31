# T07a/T07b — crash-tail and committed-record recovery

Status: locally verified; T01/global qualification stays non-green. The store
contract remains single-writer, same-filesystem process restart, not power loss
or multi-process transactions. No committed bytes are rewritten or discarded.

## Mechanism

- Before append, both log files are opened. Any unterminated physical tail is
  sealed with an appended newline, preserving its original bytes in place.
  A valid JSON tail without newline stays readable; corrupt tails are skipped
  with diagnostics rather than consuming the next valid append.
- Counter recovery scans valid indices in **both** files. A full-only residue
  of an unacknowledged failed dual write keeps its index reserved; corrupt
  physical lines, scalar JSON and invalid index types do not inflate it.
- Read/decode guards reject non-record JSON and per-line decoder limits without
  swallowing filesystem read failures. Actual oversized-integer ValueError was
  reproduced; RecursionError uses explicit decoder fault injection because this
  host handles the real deep-tail fixture as ordinary JSONDecodeError.
- Caller cancellation cannot release the store lock while its filesystem thread
  still commits. The log owns disk append plus canonical cache update as one
  commit boundary, then propagates cancellation. This may wait for outstanding
  functioning I/O; no hard guarantee against a permanently stalled OS is claimed.
- A failed append invalidates the loaded cache for subsequent reload. Full-only
  unacknowledged bytes are retained, not silently reused or called acknowledged.
- Durable emitters select the record named by the returned step, not whatever
  record happens to be last after another writer runs. Hook/plugin, approval,
  gate and runner message/index/notice/marker/turn-start consumers share this
  receipt lookup. SDK record/append return types and log version remain unchanged.

T07 was implemented in small slices: tail/counter/cache (store/log/test), receipt
consumers (gate/broker/helper/test), then hook/plugin/runner receipt consumers.
The pending repair method's nonexistent `_records` access is deliberately T08.

## Evidence under ignored `.venv/evidence/`

| Check | Result | Artifact |
|---|---|---|
| Full/compact unterminated tail, prior to fix | Both red: new record swallowed or index counted corrupt line | `t07-red.{log,xml}` |
| Malformed scalar/record/index JSON | Six red controls, then green | `t07-record-shape-red.xml` |
| Cancellation after thread commit vs canonical cache | Red: disk had record but cache did not | `t07-cancel-red.xml` |
| Loaded cache after partial store failure | Red before invalidation; green after | `t07-projection-red.xml` |
| Interleaved append receipts | Approval/gate red, then hook/plugin/marker/index red; all fixed using returned step | `t07-receipt-red.xml`, `t07-emitters-red.xml` |
| Final targeted Python 3.12 subset | **62 passed, 29.68s** | `t07-receipts-final.xml` |
| Final targeted Python 3.11 subset | **62 passed, 40.04s** | `t07-final-py311.xml` |
| Original R16 probe | **1 passed**, 18 unrelated probes deselected | `t07-final-r16.xml` |
| Real writer killed after acknowledged prefix and unfinished tails, then reconstructed | **100 trials**, zero acknowledged records lost, zero next appends swallowed; both logs checked, 16.569s | `t07-100-restarts.json` |
| Independent review | Decoder edge reproduced/fixed; final receipt/failure-invalidation review found no additional actionable issue | Source-only review; executions parent-observed |

Tests also cover exact byte-offset lookup, valid no-newline/high-index records,
full-only failed compact writes, subsequent index reservation, and direct-store
caller cancellation retaining the serialization lock. No fsync/power-loss claim,
format migration, content deletion, test skip, golden or threshold weakening.

## Interrupted diagnostics are not passes

One combined run was interrupted by the harness server restart and left an
orphaned pytest process; a later stop found it had already exited. Its partial
log is retained, not counted. Another broad attempt hit its external 90-second
runner bound without a summary; later selected groups completed and the final
62-test subsets above passed. Earlier red commands that hit outer reporting
limits retain their failure logs, not invented JUnit completion. The old T04
background cancellation also received an explicit server-restart notification;
its interrupted status must not be retroactively called a completed suite.

## Re-run

```sh
python -m pytest -q tests/kernel/test_crash_tail_recovery.py tests/plugins/test_store_plugins.py \
  tests/kernel/test_log_is_source.py tests/kernel/test_durable_events.py tests/kernel/test_cancel_pending_approval.py
python -m pytest -q review/regressions/test_review.py -k R16
python -m ruff check .
```
