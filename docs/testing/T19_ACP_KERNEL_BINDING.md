# T19 — ACP binding to canonical kernel commands

Status: integrated in the parent, locally tested and independently reviewed;
T19's internal binding is accepted locally. This is legacy0.1, not official ACPv1
or a supported editor-interoperability claim. T20 owns that separate qualification.

## Scoped behavior

The public stdio process bootstraps without implicitly resuming a workspace
session. Each connection can list, prompt, cancel and resolve interactions only
for sessions it explicitly creates. It calls real Commands APIs; runtime registry
cleanup occurs in finally. Tests exercise actual gate approvals, allowed marker
effects, pre-action cancellation and foreign-session/permission denial, including
two real subprocess peers. Invalid request shapes produce bounded protocol errors.

Notifications use typed allowlisted events, owned session filtering, a16KiB
encoded retained-payload budget and256-node traversal budget. Oversized source
strings/keys are discarded before scanning; oversized final output inspects at
most8192 source characters and emits no source prefix, preventing a truncated
credential from being copied into the reply. Detected-redaction/truncation facts
remain explicit; ordinary bounded output uses the canonical sanitizer.

Interaction IDs must remain safely representable. Malformed or partially delivered
questions fail closed. Notification/write failure stops admission, aborts output
and cleans up connection-owned work. Write failures abort while holding the output
lock, so queued writers cannot each wait another full drain timeout. Strict shared
Secrets discovery propagates file failures and protects supported environment and
resolved credential sources; the six-character exact-match minimum remains.

## Review, composition and tests

The isolated source is independently reviewed at snapshot
`cb13d49327a788c265cbad606ae0ce4fce0b0c5a529551d418efeae3e6d16f06`,
patch `c34b2ffa1a6fc84fd54ed8713be8351c499e1d2cb96c1f53799e59910c6a8dfd`.
Its unchanged32-case matrix passes on each Python version, including the original
R05 and actual stdio peer. Earlier timeout/host/import diagnostics are retained.
No reply, cancellation or close deadline is weakened.

The test peer drains stderr immediately and remaining stdout during its original
two-second close bound, requiring zero exit. A512KiB stderr-capacity regression
failed first; an unread pipe is not misreported as a product deadlock. A foreground
approved-command fixture avoids optional background-followup responses while
preserving all effect, denial and cancellation assertions.

Parent integration checks the actual destination against archived before-content.
Standalone ACP source/tests are exact reviewed bytes; only the ACP function is
merged into the parent's CLI. Every other top-level function is byte-for-byte
unchanged, preserving the accepted MCP audit and lazy console-import prerequisites.
The parent then runs one combined115-case group per Python version:
32 ACP controls,64 MCP/shared-secret controls and19 CLI/import controls. Results:
Python3.12:115 passed; Python3.11:115 passed. Ruff passes on the scoped files.

Parent composition snapshot:
`26713a0b7f228f338cba0212e63a61e5d41c08ef6533cf7abe0d8b396c5c0cbd`.
Complete parent-base-bound patch:
`a54697d4b1f7eedfbd536e43034e0274c33327a73d80ec10a9960f3aa678951c`.
Independent composition review verifies both identities with no actionable finding.
The parent rechecks the exact source/patch and both115-case JUnit results before
accepting. Raw evidence, source checks and `acceptance-receipt.json` are under
`.venv/evidence/luna-t19/parent-integration-20261023/`.

These are focused offline checks, not full-suite/G1, official-client/editor,
native/live-provider, final-artifact or soak qualification. Source-only reviewers
do not execute tests, and no commit, push or release follows from local acceptance.
