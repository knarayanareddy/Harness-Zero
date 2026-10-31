# T05: pending interaction cancellation

Status: local repair verified after independent review and follow-up fixes. T01 remains
non-green. At this checkpoint R3 was partial and the active-command delayed-write
probe still failed. Subsequent [T06](T06_SUPERVISED_CANCELLATION.md) locally repairs
active-process cancellation; the historical T05 results below are unchanged.

## Mechanism

- ApprovalBroker forwards the optional turn cancel event. Both interaction kinds
  watch it. Already-set signals prevent request creation, including auto-approval.
- Cancellation beats a same-loop answer. Late sync/recorded approvals and question
  answers are refused. Unknown/settled approval IDs cannot append fake facts.
- Cancelled pending approvals record `approved=false`, `status=cancelled` in
  record metadata and a cancellation reason, preserving existing event fields.
  Questions retain their existing cancellation status; deny/timeout stay distinct.
- Initial request persistence is inside lifecycle cleanup. Direct requester-task
  cancellation cannot leak a registered request with no waiter.
- Claimed resolution persistence is shielded from caller disconnection. A user
  decision claimed before turn cancellation may truthfully persist as approved;
  the cancelled waiter/gate still must not execute. Do not rewrite that earlier
  fact into a fake later denial.
- The gate rechecks cancellation inside the queued handler task, immediately
  before invocation, including the post-auto-approval scheduling window.

Limits: no active-process termination, STOP-hook repair, arbitrary same-user
code isolation, or transaction/power-loss guarantee. Functioning persistence is
required for the durable-fact claim; failed/stalled storage remains a broader
qualification concern. No assertion threshold, recording or golden was weakened.

## Recorded evidence

Paths are under ignored `.venv/evidence/`.

| Check | Observed result | Artifact |
|---|---|---|
| Real Commands/runner/gate pending approval cancellation | Red before fix: no approval resolution within the unchanged one-second interaction bound | `t05-red.xml` |
| Late question answer, direct requester cancellation, cancellation during request persistence | Three red controls, then green | `t05-lifecycle-red.xml`, `t05-lifecycle-green.xml` |
| Cancelled resolution caller during persistence | Red: stranded waiter; green after shielded ownership | `t05-resolution-red.xml`, final races XML |
| Queued-handler race against original gate, process-local injection/no disk reset | Red: cancelled handler wrote the marker | `t05-queued-baseline.{log,xml}` |
| Final approval/cancellation/control tests on Python 3.12 | **17 passed in 4.37s** | `t05-races-final.xml` |
| Earlier scoped tests on Python 3.11 | **14 passed in 3.08s**; later added controls are not silently counted here | `t05-targeted-py311.xml` |
| Final Python 3.11 approval tests plus contract/docs checks | **23 passed in 5.39s**, including all 17 final approval/control cases | `t05-final-py311.xml` |
| Broader gate/runner/durable-event/session-command/ask-user tests | **60 passed in 9.22s** | `t05-broad-py312.xml` |
| Real kernel/web approval/question scenarios | **5 passed in 61.93s**, unchanged goldens/CI DOM bound | `t05-scenarios.xml` |
| Headless question compatibility run | **17 passed, 1 failed**; known unchanged one-second latency assertion measured 2.499s | `t05-targeted-py312.xml` |
| Original active-command R3 probe | Still red: delayed write after Stop, pending T06 | `t05-r03-active-still-red.xml` |
| Final reviewed gate/runner/approval/question subset, Python 3.12 | **65 passed in 10.92s**, including all 19 final approval/control cases | `t05-reviewed-final-py312.xml` |
| Final reviewed approval/control plus contract/docs subset, Python 3.11 | **25 passed in 3.11s**, including all 19 approval/control cases | `t05-reviewed-final-py311.xml` |
| Final reviewed approval/question scenarios | **5 passed in 63.19s**, unchanged goldens/bounds | `t05-reviewed-scenarios.xml` |
| Deterministic repeated requester cancellation stress | **200 cases, zero failures** (100 approvals, 100 questions), 0.139s; direct regression execution, not a full suite | `t05-repeated-cancel-stress.json` |

Cases include approve/deny/timeout, pre-cancelled auto-approval, sync/async late
answers, question cancellation, session isolation, queued handlers and turn
cancellation during an already-claimed approval write. They inspect handler
start counts and resolution facts, not only marker absence. No paid model or
external editor is used by these app tests.

## Independent review and reconciliation

The first adversarial review reproduced two valid P2 defects in this change:
repeated requester cancellation could remove a question before its terminal
cleanup lookup, or cancel an approval future before cleanup called set_result.
Both were independently reproduced red (`t05-review-red.xml`) before repair.

The broker now owns strong references to cleanup tasks and retains a claimed
request until terminal cleanup finishes; it refuses new answers while resolving.
Future settlement checks `future.done()` without suppressing publication of the
recorded outcome. A subsequent broader run caught one remaining ordering race
in the completion callback. Release was moved into the cleanup worker's **own
finally**, before task completion, and both qualified-runtime suites then passed.
The earlier failed run (`t05-review-green-py312.xml`, 64 passed/1 failed) is an
intermediate red result, not hidden by the final green run.

The final bounded source-only re-review found no substantive unresolved issue
in that ownership delta. It did not independently rerun tests or establish disk,
native, active-process or full-release correctness. No external cross-model CLI
was invoked. T06 remains responsible for active processes and STOP hooks.

## Incomplete attempts are not passes

An initial broad command referenced nonexistent `test_interactions.py`, executed
zero tests and was corrected before the completed 60-test result. A broader
non-scenario foreground attempt reached its 180-second process-group bound and
only its owned group was terminated/reaped. No completed JUnit exists; it is
**incomplete**, not passed or a proven new application hang. See
`t05-nonscenario.{json,log}` (exit 124). No whole-suite or Linux/native green
claim is made.

## Re-run

```sh
python -m pytest -q tests/kernel/test_cancel_pending_approval.py tests/kernel/test_approvals.py
SCENARIO_DOM_WAIT_MS=6000 python -m pytest -q tests/scenarios/test_scenarios.py \
  -k 'approval_allow or approval_deny_no_approver or ask_user_answered or ask_user_headless'
python -m ruff check .
```

T06 must propagate cancellation to active owned processes and address terminal
hook cancellation while preserving these pre-execution safeguards and honest
partial/uncertain-effect reporting.
