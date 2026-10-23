# T18c — canonical MCP audit and read ledger

Status: parent integration is locally tested and independently source-reviewed;
the scoped canonical audit/read-ledger and privacy repair is accepted locally.
Whole T18, G1 and release qualification remain open.

## Candidate behavior

Each admitted external MCP tool invocation appends its canonical call before gate
execution and a paired typed result before replying. The connection's real session
log supplies the gate's read-before-edit facts; a successful read from another
connection, a failed read or an unread path cannot authorize an edit. Existing
session locking, capability restrictions and cancellation handling remain.

Canonical carriers are assistant.message/assistant for the tool-use block and
tool.result/user for its result, with origin=external_mcp and model_generated=false.
These carriers are required by the existing context projector; they do not assert
an LLM utterance or model usage. A regression passes the actual records through
derive_messages and checks the paired IDs, not just JSON field presence.

Call-append failure prevents handler admission. Post-effect result redaction,
projection or append failure reports uncertainty and fences further execution on
that connection. Nothing automatically retries or claims to undo a possible effect.
Malformed argument containers are rejected before recording invalid canonical
input. Internal call identities do not collide for integer42 versus string42;
wire request ID types remain unchanged.

## Privacy review follow-ups

Gate schema-validation errors can return before normal text sanitization. Audit
TextBlock copies therefore receive their own redaction, including complete repr
and JSON-escaped known-secret representations. Actual gate arguments/results and
typed image blocks are not mutated; result.metadata.full_content is not logged.

Shared Secrets.redaction_values discovers file values, credential-named environment
values, stored-name environment aliases and arbitrary environment values actually
resolved through Secrets.get. Resolved file/environment values remain in memory across rotation;
no new persistent store, capability or lookup precedence is introduced. Discovery
runs before call persistence and again before result persistence. Failure does not
silently disable redaction. Ordinary HOME/PATH values are not secrets by presence.

Redaction discovery uses a strict file load: read failures, malformed JSON and
invalid file shapes raise instead of silently returning an empty mask set. The
ordinary lookup path retains its prior fallback behavior. An absent file is valid;
already resolved credentials remain protected after rotation/removal. Actual file
read failures and malformed shapes are tested, not only a mocked discovery method.
Pre-action discovery failures return a fixed error confirming execution was not
started; raw filesystem diagnostics and the private configuration path are not
returned to the MCP peer. The general third-party executor exception contract is
not changed by this narrowly owned boundary repair.

The canonical exact-match minimum remains six characters. This is not universal
classification of unrequested, innocuously named environment values, a global
gate-wire sanitizer repair or scanning of encoded image contents.

## Evidence and limits

Ignored evidence is under `.venv/evidence/luna-t18c/` and
`.venv/evidence/secret-source-coverage-20261023/`. Earlier three-file candidates,
both review findings, actual before-content, patches and failing runs are retained.
The expanded candidate explicitly adds the parent-owned shared Secrets dependency.

The exact parent integrated candidate passes64 focused checks on each Python
version:41 MCP audit/read-ledger/CLI/export/cancellation/server controls plus23
shared settings/secrets controls. Actual CLI enum-secret cases, canonical replay, late credential
resolution and uncertainty-fencing regressions failed first. Failure injections
are unit controls, not actual filesystem-failure e2e claims. One earlier cold
initialization timeout remains preserved, not erased by subsequent passes.

Accepted slice snapshot before later shared-CLI composition:
`be300c699769d8b7af6bc3b06d5686e54aad5019e681a66aa9b3e6b328af2d7a`.
Complete actual-base-bound patch:
`4db0bab09d19204bab5b04a460f1e6b2ffe2750c420f4b89c94a70123272845e`.
Independent source/integrity review verifies both with no actionable final-delta
finding; the parent rechecks both and the two64-case JUnit results before accepting.
The reviewer does not execute tests. See
`parent-discovery-error-20261023/acceptance-receipt.json` for the review chain and limits.
Later headless-import and ACP integration preserve the exact MCP helper functions;
the current combined115-case parent group passes per Python version. Its updated
shared-CLI source identity and composition-review status are recorded in
`T19_ACP_KERNEL_BINDING.md`, not silently substituted into the earlier receipt.

Official-host interoperability, server non-text delivery, Git-state approvals,
broader background behavior, full-suite/native/live/final-artifact qualification
are separate. Passing these subsets does not close whole T18 or G1.
