# T01 baseline evidence

Baseline SHA: `bfa52425c1ff37536f31dc74df2ed66dcd953e58` (upstream HEAD checked before
work; unchanged from the review). Work checkout: `/Users/brightech/work/Harness-Zero`.
This document distinguishes historical CI, new local execution, diagnostic
reproductions and unqualified native/live evidence. Runtime repairs are out of
scope for T01–T03.

## Clean toolchains

The review used a broken Homebrew Python 3.13 installation and temporary
`PYTHONPATH`/`DYLD_LIBRARY_PATH` workarounds. They are **not** needed in the new
environments and were not installed in production code.

| Component | New local version / provenance |
|---|---|
| macOS / CPU | 26.2 / ARM64 |
| Python 3.11 | Managed standalone CPython 3.11.17; working stdlib SSL/XML/Expat |
| Python 3.12 | Managed standalone CPython 3.12.15; working stdlib SSL/XML/Expat |
| Node | Official Node 22.23.3 darwin-arm64 archive, SHA256 verified |
| Node packages | Locked `jsdom@24.1.3`, `tailwindcss@3.4.17`, installed with dev dependencies |
| Pytest / asyncio / Ruff | 9.1.1 / 1.4.0 / 0.17.1; exact per-environment snapshots retained |

Node archive SHA256:
`23b25245dcfb9af7262f8ff142e9e2e0af025368117329e7a7458a51e5922f53`.
The bootstrap uv 0.13.4 macOS ARM64 wheel was verified against its published
SHA256 `95f4eb2b991b53b50ec2b18ab612fca8ba1e7a75a47af0331fb8bcd21da3eb03`.
These tools are local qualification resources, not new application dependencies.

Each venv's bin directory is explicitly first on PATH so subprocess jobs calling
`python` use the intended environment. Node 22 precedes the host Node 26. Test
processes receive disposable HOME/HARNESS_CONFIG_DIR; no real vendor keys or
user configuration is needed. The opt-in fixture additionally removes secret-
shaped environment variables. No native provider/IDE/cloud call was made.

## Reproduce prerequisites

With an approved uv installation and Node 22 already on PATH:

```sh
uv python install 3.11 3.12
uv venv .venv/py311 --python 3.11 --seed
uv pip install --python .venv/py311/bin/python -c docs/testing/baseline-requirements.txt -e '.[dev]' build
uv venv .venv/py312 --python 3.12 --seed
uv pip install --python .venv/py312/bin/python -c docs/testing/baseline-requirements.txt -e '.[dev]' build hatchling==1.32.4
npm ci --include=dev
```

For each interpreter, prepend its bin directory and run
`python scripts/verify_environment.py`. Readiness reports missing modules, wrong
child Python prefix, missing locked Node dependencies, an unsupported baseline
runtime or review-only environment contamination. It never installs or repairs
anything and never prints credential values. Readiness is not a test pass.

Use the workflow's actual selection/environment; verbose output is diagnostic:

```sh
export PATH="$PWD/.venv/py312/bin:$PATH"   # Node 22 must also precede host Node
export HOME=$(mktemp -d)
export HARNESS_CONFIG_DIR=$HOME/.harness
export SCENARIO_DOM_WAIT_MS=6000
python -m pytest -vv -rs --durations=20 --junitxml=pytest-results.xml
```

The two new local full-suite processes have a 1,500-second diagnostic ceiling,
capture detailed node IDs and retain logs/JUnit rather than silently hanging.
`-vv` and `--durations` change diagnostics, not selection or assertion thresholds.
No runtime code was edited during those runs; new baseline scaffolding tests
are verified separately. Both full runs completed normally with failing exit
status, not the diagnostic timeout. The two lanes ran concurrently on this Mac;
that is a shared-resource stress condition, not equivalent to separate CI hosts.

### Completed local full-suite results

