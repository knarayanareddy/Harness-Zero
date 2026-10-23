# Opt-in review reproductions (T02)

These are **fail-first desired-behavior assertions**, outside the default `tests/`
suite. At baseline `bfa52425` they are expected to fail; no `xfail`, skipped test,
or weakened assertion makes a finding green. They are diagnostic evidence, not a
replacement for the owning repair's production regression tests (T04–T26).

```sh
python -m pytest review/regressions -vv -rs --junitxml=review-results.xml
```

Use the qualified development environment with Node 22, `npm ci --include=dev`,
and `build` plus `hatchling` available for the wheel probe. Tests use disposable
workspaces/HOME, synthetic credentials/media and local peers. No paid model,
user screenshot, real secret or external IDE is used. R7 is a shipped-module
jsdom probe, R8 a wrapper probe, R18 a data-recipe probe (not a native build), R19
a built-wheel inventory probe (not a clean-install discovery test). See the
ledger for the stronger acceptance proof required by the eventual repairs.

An exception or setup failure other than the finding's documented failure mode
is **inconclusive**, not evidence that the product defect reproduced. Preserve
the JUnit/log and record each probe's observed result separately.
