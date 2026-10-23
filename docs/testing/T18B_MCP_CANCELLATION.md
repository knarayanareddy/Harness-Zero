# T18b — in-flight MCP CLI cancellation

Status: **locally integrated, tested and independently source-reviewed**. Whole T18
remains open for canonical audit/read-ledger behavior and declared broader limits.

The shipped stdio CLI reads control messages while one real gate call is active.
The connection owns its call/event/task, and the callback forwards that event to
the existing gate/ToolContext cleanup path while retaining the session turn lock.
Further tool calls fail busy rather than creating an unbounded queue. One writer
serializes responses. A valid matching cancellation suppresses the cancelled
request's response; malformed, unknown, completed and initialize cancellations do
not authorize stopping unrelated work. Booleans/floats do not alias integer IDs.

Requests admit each typed string/integer ID once per connection. Reuse is rejected
before dispatch, preventing a stale cancellation from matching a newer call.
The internal finite budget is 4,096 IDs. Exhaustion fails closed and settles owned
active work with an error rather than reporting successful completion. This is
not an undo guarantee for an effect committed before cancellation.

EOF ends admission. An already accepted call gets at most 100 ms to finish and
flush its ordinary response; remaining active work is then cancelled and awaited.
Explicit cancellation notifications do not receive this drain grace. The
cooperative command fixture still exits/reaps under the unchanged two-second
EOF bound. No claim is made that arbitrary uncooperative trusted plugin code can
be forcibly stopped inside the interpreter.

Every response flush also has a conservative **one-second** compatibility limit:
a peer that stops draining output is disconnected and undeliverable output is
aborted. Once EOF is observed, the active wait and response flush share the
remaining100 ms deadline. Already-ready EOF takes priority over same-turn task
completion. An inline flush which began before EOF dispatch is bounded by the
one-second limit, not retrospectively claimed to finish within100 ms.

Primary references inspected by parent:
- https://modelcontextprotocol.io/specification/2024-11-05/basic/messages
- https://modelcontextprotocol.io/specification/2024-11-05/basic/utilities/cancellation
- https://modelcontextprotocol.io/specification/2024-11-05/basic/lifecycle

## Evidence (`.venv/evidence/`, local/ignored)

- Worker serial-loop baseline and later reused-ID regressions fail first. After
  the ID fix, 29 worker checks pass per Python version; independent source review
  verifies the actual candidate and scoped patch with no remaining findings.
- Two cold CLI startup attempts timed out; later single/subset checks pass.
  All attempts remain recorded; the startup deadline is not increased.
- Parent hashes all four destinations against the worker seed before integration.
  A parent negative test times out waiting for tools/list during the active call
  (`t18b-parent-red.xml`), demonstrating the serial-dispatch seam independently.
- Parent keeps every worker assertion/deadline. Synthetic children publish
  readiness only after installing their SIGTERM handler. The late integer control
  ID is outside all busy-retry IDs admitted within the bounded connection budget.
- Initial parent combined subset: 92 checks pass per version, source-reviewed.
  However, the broader original-probe run then records **14 passed / one R04
  failure** because immediate EOF cancelled/suppressed a short allowed read.
  That earlier candidate is not accepted merely because its subset was green.
- New real-CLI half-close read regression fails first (`t18b-half-close-red.xml`).
  The bounded drain restores its response without weakening active-command EOF
  cleanup, cancelled-response suppression, reaping or negative marker assertions.
- Final combined CLI/server/client/media/provider subset: **93 passed per Python
  version** (`t18b-half-close-fixed-py312.xml` and `...-py311.xml`). All **15
  previously repaired original probes pass together on Python 3.12**
  (`repaired-originals-half-close-fixed-py312.xml`). Ruff and diff whitespace pass.
- Final candidate snapshot:
  `faa8364e7a1c4dc20bd77f243ecf0b5b15dbe36ee56dd08f7bcef7b030d5b8c2`.
  Complete parent-base-bound patch SHA:
  `12fbab2f518e1a3882a0f57c326487e65e446ba1345d3f05a9a0651a761dff82`.
  That intermediate source review independently verified the snapshot/patch, but found that
  the EOF grace bounds task completion, not the subsequent writer.drain. A slow
  reader can still block flushing. Completion may also win before EOF dispatch,
  so only adding a deadline inside the EOF helper is insufficient. A new isolated
  worker repaired bounded output ownership; that earlier snapshot is historical,
  not the finally accepted candidate.

## Final output-backpressure repair

Two blocked-drain unit regressions fail first in the follow-up worker. The
private flush-timeout path aborts output, stops admission and awaits owned call
cleanup. Parent keeps production byte-identical to the worker, replaces a fixed
10 ms correctness sleep with an observed-EOF event, and publishes synthetic JSON
readiness markers atomically after handler installation. Existing real-CLI
cancellation/EOF/half-close, strict two-second cleanup and one-second headless
assertions remain unchanged. The two writer-injection tests are not described
as a real backpressured CLI-process e2e test.

- Final parent combined subset: **95 passed per Python version**
  (`t18b-output-parent-py312.xml`, `...-py311.xml`). All **16 locally repaired
  original probes pass together on3.12** after the separately accepted T25 fix.
- The worker's exploratory3.11 combined attempt timed out after17 progress dots.
  Final focused worker groups and final combined parent runs complete; the root
  cause is not established or claimed solved. Its earlier empty-marker fixture
  race is addressed by atomic readiness publication, not longer timeouts.
- Independent Luna source review verifies final snapshot
  `e5b13bc4293efa884c2e9185f5e2046adb7812868d437e1bf5a43a6db5184f0c`
  and actual full parent-base-bound patch SHA
  `f7a9ed83bc53c88c72a8a0e1394ef733da2867290472a34d7d891d1154de73e5`
  with no findings. Reviewer does not run tests. Parent rechecks both before
  accepting; see `t18b-acceptance-receipt.json`.

The process observations use PID plus a unique command token only for testing;
they add no numeric PGID kill fallback. The native supervisor/process lifecycle
remains T06's existing implementation. The real CLI command/EOF/ID tests are not
replaced by the separate small-budget unit test's synthetic async handler.

Canonical request/result audit and successful-read ledger projection remain
unrepaired: `review/supplemental/test_mcp_read_ledger.py` is explicitly red.
This slice is not full T18 acceptance, external MCP-host interoperability, a
fresh full-suite pass, live-provider qualification or a packaged artifact claim.
