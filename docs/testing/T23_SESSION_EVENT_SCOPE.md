# T23 — session-scoped Studio events and actions

Status: scoped event/action behavior locally verified and source-reviewed.
R07 and broader UI qualification remain open under T24.

Session-only handlers require the selected session's envelope identity. Global
notices/settings retain their behavior. Switching clears old streaming and
interaction transients; durable plugin facts retain their named SSE registrations.

## Evidence (`.venv/evidence/`, ignored)

- Reviewed snapshot `430d68c8622baa6508c09447452b2d4f5da7c00a850dcb0627ed875fd8c021f4`;
  complete patch `db0a7ccdf85cf0b683187234f12b7fb50a11ea3b4d9bb87bb1dd81668fbac3fa`.
  Independent review verifies both; parent destination checks and byte comparisons
  establish the exact reviewed code was integrated and served. Reviewer runs no tests.
- Four semantic jsdom cases run on Node22.23.3 through a required pytest wrapper.
  Worker frontend group:35 passes per Python version. Parent frontend/bus/ledger
  group:71 passes on3.12. These overlap and are not summed into an invented suite.
- Initial actual browser checks show A's first stream, switching to B, and real A
  late text/approval arriving over SSE without painting B. Platform interruptions
  are preserved in `t23-browser-interruption-checkpoint.json`, not called a full journey.
- Isolated Chromium153/Playwright1.63 continuation uses the same persisted synthetic
  sessions, real HTTP/SSE, runner, gate and temporary files. Explicit new fixture
  turns restore uncompleted pending work; no transcript/tool action is automatically replayed.
- A's actual cancellation leaves B busy with the same pending approval. Native B
  approval completes its real handler: marker-B.txt contains APPROVED_B and marker-A
  remains absent. The global plan-mode badge updates. The owned server exits0 and
  both ports close. Earlier positive checks are retained, not rerun.
- Raw evidence: `t23-headless-continuation-20261023/restoration-result.json` and
  executed scripts. Parent verifies all behavior assertions and marker contents.
  Screenshot SHA `cba5eaec826fce0f5aa243b1a7371f9635cecc05ad13ab3e25d6859cef77564a`.
  See `t23-acceptance-receipt.json` for exact provenance and scope.

## Qualified visual result / remaining T24

Parent inspects the screenshot: B's completed result and usable composer are
present, but the approval card retains an Approval Required header after backend
resolution. This is a presentation/reconciliation follow-up, not evidence that
the backend approval is still pending. Per-card affordances need T24 proof.
Historical fixture errors/cancellations are preserved, not deleted for a cleaner image.

Unchanged original R07 reports foreign_visible=false, foreign_busy=false,
stale_visible=true. T24 must reject stale replies, merge live/snapshot state without
duplicates and reconcile pending/busy display. Browser evidence spans multiple
owned server runs; it is not an uninterrupted journey, latency SLA, full-suite,
native/live-provider or release qualification.
