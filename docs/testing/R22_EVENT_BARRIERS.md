# R22 — delta coalescing crosses lifecycle boundaries

Status: P2 locally repaired, source-reviewed and verified in the parent.
Owner: T24 follow-up / T29 qualification; broader G1 remains open.
This is distinct from accepted R21's session-identity merge guard.

The actual EventBus receives, for one queued subscriber and one session/turn:

1. model.text.delta A
2. model.completed
3. model.text.delta B

The subscriber receives delta AB followed by model.completed. The later delta is
merged into an earlier queued object across the completion boundary. The event
is not foreign-session data, but its ordering is wrong for lifecycle consumers.

The publish documentation promises consecutive coalescing. The current cached
last-delta pointer survives a matching non-delta publication, allowing this move.
The minimal typed-SDK regression is
`review/supplemental/test_r22_bus_delta_barriers.py`. Each Python3.11/3.12 run has one
genuine expected ordering failure, one passing adjacent-coalescing positive
control, and zero setup errors. Existing fan-out and session/channel controls
remain separate; no production bus source changed during this diagnosis.

Ignored evidence: `.venv/evidence/bus-barrier-probe-20261023/`, including the
produced/consumed trace, command, source hashes, both red JUnit files and
`r22-finding.json`. The observed bus source SHA-256 is
`bd9845553d496759cc662aba0113b1c9a87d41050acaf85faf303b0cb742e499`.

The bounded repair must terminate the merge chain at a delivered lifecycle
boundary while preserving adjacent coalescing, per-subscriber private copies,
session/turn/call/channel correlation, type filters and full-queue preservation
of critical events. Add default regressions and retain the supplemental probe;
do not alter old assertions or treat source-only review as execution.

The fix clears the cached delta for subscribers receiving a non-delta event,
before queue handling. Filtered-out events do not create a delivered boundary.
Eight behavioral cases fail first, then17 new cases plus33 unchanged bus controls
pass per Python version in the isolated candidate. Initial test-construction
errors are retained separately, not counted as behavioral failures.

Parent integration waits until cancelled browser work is confirmed inactive.
The parent matches reviewed snapshot
`cccbd9aa1d1c5458fbcdf1f0328f2e6776f1f7c5688eafff4a36a947becc20ea`,
patch `dfcf706b918e47e71a43549c396274855e946f2d096174fbfa4cc75772b626b3`.
Independent source/integrity review is clear; the reviewer executes no tests.
Parent52-case bus/supplemental and192-case combined bridge/privacy/UI groups pass
on both Python versions. The supplemental module is renamed only to avoid a
pytest basename collision; its assertions remain unchanged.

Both baseline interpreters were missing standard-library encoding files after
interruption. Exact3.11.17/3.12.15 runtimes are recovered in durable ignored
project storage; the listed parent checks are refreshed on those runtimes. No
system Python or package environment is modified globally, and no cause of the
earlier missing files is asserted.

See `.venv/evidence/r22-parent-integration-20261024/acceptance-receipt.json`.
The cancelled browser continuation did not start; its earlier completed stage is
preserved with the old backend identity. Remaining browser stages must bind the
new bus hash. No full-suite/G1, native, live, artifact or soak qualification is
implied by these focused groups.