| Lane | Passed | Failed | Skipped | JUnit elapsed | Setup errors |
|---|---:|---:|---:|---:|---:|
| macOS / Python 3.11.17 | 712 | 7 | 5 | 996.890 seconds | 0 |
| macOS / Python 3.12.15 | 715 | 4 | 5 | 1,117.135 seconds | 0 |

Each collected 724 existing tests. Wrapper elapsed times were 1,002.517 and
1,124.353 seconds respectively; both returned exit code 1. No full run is green.
The five local skips match the historical macOS skip classification below.

### Failure inventory and ownership

Every full-run failure is classified here. Buckets name observed symptoms and
owners; they do not pretend an unproven root cause is established.

| Bucket / owner | Failed node(s) | Full run(s) | Classification / follow-up |
|---|---|---|---|
| B01 — T01 diagnosis, T06 cancellation, T29 qualification | `tests/kernel/test_ask_user_through_kernel.py::test_headless_returns_error_within_1s` | Both | Stable strict-latency failure, also reproduced alone; checkpoint critical-path cost and swallowed STOP cancellation identified below |
| B02 — T29 checkpoint/recovery qualification | `test_scenario[plain_answer_kernel]`, `test_scenario[tirith_blocks_ssrf_kernel]` | 3.11 | Golden differences lose/reorder checkpoint/workspace-changes facts; snapshot-skipped state appears in SSRF case. Do not regenerate goldens; investigate checkpoint deadlines/cancellation and order dependence |
| B03 — T29 scenario reliability; T23/T24 frontend ownership where reproduced | `test_scenario[plugin_panel_appears_when_enabled]`, `[present_workspace_file]`, `[read_then_edit_web_dom]` | 3.11 | Missing timeline content or terminal state within unchanged DOM observation bound; a wire turn.completed does not prove the client handled it within the bound |
| B03 — same owners | `test_scenario[plain_answer_web]`, `[security_preset_badge_web_dom]` | 3.12 | Missing observed terminal event, or client still onboarding/no submit at its deadline; investigate before attributing to client vs fixture/load |
| B04 — T29 replay/job/output qualification | `test_scenario[huge_tool_output_pruned_and_spilled]` | Both | 3.11 requests an unrecorded third response; 3.12 sees a cancelled/background-job path without expected tool.result.pruned. Investigate terminal timing/job wake-up vs fixture assumptions; do not pad recordings blindly |

The six failing 3.11 scenarios were rerun together: **6 passed in 98.414s**.
The three failing 3.12 scenarios were rerun together with the CI DOM bound:
**3 passed in 46.111s**. Their JUnit/logs are retained as
`py311-failure-isolation.*` and `py312-failure-isolation.*`. These establish
suite/load/order sensitivity, **not fixes and not a green full baseline**.
The isolated headless test remains red on both runtimes. No timeout was raised,
assertion removed, recording padded or golden updated.

T01's execution/failure-inventory work has finished. Qualification remains
blocked by B01–B04 and missing required native/new-Linux evidence. The next
repair task may start with these explicit owners and regression gates; it must
not claim T01 passed or turn the isolated successes into release qualification.

## Historical remote evidence — not a new changed-tree run

