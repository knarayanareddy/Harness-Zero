# T11 — preserve validated confined working directories

Status: wrapper and required native macOS acceptance locally verified on
2026-10-22. Global/Linux qualification remains incomplete.

Seatbelt already resolves and validates the requested cwd against the workspace,
but returned an ExecRequest whose cwd was reset to the workspace root. It now
returns that validated cwd. No profile, environment, argv, approval or native
availability decision is weakened.

## Evidence (ignored `.venv/evidence/`)

### Final native verification — 2026-10-22

- `t11-native-final-py312.xml`: **27 passed, 3 Linux-only skips**, 1.36s.
- `t11-native-final-py311.xml`: **27 passed, 3 Linux-only skips**, 0.75s.
- `t11-native-r08.xml`: original R08 passes, 18 others deselected.
- Actual SandboxService -> supervised LocalExecutor -> Seatbelt -> shell verifies
  nested `pwd`, relative write location and reported partial enforcement. Another
  actual confined shell cannot read a synthetic outside file or write an outside
  marker. No native Seatbelt case skips in these final runs.
- Both the availability and runtime profiles grant only
  `(allow file-read-data (literal "/"))` for loader bootstrap. This allows the
  exact root directory, **not** its descendants. No global file-read-data or
  `(subpath "/")` grant is added; workspace '/' remains forbidden.
- `t11-loader-root-red.xml`: probe/runtime profile assertions fail before the
  root-directory rule. They pass with the native checks after it. Ruff and
  `git diff --check` pass.

### Corrected diagnosis of the earlier blocker

The prior conclusion that this Mac could not apply Seatbelt profiles was too
broad. Terminal automation succeeded, but the unchanged native test still skipped
(`t11-terminal-4f5e03ca/pytest.xml`); its exit zero was **not** a native pass.
Direct probes then established that allow-default and read-all probes work while
the repository's path-filtered probe aborts with SIGABRT. Existing crash evidence
places the abort in dyld/ignition startup, not a permissions approval denial.
The exact root-directory read rule makes the otherwise unchanged probe succeed;
metadata-only root access does not. Intermediate exploratory read grants were
used only for `/usr/bin/true` diagnostics and were not added to product policy.
Diagnostics are retained under `t11-terminal-4f5e03ca/`.

No administrator permission, Full Disk Access, disabled SIP, new machine or paid
CI lane was needed. The earlier skipped native evidence below stays historical;
it must not be confused with the new actual native passes. This qualifies the
specific cwd/read/write cases, not every Seatbelt operation or all T06 scenarios.

### Earlier wrapper-only checkpoint

- `t11-red.xml`: argv and shell nested-path controls both fail against the old
  wrapper; native test skips because the actual profile probe is unavailable.
- `t11-py312.xml`: **24 passed, 4 skipped**, 0.61s.
- `t11-py311.xml`: **24 passed, 4 skipped**, 0.49s.
- `t11-r08.xml`: original R08 wrapper probe passes, 18 others deselected.
- Tests retain timeout/interactive fields, use a directory containing a space,
  reject direct outside cwd and an outward symlink, and leave Linux controls intact.
- Ruff and `git diff --check` pass.

One skip is the newly required real macOS pipeline: SandboxService -> LocalExecutor
-> Seatbelt -> nested `pwd` and relative write. This host reports:
`sandbox-exec could not apply the probe profile: no diagnostic`. Three other
skips are unchanged Linux-only bubblewrap integrations. A stubbed availability
fixture proves only wrapper construction, never native confinement.

To reproduce native acceptance, run
`python -m pytest -q tests/kernel/test_sandbox.py -k seatbelt_native_nested -rs`
on a macOS lane that permits sandbox-exec profiles. It must pass, not skip. This
now passes on the current host. Linux native qualification remains outstanding;
no CI spending, remote deployment or OS security-setting change is authorized.
