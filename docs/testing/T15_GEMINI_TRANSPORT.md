# T15 — Gemini transport-error classification

Status: locally integrated and verified; G1/global qualification remains non-green.

The adapter no longer accesses nonexistent enum members on ProviderErrorKind
(the SDK uses literal strings). HTTPX TimeoutException becomes a retryable SDK
`timeout`; other TransportError instances become retryable `unavailable`.
The same mapping covers initial send and SSE iteration. Cancellation remains
CancelledError; responses close through the existing finally block. Retryability
is classification, not permission to replay uncertain side effects automatically.

## Evidence (local ignored `.venv/evidence/`)

- Luna worker changed only Gemini provider/tests in its isolated baseline-HEAD
  worktree. Import probes confirmed it tested that tree, not the parent install.
- `luna-t15/fail-first.xml`: **3 failed, 1 passed** in the initial selected cases.
- `luna-t15/green-gemini.xml`: worker **11 passed** on Python 3.12.
- `t15-parent-py312.xml`: parent **11 passed**, after byte-for-byte transfer checks.
- Parent added two real Commands/runner integration controls: connection timeout
  and interrupted SSE read become typed turn errors, never turn.completed.
- `t15-integrated-py312.xml`: **13 passed in 12.83s**.
- `t15-integrated-py311.xml`: **13 passed in 7.43s**.
- `t15-parent-r14.xml`: original R14 passes (18 unrelated probes deselected).
- Combined provider/runner/ledger subsets: **46 passed on each Python version**
  in `t14-t15-integrated-py312.xml` and `t14-t15-integrated-py311.xml`.
- Luna's source-only review of the worker candidate found no actionable issue.
  Parent separately verified the additional integration tests and both versions.

Tests cover ConnectTimeout, ConnectError, ReadTimeout, cancellation, and an actual
local TCP response that closes early after emitting partial SSE text. That peer
reproduces a real HTTPX stream/protocol transport failure, not a replacement tool
executor. Remaining synthetic transport injections isolate error classification;
they are not live Gemini requests. No golden/assertion/deadline was weakened.
Media serialization remains T13; semantic SSE termination validation and full
live-provider qualification are not claimed by this transport-only repair.
