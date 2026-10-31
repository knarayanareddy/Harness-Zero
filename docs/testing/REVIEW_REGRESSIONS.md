# Review regression ledger — T02

T04–T25 update: R1, R2, R3, R4, R6, R8, R9, R10, R11, R12, R13, R14, R15, R16, R17 and R19 have scoped local repair evidence in the
machine-readable index. R3's original active-command probe now passes after T06;
its earlier T05 failure remains historical. T01/global qualification remains
non-green; neither known latency failures nor incomplete native checks are passes.

R4's allowed-read binding is locally repaired; this is not full T18 acceptance.
Its in-flight protocol cancellation is locally accepted in T18b; audit and
declared broader cases remain open.

Supplemental findings are separate from the original 19. R20's P1 outside-skill
leak is locally repaired and independently source-reviewed; packaging/global
qualification remain blocked. Run
`python -m pytest review/supplemental` explicitly; see
[R20 evidence](R20_SKILL_CONFINEMENT.md). Original local-repair counts do not
include or erase supplemental qualification requirements.

R21's queued cross-session delta mixing is also locally repaired and independently
source-reviewed. Its required default regressions are in
`tests/kernel/test_bus_delta_fanout.py`; see [R21 evidence](R21_EVENT_SESSION_COALESCING.md).
Neither supplemental repair substitutes for the still-open full UI/ACP gates.

Baseline: `bfa52425c1ff37536f31dc74df2ed66dcd953e58`. Upstream HEAD was checked before
work and still matches. No R1–R19 runtime repair is part of T01–T03.

## Reproducible evidence

The machine-readable index is [review_regressions.json](review_regressions.json).
Each row owns a named executable desired-behavior assertion in
[`review/regressions/test_review.py`](../../review/regressions/test_review.py).
Run explicitly, outside default collection:

```sh
python -m pytest review/regressions -vv -rs --junitxml=review-results.xml
```

The first qualified macOS/Python 3.12/Node 22 run produced **19 failures, no setup
errors**. All 19 failure reasons were inspected and match their finding, including
the original crash-recovery/compaction reproductions. This is successful defect
reproduction, **not a green functional suite**. It does not convert the existing
main-suite green results into proof that these boundaries work.

Raw JUnit and log are local/ignored at `.venv/evidence/t02-review.xml` and
`.venv/evidence/t02-review.log`. These paths contain disposable synthetic
workspaces/absolute host paths, not user credentials. The committed JSON index is
the concise evidence inventory; no huge raw log or user transcript is committed.

## Meaning of each result

- `baseline_red`: the assertion failed for the documented defect; keep the finding open.
- `fixed_local_verified_global_qualification_blocked`: targeted/original-probe
  and applicable scoped artifact checks pass after a repair; whole-suite/native
  qualification is non-green or incomplete, not released.
- `inconclusive`: fixture/setup/time budget failed for another reason; investigate the probe first.
- `fixed_verified`: reserved for owning repair work after original reproduction,
  active caller, production regression, and applicable artifact/native/live gates pass.

No `xfail`, skip, lowered threshold or fake-success implementation is used to
hide the 19 failures. The opt-in location keeps intentionally failing review
reproductions distinct from required regression tests. It is **not** permission
to call the product safe while those failures remain. M1–M3 repair tasks move or
adapt the assertions into their actual required suites and retain their original
IDs; the release gate requires all 19 closures.

## Evidence limitations and required upgrade on repair

| Findings | What ran now | What the owning repair must additionally prove |
|---|---|---|
| R1, R3, R15–R17 | Real gate/runner/process/store/log | Race-resistant opens, pending approvals, process trees, crash fault injection, summary consumption after restart |
| R2 | Real kernel continuation and actual OpenAI conversion | Strict HTTP peer; every call ID; errors and session reload |
| R4, R5, R13 | Shipped CLI processes | Locked mutations, direct unlisted invocation, empty list, ACP in-flight cancel plus actual supported editor/client |
| R6 | Actual three payload builders | Valid image bytes, provider-specific URL/base64 rules and outbound wire assertions |
| R7 | Actual shipped modules in jsdom | Real-browser cross-session, reload/reconnect and pending-approval journeys |
| R8 | Actual Seatbelt wrapper with stubbed availability | Real confined nested cwd/write on a qualified macOS host |
| R9, R14 | Actual providers with HTTP mock transport | Local streaming peer, read-time failures and runner/breaker terminal state |
| R10 | Whole-stream control vs split-close parser | All split points, bounded malformed streams, following text |
| R11 | Actual client with late-start stdio fixture | Kernel tool registry updates, HTTP recovery, duplicate/removal controls |
| R12 | Actual MCP handler with standard synthetic image reply | Peer -> gate -> store -> next supported model request |
| R18 | Evaluated PyInstaller data recipe | Actual frozen engine `ui.contributions` and browser resources |
| R19 | Actual built-wheel contents | Isolated non-editable install outside checkout; discover/open all builtin skills |

R15's initial hook-contract failure masks the separately source-confirmed missing
ModelRequest params and discarded-summary bugs. R3's process reproduction does
not itself demonstrate the separate pending-approval variant. Do not close a
multi-layer finding by fixing only its first visible failure.

## Negative controls and ongoing ownership

The R10 control must parse the unsplit stream before testing its split form; R11
must actually connect/discover tools before checking callback count; R2 must make
two real kernel model requests. Unexpected controls failing are probe failures.
R18's mock build machinery intentionally claims only recipe evidence.

`tests/test_baseline_contracts.py` checks ledger completeness, named test ownership,
canonical SDK fields and real Commands names. It does not execute or declare the
19 broken behaviors green. Historical review/DSH plans remain untouched; any new
defect is assigned a new ledger entry and owner rather than silently expanding an
old “fixed” claim.

## T04 update

R1 now passes its corrected original probe, permanent real-gate confinement/race
tests on both local qualified runtimes and a separately installed wheel canary
check. The old query-equals-secret assertion was corrected because no-match text
legitimately echoes a query; the new private-payload/zero-match oracle was also
run against baseline code and still fails there. See
[T04_SEARCH_CONFINEMENT.md](T04_SEARCH_CONFINEMENT.md) for precise scope, limits,
evidence and final full-run status. The original 19-red result above remains
historical baseline evidence. R2–R19 stay open; no global green claim is made.
