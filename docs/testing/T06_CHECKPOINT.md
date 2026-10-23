# Historical T06 checkpoint — superseded

Historical status before owner approval/implementation: blocked on the decision in
`docs/adr/0002-owned-process-group-identity.md`. T01 remains non-green. No T07
work had started. Current local evidence is in
[T06_SUPERVISED_CANCELLATION.md](T06_SUPERVISED_CANCELLATION.md); T01/global
qualification remains non-green. The red results below are retained history.

## Implemented and observed

- Turn cancellation relays to the tool's cooperative signal and races active
  handlers and timeout grace. Pending/queued T05 safeguards remain intact.
- Owned cleanup snapshots survive cancellation of their caller and retain late
  process registrations; cleanup failures are explicitly unconfirmed.
- STOP hooks remain cancellable through terminal processing, including a blocked
  prompt path; external STOP Task.cancel is not converted into success.
- Group cleanup includes surviving pipe-holding/redirected children in its
  intended contract; output-reader failure no longer wedges wait state, and
  partial/uncertain effects are not called rollback.
- Conservative guard refuses signals/escalation after known leader exit if
  numeric-group ownership cannot be established. This deliberately leaves the
  two descendant cases unresolved until an identity-preserving design is chosen.

## Evidence under ignored `.venv/evidence/`

Original active-write, signal-ignoring handler, group survivor, STOP, output-
reader, timeout-grace, late-registration and interrupted-snapshot cases were
observed red before their changes. `t06-ownership-red.xml` records both abandoned
snapshot and synthetic group-reuse failures before the conservative checkpoint.

Latest scoped checkpoint: `t06-safe-checkpoint.xml` — **52 passed, 2 failed**
(54 tests), zero setup errors, 13.257 seconds. The failures are:

1. `test_kill_reaps_group_when_leader_exited_but_child_holds_pipes`
2. `test_redirected_child_is_stopped_after_leader_and_readers_exit`

Both explicitly raise unconfirmed-identity RuntimeError instead of signalling
a possibly reused group. They remain required, not skipped/xfail.

An earlier implementation passed 100 real foreground cancellation trials with
zero delayed writes (maximum measured gate return 0.024s), before the conservative
ownership guard. `t06-100-trials.json` is **intermediate evidence**, not final T06
qualification. Similarly `t06-reviewed-py312.xml` (52 passed) predates that guard.
Do not report these older successes as closure of the remaining ownership gap.

The first group-survivor fixture used ExecRequest's default shell mode and a
loose readiness match, so error text gave a false pass. It was corrected to
`shell=False` and an exact stdout barrier, then reproduced the real failure.
No production assertion/golden/timeout was relaxed. A STOP setup barrier was
placed first in its test's hook list to isolate it from baseline checkpoint
latency; default hooks are not disabled.

Independent review's first four findings were reproduced and repaired; its
second pass exposed interrupted snapshot ownership and stale group identity.
The former now passes; the latter is why T06 remains blocked. No full-suite,
Linux/native, installed-artifact or release qualification is claimed here.
