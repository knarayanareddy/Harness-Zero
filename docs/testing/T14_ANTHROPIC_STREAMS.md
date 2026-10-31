# T14 — Anthropic SSE terminal-state correctness

Status: locally integrated and verified; G1/global qualification remains non-green.

The adapter now raises typed ProviderError for HTTP-200 SSE error events and for
EOF before message_stop. It no longer fabricates successful UsageEvent/Done at
premature EOF. Error types map to the existing SDK kind/retryability vocabulary;
cancellation is not caught as an ordinary provider failure. Normal message_delta
output usage is cumulative, so the latest snapshot replaces, rather than adds to,
the previous output count.

Primary reference consulted by the Luna worker and reviewer:
https://platform.claude.com/docs/en/build-with-claude/streaming — error events,
message_stop termination and cumulative usage. No live Anthropic request was made.

## Evidence

- Worker isolated worktree: `luna-t14-anthropic`, baseline HEAD, only three
  allowlisted files changed. Import probe confirmed that worktree's modules.
- `.venv/evidence/luna-t14/provider-red.xml`: **5 fail-first failures**.
- `kernel-red2.xml`: corrected local-peer setup gives **3 failed, 1 passed**;
  initial setup-error evidence is retained separately, not called defect proof.
- `focused-green.xml`: worker **19 passed** on Python 3.12.
- `t14-parent-py312.xml`: parent independently verified the same 19 checks after
  confirming all three transferred files byte-identical to the candidate.
- Parent removed one unused test import found by Ruff; no production difference.
  Added deterministic 1/2/17-byte HTTPX byte-stream UTF-8 controls, in addition to
  the real TCP peer's three-byte writes (which the OS may coalesce).
- `t14-t15-integrated-py312.xml`: **46 passed in 7.56s**.
- `t14-t15-integrated-py311.xml`: **46 passed in 5.77s**.
  Each combined subset contains 22 Anthropic, 13 Gemini, 9 OpenAI and 2 ledger
  checks. This is not a full-suite result.
- `t14-t15-original-probes.xml`: original R09, R14 and R02 pass (3 selected).
- Luna source-only review found no actionable issue; executions were verified by
  the parent, not independently repeated by that reviewer.

The local HTTP/SSE peer exercises valid text/tool/thinking events, split UTF-8,
explicit error events and early EOF. Real TurnRunner/CircuitBreaker controls
report error/open state rather than success. Cancellation propagation is tested.
No assertion, timeout, recording or golden was weakened. Pending image fields,
general malformed-SSE schema validation, partial-failure billing and live vendor
qualification are not silently included in this slice's support claim.
