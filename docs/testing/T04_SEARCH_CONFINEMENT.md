# T04 / R1: descriptor-confined code search

Status: local repair verified; targeted/artifact checks pass and independent
static review returned no substantive issue. Global regression qualification
remains blocked, not pending a live process. T01 stays non-green. No other
review finding is repaired here.

## Mechanism and limits

The real locked-mode gate calls one Python reader. Workspace/subdirectory and
child opens are directory-relative, no-follow operations; reads use an acquired
file descriptor, not a pathname reopened after validation. Child opens are
non-blocking, and only `fstat`-confirmed regular files are read. Acquired directory
objects stay pinned if their names are replaced. Descriptors close on limits,
errors and normal exit.

Discovered symlinks are skipped, including inside targets. Explicit inside
directory aliases may be canonicalized by the gate before the handler runs;
that existing authorization is retained. Outside roots are denied. Ignored
trees stay ignored even if explicitly selected. Inaccessible entries are
skipped; readable siblings remain searchable. Unsupported descriptor operations
fail closed. The unsafe/unreachable ripgrep alternative is removed, and the
plugin no longer declares unused `exec` authority.

This authorizes acquired workspace objects, not arbitrary same-OS-user code
isolation, a hard-link prohibition, a regex CPU budget, new ordering guarantees,
native process confinement or repairs of unrelated filesystem tools.

## Retained evidence

Paths below are under ignored `.venv/evidence/`.

| Check | Observed result | Evidence |
|---|---|---|
| Permanent gate regression before implementation | Outside canary leaked, inside control read; red | `t04-red.xml` |
| Leaf, parent and root swaps against original HEAD search code, process-local injection with no disk reset | All 3 read outside canaries and failed | `t04-race-baseline.{log,xml}` |
| Same deterministic swaps with repaired reader | Pass; every test asserts its injection fired | `t04-targeted-py312.xml` |
| Concurrent real-OS symlink mutation during 100 helper searches | 100 inside controls present, zero private-canary disclosures, 86 actual link swaps; supplementary stress evidence, not a separate gate/full-suite pass | `t04-live-race.json` |
| Explicit ignored-root regression | Both cases red before guard, then green | `t04-ignore-red.xml`, targeted XML |
| Search/policy/fs/gate/import/catalogue/ledger subset on 3.12 | **109 passed, 1 skipped**, 8.77s; documented macOS catalogue skip | `t04-targeted-py312.xml` |
| Search/policy/fs/gate/import subset on 3.11 | **96 passed**, 6.33s | `t04-py311.xml` |
| Corrected review R1 probe | Baseline red on private payload; repaired reader green | `t04-r01-baseline.xml`, `t04-r01.xml` |
| Separate non-editable wheel install, cwd outside checkout | Actual locked gate reads inside control and excludes outside symlink canary; import from installed site-packages | `t04-artifact.json` |
| Fresh-context adversarial reviewer | No substantive issue; static only, no independent native execution | Review recorded separately from runtime proofs |

Wheel SHA256:
`a42901ba7b5b9965b42c7b024d6e43a5d6cf4eb8299e364115389172f1eeb588`.
The wheel smoke does **not** close missing skills R19 or frozen resources R18.
Linux native confinement and a live vendor are not claimed by any of these tests.

### Corrected probe oracle

The old R1 probe rejected the query string itself in output. A legitimate
no-match message echoes it. The corrected probe uses a distinct private payload,
requires successful read-only execution and **zero matches**, and rejects the
entire private line. Original baseline code still fails this stronger precise
check; the repaired implementation passes. No golden, timeout or unrelated
assertion was changed. This is a probe correction, not permission to hide leaks.

## Full regression provenance

An initial full run was stopped after another ignored-root edge case was found
and repaired; `t04-full/run.json` records exit -15. It is incomplete, not passed.
A new single Python 3.12 full lane started after the final runtime/manifest
changes, using disposable HOME/config, Node 22, unchanged assertion thresholds
and the 1,500-second diagnostic ceiling. Its runner subsequently disappeared
without final JUnit, summary or `run.json`; the termination cause is unverified.
The earlier chat status saying it was still running was stale and is withdrawn.
Retained `t04-final-full/pytest.log` contains **706 passed, 1 failed, 3 skipped**;
this is an interrupted run, not a completed full-suite result.

The remaining **34 collected nodes** were then executed in seven bounded,
sequential foreground batches: **31 passed, 1 failed, 2 skipped**, zero setup
errors. Retained evidence is `t04-resumed/batch-*.{xml,log}` and `summary.json`.
Node-ID parsing preserves spaces inside parametrized tests; a preliminary parser
that counted only no-space names was corrected before calculating coverage.
All **744** collected nodes now have observed outcomes across the interrupted
and resumed executions: **737 passed, 2 failed, 5 skipped**. These counts are
**partitioned coverage, not an uninterrupted full-suite pass**.

The original portion failed `huge_tool_output_pruned_and_spilled` (also seen in
T01/B04). Resumed batch 4 failed `settings_tab_from_plugin_schema`: expected
`Probe Vault` group tabs but saw none. That new observed node is assigned to
T29 scenario-readiness diagnosis/T24 frontend reconciliation; its root cause is
not established and is not silently labelled an unrelated pre-existing defect.
Both failing scenarios pass together in isolation (**2 passed, 25.115s**;
`t04-failure-isolation.{xml,log}`), demonstrating sensitivity, not a repair.
No recording/golden/timeout was changed to obtain those isolated passes.

Global qualification remains blocked by these results and the documented T01
native/runtime gaps. Local T04 closure is the scoped gate/race/artifact proof,
not a claim that the entire application or release is qualified. No verification
process remains running and no T05 work started.

## Catalogue and rerun

The owning `build_catalog`/`write_outputs` generators refreshed only the search
capability/summary/description rows. The documented macOS-only screenshot tool
was omitted from the catalogue inventory to match its canonical Linux projection;
this is catalogue normalization, **not execution on Linux**. No golden changed.

```sh
python -m pytest -q tests/kernel/test_search_confinement.py tests/plugins/test_search_tools.py
python -m pytest -q review/regressions/test_review.py -k R01
python -m ruff check .
```