Run [39082591345](https://github.com/knarayanareddy/Harness-Zero/actions/runs/39082591345)
on the exact reviewed SHA:

| Lane | Historical full-suite result |
|---|---|
| Linux / Python 3.11 | 720 passed, 4 skipped; 555.13 seconds |
| Linux / Python 3.12 | 720 passed, 4 skipped; 555.63 seconds |
| macOS / Python 3.11 | 719 passed, 5 skipped; 554.44 seconds |
| macOS / Python 3.12 | 719 passed, 5 skipped; 562.69 seconds |

The full remote log was retrieved and retained at
`.venv/evidence/t01/remote-baseline.log`; its provenance summary is
`.venv/evidence/t01/remote-summary.json`. Linux cannot be executed locally on
this Mac without a provisioned VM/container/runner; none is available. No push,
new GitHub Actions run or public result was created by this task.

### Historical skip classification

- Linux, both versions: **all four native bubblewrap cases skipped** (three
  integration tests at `tests/kernel/test_sandbox.py:409/436/467`, plus the
  full-enforcement scenario). Bubblewrap was installed but user/PID/network
  namespaces failed with `RTM_NEWADDR: Operation not permitted`. Green CI is
  **not native-confinement proof**; T29 needs an appropriately provisioned runner.
- macOS, both versions: three Linux-only bubblewrap integration tests and its
  full-enforcement scenario skipped, plus the documented Linux-catalogue
  freshness test (`tests/kernel/test_catalog.py:155`). The catalogue skip is
  intentional platform variance; native sandbox qualification is a separate
  still-unmet release gate.
- Historical pytest/pytest-asyncio/Ruff/Pydantic/httpx/aiohttp versions match the
  new local resolved versions. No observed difference in those dependencies
  explains away the local failures. The committed baseline constraints retain
  the resolved versions without changing application dependency requirements.

## New local observations

- Both read-only readiness reports pass with clean 3.11/3.12 and Node 22.
- Final baseline scaffold + docs/version/import-boundary subset on Python 3.12:
  **14 passed in 3.83s**; diagnostic/contract scaffolding alone on Python 3.11:
  **5 passed in 0.45s**. These are completed subsets, not full-suite results.
- Opt-in defect suite: **19 failures, zero setup errors**, each matching its
  finding. R13's CLI configuration path was explicitly aligned with the parent
  kernel before the recorded final run. These are open defects, not passes.
- Ruff on the whole checkout passes after the new files are included.
- Seatbelt's actual availability probe returns **available=false**, enforcement
  **none**, reason `sandbox-exec could not apply the probe profile: no diagnostic`.
  The wrapper-only R8 probe does not establish native confinement.
- npm reports the previously reviewed **7 development dependency nodes** (5 high,
  2 moderate); remediation remains T27. No lockfile/major-version change is made.

## B01 — strict headless question latency remains an open baseline blocker

The same existing assertion fails under clean Python 3.12: a measured run took
**1.496 seconds** against the unchanged `<1.0` limit. Therefore the earlier
failure cannot be dismissed as only a Python 3.13 problem. A profiled isolated
run took 1.012 seconds and logged cancellation of the terminal checkpoints hook
at the outer one-second timeout; the runner can return after that deadline.

The unchanged same test also failed on clean Python 3.11 at **2.370 seconds**.
Source inspection at `harness/kernel/runner.py:1004–1020` confirms STOP hook
dispatch catches and suppresses `CancelledError`, matching the isolated timeout
warning. This is a concrete cancellation-propagation seam to cover in T06; it
does not establish why every slow run crosses the budget. Both performance and
terminal cancellation semantics require repair before the gate can close.

A separate disposable real-kernel span experiment completed in **0.863 seconds**:
checkpoint USER_PROMPT_SUBMIT consumed 0.391 seconds and STOP consumed 0.376
seconds, while the largest context-source span was under 0.009 seconds. These
measurements identify synchronous turn-critical checkpoint work as the main
observed budget consumer; they are **not proof of a unique root cause** for every
timing variation. Concurrent full-suite load and instrumentation affect timings.

Preserved evidence: `.venv/evidence/t01/question-profile.{log,pstats}` and
`question-spans.json`. No production hook was disabled, no test threshold was
relaxed, and no timeout was turned into success. Follow-up owner: T01 baseline
diagnosis plus T29 qualification; any checkpoint scheduling/performance repair
needs its own narrow task before claiming the one-second gate is resolved.

## Evidence retention and completion status

Local artifacts live under ignored `.venv/evidence/t01/`:
per-lane `environment.json`, `dependencies.txt`, `pytest.log`, `pytest.xml` and
`run.json`. Raw absolute paths/synthetic traces stay local; concise measured
results and required skip reasons belong in this document. Never commit the
virtualenv, actual user sessions or a raw environment dump.

**T01 is not complete while the strict latency gate or required lane evidence
remains unresolved.** T02/T03 preserve the known failures and acceptance bar so
later repairs do not erase this status.
