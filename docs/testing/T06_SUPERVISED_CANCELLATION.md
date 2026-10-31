# T06a/T06b — supervised active-process cancellation

Status: locally verified after owner-approved supervisor design and independent
review. T01/global/native qualification remains non-green. This supersedes the
historical conservative checkpoint; no commit, push or release is implied.

## Mechanism

T06a relays turn cancellation into the per-tool cooperative signal, races active
handlers and timeout grace, owns whole cleanup snapshots across caller
cancellation, retains processes registered during cleanup, and interrupts STOP
hooks without reporting success. Termination failures are unconfirmed; partial
effects are never called rollback. Tool timeout remains a separate fact.

T06b supplies a private stdlib supervisor in `harness/sdk/process_supervisor.py`.
The current interpreter launches its **absolute file with -I**; the frozen engine
uses an early private self-entrypoint, before CLI bootstrap. No ambient Python
lookup or shell FD-redirection dialect is required. The helper stays the live
session/group leader, catches TERM, and **self-signals its own group** on private
TERM/KILL control messages. Engine code does not signal a recovered numeric PGID.
Owner-pipe EOF closes the group. Payloads inherit standard IO but not status,
control or wakeup descriptors. SIGCHLD uses a wakeup pipe/selector, not a sleep
poller; signed signal exits remain distinct from explicit exit(128+signal).

Normal payload completion closes inherited foreground descendants. Independent
long-running work must use the existing Jobs/background handoff, not an orphaned
shell `&`. PID now identifies the live supervisor. A supervisor killed by some
outside actor is an unconfirmed outcome, never authority for numeric fallback.
Arbitrary same-user code and intentional group/session escape are not isolated.

Frozen one-file workers inherit only the actual engine's allowlisted private
PyInstaller state, never tool overrides. These controls are reserved, stripped
from payloads, and frozen library paths are restored for ordinary payloads.
The group/session-leader check is **not weakened**. Reference inspected:
https://pyinstaller.org/en/stable/advanced-topics.html#environment-variables-used-by-frozen-applications
(PyInstaller 6.22.3 used for the artifact).

## Final scoped evidence

All retained paths below are under ignored `.venv/evidence/`.

| Tier | Observed result | Artifact |
|---|---|---|
| Supervisor/gate/runner/jobs/sandbox compatibility on Python 3.12 | **88 passed, 3 skipped, 19.81s**; native Linux-only cases are unavailable here, not passed | `t06-final-compatibility.xml` |
| Direct/group/approval/cooperative/executor/import subset on Python 3.11 | **64 passed, 21.69s**, before the final frozen-worker environment delta; that delta separately verified below | `t06-final-py311.xml` |
| Original R3 active-command reproduction | **1 passed**, 18 unrelated review probes deselected | `t06-final-r03.xml` |
| Descriptor/argv/stdin/status/startup/owner-EOF and dash-backed launch checks | Included in final compatibility subset; actual local dash binary used, not a Linux OS qualification | `tests/plugins/test_process_supervisor.py` |
| Focused real-gate lifecycle stress | **100 confirmed stops**, zero delayed writes, max 0.039s, descriptor samples stayed at 10 | `t06-focused-100.json` |
| Installed non-editable wheel, cwd outside checkout with hostile shadow package | Exit 7 and exact stdout preserved; trusted installed package imported; helper ignores cwd shadowing | `t06-wheel-smoke.json` |
| Uncorrected actual one-file artifact | **Red:** exit125, no READY; confirmed the worker/leader finding | `t06-frozen-red.json` |
| Corrected actual one-file engine, scripted mock-provider real run_command | Exit0, payload marker present, no private _PYI_ payload environment; open fixture preset, no live vendor | `t06-frozen-smoke.json` |
| Independent review | Prior blockers reproduced/fixed; final frozen-environment delta has no source-established unresolved issue | Source review, not independent execution |

Wheel SHA256: `6dcdc47b788ac12f62e0ae292e5a6dcd46984aea228ec3a054ea7e95e68be657`.
Frozen engine SHA256: `d2ddc295aaee46aebe31194cf1dd62110dba51b07189adfd228ad960c301877f`.
The artifact smokes do not close R18's missing core UI manifest or R19's builtin
skills; those are independent resource defects.

## Evidence limits and corrected oracles

The 100 focused trials use a test-only tool alias with the real gate, actual
shell handler, executor and supervisor, isolating process lifetime from
name-triggered checkpoint setup. **They are not 100 full production turns or a
policy-coverage claim.** The attempted final 100-trial builtin route timed out
before readiness after 16 successes, with only tool.call observed on the next
trial. Its cause is not established; retain `t06-final-100-diagnostic.json` as
**inconclusive**, not a pass or proof it is solely the old checkpoint issue.
Earlier successful trials from intermediate backends are not final evidence.

The two-second Q-03 bound applies to confirmed process cleanup at tool.result,
not all subsequent STOP processing. The permanent test was corrected to measure
that event and require termination_confirmed=true, retaining the independent
turn-completion bound. T01's existing strict headless-latency assertion was not
modified. No recording/golden or production timing bound was relaxed.

The original group fixture's loose readiness/shell-mode false pass, a STOP
setup-barrier miss, the fail-closed intermediate state and all review red cases
remain documented in the historical checkpoint and retained XML. Review found
and drove fixes for redirected children, late/interrupted cleanup snapshots,
timeout-grace cancellation, hook-blocked STOP, stale IDs, dash syntax, startup
handoff, partial FD allocation, signal status, and actual one-file worker mode.

Required native bwrap/Seatbelt tests, Linux execution, full default-suite/soak,
signing/notarization and live vendor tiers remain separate unverified gates.
Native payload wrappers are unchanged; bwrap parent-death/session behavior
requires a provisioned Linux runner. This is not a claim of a qualified release.
