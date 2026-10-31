# T17 — fail-closed MCP exports

Status: locally integrated. Allowed execution/cancellation qualification is T18;
G1/global qualification remains open. Export authorization alone is not execution.

## Configuration contract

The canonical `plugins.bridge-mcp-server.expose` wins over the legacy
`plugins.mcp-server.expose` whenever explicitly present, including `[]`. Only
absent canonical values consult the legacy path. Missing/empty exports mean no
tools. Defensive null handling also denies all; null is not a new valid schema
value. String/dict/mixed-type allowlists raise instead of being read as names.

CLI and plugin setup share one resolver using the public SettingsStore and
PluginSettingsView getters. A sentinel default distinguishes absent values from
schema defaults; no global settings semantics change. Tools/list and tools/call
use the same filtered names. No wildcard/implicit expose-all or new authority.

Example configuration:
`{"plugins":{"bridge-mcp-server":{"expose":["view_file"]}}}`.
The existing schema already specifies canonical/alias/empty semantics and is
unchanged. Permission presets, native sandbox policy and pending ZIDE-04 Git
approval binding are not bypassed by exporting a name.

## Evidence (`.venv/evidence/`, local and ignored)

- `luna-t17/`: worker actual-CLI fail-first cases, then 4 passes on each version;
  module paths verified against its isolated baseline worktree.
- `t17-parent-red.xml`: parent independently reproduces 3 failed/1 passed.
- T17a applies CLI/server repair and explicitly grants the same prior positive
  fixture tools; assertions remain. Seven checks pass in `t17a-parent-green.xml`.
- T17b closes the plugin's independent truthy-alias path and rejects invalid
  list shapes. `t17b-parent-red.xml` records 5 failed/4 passed before that fix.
- Final scope: six actual CLI cases, six real scoped-settings/plugin cases,
  three invalid-list cases, three prior server/stdio positive controls.
  **18 passed on Python 3.12, 26.58s** (`t17b-parent-green.xml`);
  **18 passed on Python 3.11, 52.07s** (`t17-parent-py311.xml`).
- Original R13 passes in `t17-parent-r13.xml`.
- Independent Luna source review verifies candidate
  `e2f299aa1d891eafbf8b1477332d1755d3af827a9fc675ef56c09833fa15e0c5`
  and finds no actionable issue. Parent verifies the scoped patch hash; reviewer
  did not run tests or hash the patch. See `t17-acceptance-receipt.json`.

The canary's absence is not the only oracle: tests assert the distinct
pre-executor denial and exact tool list. Positive toy-handler protocol fixtures
are separate from the actual CLI denial tests and do not establish T18 behavior.
CLI subprocesses run from source, not an isolated installed artifact. Changes
were split into five-file CLI/fixture and four-file resolver increments. No
assertion/golden/deadline weakened. No full-suite/live-host/release claim.
