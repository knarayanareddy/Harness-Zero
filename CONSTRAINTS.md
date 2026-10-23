# Harness-Zero quality contract v1 — T03

Effective for subsequent repair tasks. This is a target/acceptance contract, not
a claim that the reviewed runtime already satisfies it. Known open violations:
R1–R19 in `docs/testing/review_regressions.json`. Baseline acceptance work does
not authorize an implementation beyond T01–T03 or any remote publication.

## Authority and scope

- Keep one kernel, canonical SDK, per-session transcript and explicit plugin trust boundary.
- Preserve Python 3.11/3.12 + Node 22 as the qualification baseline. Python 3.13
  has not earned a support claim. Linux/macOS native gates require actual hosts.
- No new provider, bridge, policy, setting or UI feature counts as integrated
  without a named active consumer and a test crossing that seam.
- In-process plugins are trusted code, not isolated by Python capability metadata.
  Workspace content, provider output and retrieved instructions are untrusted.

## Non-negotiable safety and recovery thresholds

| ID | Target gate | Owner / verification |
|---|---|---|
| Q-01 | **0** outside-workspace canary reads/writes in the negative corpus; validate each opened target, not only a supplied root | T04 / BC-01 |
| Q-02 | **0** handler starts from denied, timed-out, cancelled or late-approved interactions | T05 / real gate tests |
| Q-03 | **100/100** local cancellation trials: signal delivered, owned process tree reaped within **2 seconds** of an accepted cancel request, no delayed marker after reported pre-action stop | T06; independent parent wall clock/process checks |
| Q-04 | Pending-interaction cancellation settles within **1 second**; preserve the existing headless question's **<1 second** end-to-end assertion | T05/T01; no removing snapshots or lowering limits without an explicit new decision |
| Q-05 | **100/100** process-kill/restart trials retain all acknowledged records at the documented process-restart boundary; repair is idempotent; no automatic re-execution of uncertain tools | T07/T08 / BC-10 |
| Q-06 | Compaction persists its summary before completion and bounds the next actual request within the selected context budget, or fails explicitly without destroying history | T09/T10 / BC-09 |
| Q-07 | **0** foreign-session/stale-history paints or misdirected approval actions in cross-session/reconnect corpus | T23/T24 / real browser plus jsdom |
| Q-08 | **100 turns and at least two hours** mixed-session qualification with zero unexplained missing records, orphan jobs or dangling interactions; retain RSS/FD/child-count trends | T29; growth is investigated, not hidden by a restarter |
| Q-09 | A supported provider/protocol/artifact completes its actual vertical slice; absent credentials, native backend or signing evidence is **unverified**, not pass | T12–T26 / verification matrix |

The cancellation bounds are tests of supported local cooperative/process work,
not a promise to undo a remote action already committed. Backend-specific limits
must be declared before a capability is supported. Distinguish stopped-before-
action, stopped-with-possible-partial-effects and external-outcome-unknown; these
are behavioral meanings, not newly implemented serialized enum names.

## Durability and privacy

The initial supported durability boundary is **process crash/restart on the same
filesystem**, not sudden power loss. Current `flush()` without `fsync()` is not a
power-loss guarantee. Acknowledgement must mean both store writes succeeded at
the documented boundary; partial tails cannot swallow the next acknowledged
record. Preserve/quarantine damaged bytes; never delete a conversation to make
recovery pass. Synthetic recovery facts never assert an uncertain action ran or
did not run. Format/migration changes need explicit restore/downgrade evidence.

No credential/user transcript enters test fixtures, recordings, artifacts or
committed logs. A local analysis tool's output can still reach a remote model;
do not imply local execution alone prevents disclosure.

## Change acceptance and reporting

1. Fail-first regression for the actual boundary; at least one positive/control
   and one negative/interruption case. Do not mirror implementation bugs in a fake.
2. No assertion deletion, `xfail`, new broad suppressions, lowered thresholds or
   silently skipped required lanes to produce green. A time limit failure needs
   measured diagnosis. Known intentionally red review probes stay visibly open.
3. Separate local subset, full suite, historical remote, live vendor, native and
   artifact evidence. An incomplete run or killed process is never a full pass.
4. Keep default source suite green while repair probes remain explicitly red;
   release qualification still blocks on all applicable open findings.
5. Record SHA, dependencies, command, elapsed time, JUnit/log, required skips and
   environment limitations. A baseline runner's hard timeout is **1,500 seconds**
   per current full-suite lane, a diagnostic ceiling, not a weakened operation SLA.
6. New/changed diagnostics must pass Ruff and their own negative tests. Tests
   cannot pass merely because setup failed. Generated catalogues stay generated.
7. Test the exact installed/frozen artifact outside its source checkout. Promotion
   of a rebuilt artifact needs a new qualification result.
8. Owner review is required before changing any of these thresholds, widening
   authority, adding required live spend or publishing a support claim. User
   authorization for T01–T03 is not a license choice or publish authorization.

Detailed boundary IDs: `docs/architecture/CONTRACTS.md#5-baseline-boundary-contract-v1`.
Required evidence lanes: `docs/testing/VERIFICATION_MATRIX.md`.
