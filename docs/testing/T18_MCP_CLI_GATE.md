# T18a — actual MCP CLI read mediation (partial T18)

Status: local text-read slice integrated; **T18 remains open** for complete
request/result audit/read-ledger behavior and other
declared limits. G1/global qualification is still non-green.

The shipped CLI no longer calls nonexistent PolicyGate.execute_tool. Each stdio
connection creates a dedicated session tagged `transport=mcp_stdio` and
`principal=mcp_stdio`, without resuming an unrelated conversation. Calls hold
that session's lock and use the actual gate, empty principal capabilities, real
settings/approval broker and session log. Caller parameters do not grant approval
bypass or create an interactive user. Plugin teardown runs in finally.

## Verified scope

Actual CLI subprocesses perform an explicitly exported allowed text read, reject
missing arguments and outside paths, redact a synthetic registered secret, deny
locked-mode mutation before its handler, and reject a headless write through the
real broker without creating the target. Policy decisions are persisted. No
provider inference or replacement tool executor is used. T17 canonical export
precedence and fail-closed defaults remain intact.

## Evidence (`.venv/evidence/`, local/ignored)

- The Luna execution worker failed DNS after leaving partial source/tests. Those
  files and diagnostic logs were inspected, not treated as a completed result.
  Prototype artifacts named green may contain failures; they remain historical.
- Parent `t18-parent-read-red.xml`: exported read fails before the API binding.
- Parent `t18-parent-initial-green.xml`: **3 passed in 3.40s**.
- Parent `t18a-parent-py311.xml`: **21 CLI/server checks passed in 14.60s**.
- Parent `t18a-t21-parent-py312.xml`: **35 combined CLI/server/client checks passed
  in 38.72s**. The Python 3.11 CLI/server21 and client14 results are separate
  completed runs, not an invented uninterrupted35-pass run.
- Original R04/R11/R13 pass (`t18a-t21-original.xml`).
- Parent retains a strict **one-second** headless tools/call observation bound;
  the worker's relaxed three-second prototype is not adopted. This does not
  resolve the separate existing end-to-end headless-question G1 latency blocker.
- Luna source review verifies snapshot
  `366d8e55e4cbb052191ec31254a440d06b2d36dc13ec4bf9316af107ea397c65`
  with no actionable issue in this bounded slice. Parent verifies the scoped
  patch hash; reviewer does not run tests or independently hash the patch.
  See `t18a-acceptance-receipt.json` for exact scope and remaining work.

## Not yet qualified

The T18a baseline stdio loop was serial and could not consume cancellation while
a tool was awaiting completion. T18b's parent candidate now handles the advertised
notifications/cancelled, stale IDs, EOF cleanup and bounded output flushing with95
combined checks passing per version. Exact parent source/patch review is accepted;
this does not close the remaining audit slice. See
[T18b evidence](T18B_MCP_CANCELLATION.md). Primary reference inspected by parent:
https://modelcontextprotocol.io/specification/2024-11-05/basic/utilities/cancellation

Complete canonical request/result audit and read-before-edit ledger semantics
are not established by the current policy-only log assertions. Non-text tool
responses, background handoff, arbitrary approved Git mutations and live external
host compatibility are not qualified. ZIDE-04 runtime Git-state approval binding
is still pending. No full T18 acceptance or release capability is claimed here.

An explicit supplemental T18 probe now measures the remaining ledger seam:
`review/supplemental/test_mcp_read_ledger.py` runs the actual CLI with a temporary
open-preset workspace. An unread overwrite is blocked and a real read succeeds,
but the subsequent read-backed overwrite is still blocked. Parent fail-first
evidence is `t18-audit-read-ledger-red.{log,xml}` (one assertion failure, no setup
error). This is a T18 remainder, not a new original audit finding or evidence of
an unauthorized mutation. It must become a passing required regression before
full T18 acceptance; cancellation alone will not close that task.
