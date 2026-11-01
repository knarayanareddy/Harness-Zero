# Qualification checkpoint — 2026-11-01

**Development source checkpoint, not a release or full-platform certification.**

## Reviewed changes

This checkpoint includes the accumulated coding-only work in `677d5a0`, its
verification notes in `a2b0dd8`, and the subsequent RepoMap repair. The repair
applies ignore rules relative to the workspace rather than its absolute
ancestors, preserving source discovery beneath directories named `.venv`,
`build` or `brain`. Its four-case regression failed before the fix and passes
after it. The ten-test focused module passes on both baseline Python versions;
three affected scenarios pass their unchanged goldens with verified imports.

Independent review binds the two changed runtime/test files to snapshot
`2278395db3e2fb00aedfad67690915e082f58aa97976dd7467058be2f0704133`.

The seven untracked `builtin/skills/cinematic-ai-filmmaking/` files remain
excluded under the owner's earlier coding-only decision. Private verification
drivers, raw logs, runtime stores, credentials and ignored evidence are not
part of the published source checkpoint.

## Completed local verification

The qualified 868-file source snapshot is
`2866c9684977a71042e4910e3c04e25dd0c17eb5c593793f4d9825114895e30f`.
Later plan/status/checkpoint documentation does not constitute another suite run.
The runtime and RepoMap regression bytes retain their reviewed identities.

- Python 3.11.17 and 3.12.15: **1,323 passed, 5 skipped each**, one serial default
  invocation per lane, no retries, unchanged 1,500-second diagnostic ceiling.
- Ruff, five import-boundary tests, plugin doctor, plugin tests and strict UI
  audit pass per baseline. CSS freshness is retained from the successful build
  because its complete static inputs are unchanged.
- The repaired isolated environments pass readiness without interpreter
  overrides. The earlier Python 3.11 missing-build-backend failure is retained;
  a real packaging test passes after installing the declared prerequisites in
  a new environment, not by changing the test.
- Q05 process-crash/restart corpus: **100/100 pairs**, 200 crash/restart
  transitions, with all 100 repair traces independently revalidated. All
  **401 worker cleanup receipts** report empty owned groups and no output
  overflow. The one-shot launchd controller exits zero and its exact temporary
  service is removed with absence verified. Source/carrier pins stay unchanged.
- Q03 source cancellation corpus: **100/100 trials**, worst accepted-cancel-to-
  reap **39.291 ms** against the unchanged 2,000 ms bound. All 300 observed
  supervisor/shell/child identities are reaped and all delayed markers are
  absent after the barrier. Canonical results confirm termination; a per-trial
  kernel signal audit or TERM-versus-KILL breakdown is not claimed. The single
  launchd run exits zero and its exact service is removed with absence verified.
- The private recovery observer passes **20/20 controls** after an explicitly
  approved whole-contract rework. Earlier failed controls and candidates remain
  preserved; neither deadlines nor restart-acceptance assertions were weakened.

The source supervisor's separate summary hit a metadata-key error after the
successful suites. Raw JUnit hashes/counts, controller exit, source identity and
job cleanup were reconciled independently; no passing suite was rerun to repair
that summary.

## Open qualification and limits

- No completed uninterrupted >=7,200-second endurance result exists yet. The
  prepared soak preflight passes, but no soak pass is claimed.
- Q05 establishes process restart on the same filesystem, not power-loss
  durability or the outcome of uncertain external effects. Its older 14/100
  boot stall and separate 9/100 second-turn failure remain unexplained.
- The positive control creates the exact marker through the verified builtin
  handler and exits zero, but retains a `/private/var/select/sh` permission
  warning. Native shell support is not certified by that result.
- Five platform-specific skips per source lane remain unverified. Linux native
  confinement, external editor/native WebKit, refreshed final artifacts,
  live-provider coverage, signing/notarization and release decisions remain
  separate gates. Overall release qualification is not green.

Local receipts remain under `.venv/evidence/`, especially
`qa31-repomap-composed-qualification-20261031/terminal-reconciliation-20261101.json`,
`qa31-q05-controlled-full100-20261101/attempt-01/parent-independent-acceptance-20261101.json`
and `qa31-q03-full100-20261101/plans/parent-independent-acceptance.json`, plus
`qa31-observer-b2-controls-20261101/controls-receipt.json`. These ignored
artifacts are local evidence, not files available in a fresh GitHub clone.
