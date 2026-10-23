# G1 — safety/recovery checkpoint, 2026-10-22

Status: **not accepted**. Local repair slices are not full qualification.

## Fresh uninterrupted full suite

The default CI selection ran on clean Python 3.12/Node 22 with disposable HOME,
no vendor credentials, unchanged SCENARIO_DOM_WAIT_MS=6000 and the existing
1,500-second diagnostic ceiling. It completed: **831 passed, 2 failed, 5 skipped**,
zero setup errors, console 790.49s. The before/after source-tree fingerprints
match (`f8731035dcbb54c3fcdfe8759bb76d9afd9db832767d4c4d8176cf7b3fc8b64c`).
Artifacts: `.venv/evidence/g1-full-py312/{started.json,run.json,pytest.xml,pytest.log}`.

Failures:
- `ask_user_headless`: STOP checkpoints exceeded their existing five-second
  deadline, so the golden's workspace.changes record was absent.
- `huge_tool_output_pruned_and_spilled`: supervisor cleanup reported TimeoutError;
  a subsequent narrow run instead moved slow output handling to background. The
  required pruning event was absent. No golden/recording was changed.

Skips: three Linux-only native bubblewrap tests, its Linux-only full-enforcement
scenario, and Linux-generated catalogue freshness on macOS. Seatbelt's T11
native cases passed; this does not qualify Linux or every native workflow.

## Bounded follow-up slices

1. Identical validated commit endpoints now return the exact empty diff without
   a second commit validation or two redundant Git diff subprocesses. Both
   endpoint validation and all actual snapshots remain. The positive control
   failed first; nonexistent identical endpoints still fail closed.
2. A supervisor's actual reaped exit status is distinguished from delayed output
   completion. The existing one-second termination deadline still errors if the
   anchor is unreaped. A held reader-completion barrier reproduced the false
   unconfirmed status; output is still awaited and retained. The test's initial
   argv fixture was corrected to shell=False and the fail-first probe rerun.
3. Profiling the noisy scenario recorded 20,118 get_type_hints calls; declaration
   resolution consumed 33.24s cumulative in that instrumented run. A bounded
   256-entry cache now holds only static class declarations. Every payload still
   checks required/extra fields and leaf types; no payload or verdict is cached.
   A fail-first control proves 202 checks resolve declarations once, while later
   malformed payloads remain rejected. Profiling overhead is not a normal SLA.

Evidence:
- `g1-same-checkpoint-red.xml`, `g1-drain-corrected-red.xml`,
  `g1-validation-cache-red.xml`: observed fail-first controls.
- `g1-drain-corrected-green.xml`: 36 earlier lifecycle/executor controls pass.
- `g1-noisy-profile.{log,pstats}`: pre-cache instrumented diagnostics.
- `g1-repairs-py312.xml`: **51 passed in 67.43s**.
- `g1-repairs-py311.xml`: **51 passed in 55.23s**.
  These include both previously failing scenarios, real lifecycle/ownership
  controls, an unreaped-anchor negative case and unchanged checkpoint suites.

These targeted passes do not replace a new full-suite pass. G1 remains open.
The earlier comparison logs named green may contain failures; the final 51-test
artifacts above are the completed scope, not an invented union/full pass.

## Strict latency still open

The unchanged `<1 second` headless test passed alone in
`g1-headless-initial.xml`, but failed in later subsets and in all three instrumented
span trials. `g1-headless-spans.json` records checkpoint Git subprocesses taking
roughly 0.1–1.9 seconds each. Some turns hit their external one-second deadline
before the initial snapshot finished, then ran terminal cleanup. Reducing empty
diff work is not a proven solution to this whole critical-path limit.

No snapshot was removed, no deadline relaxed, and no timeout converted into a
successful turn to hide the issue. Larger checkpoint scheduling/storage changes
need their own design and fail-first slice before claiming this gate resolved.
Existing owners B01–B04/T29 remain. Continue original integration-defect repairs
with these explicit limits; do not promote a qualified build or new capability
on the strength of local subsets.
