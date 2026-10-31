# ACP compatibility status

**The offline ACPv1 vertical slice is locally integrated and verified with the
real Python SDK0.12.1.** This is not general IDE/editor, native-artifact, live-model
or full-G1 qualification. The internal legacy0.1 dialect remains supported on a
separately locked connection. See `T19_ACP_KERNEL_BINDING.md` for that dialect.

## Integrated offline scope — 2026-10-24

- Integer protocol-version negotiation and connection-owned session create/list,
  with launched-workspace-only cwd validation and no dialect mixing.
- Text and baseline resource-link prompts; links remain reference data and are
  not implicitly fetched. Real text/thinking/read-tool updates use SDK shapes.
- Correlated `session/request_permission` RPCs offer allow-once/reject-once only
  and resolve through the actual Commands/gate. Approval-only availability does
  not make unsupported structured questions interactive.
- Original monotonic approval deadlines include persistence/forwarding time;
  expired, foreign, malformed, duplicate and late responses cannot grant work.
- Cancellation/EOF wait for owned cleanup and prior updates before reporting the
  original prompt result. Existing bounded transport behavior remains.

The real pinned SDK is a development/test dependency, not a production import.
Image/audio/embedded-resource prompts, client filesystem/terminal RPCs, nonempty
client MCP configurations, elicitation, authentication and session replay/load
are not advertised as supported. Legacy questions remain a separate capability.

Reviewed six-file source snapshot:
`9614822e2378ec3cf656bbb8915b14e36cd5bef76bded78bfcf1b4d825d8e261`.
The parent captures its actual legacy/broker before-state and complete integration
patch `ce835464d7e6fa6b48fb9e1a57a59f3ba0b71e12e8601f3a4b7bb134a6a27609`.
The reviewed Git-launcher prerequisite preserves all checkpoint operations and
deadlines; it is not permission to omit prompt/stop hooks.

One clean quiet-window77-case ACP/broker group passes on each Python version,
including unchanged original R05. After exact destination rechecks and integration,
the real parent's414-case bridge/UI/broker/privacy/session/checkpoint/resource
groups pass on3.11 and3.12. Earlier timing failures remain retained, not rewritten
as successes or attributed solely to host load. Full suites and editor/artifact
tiers remain separate. Evidence: ignored `t20-parent-integration-20261024/` and
`g1-git-launcher-20261024/owner-quiet-composed-acp/`.

## Real official-client probe — 2026-10-23

### Subsequent frozen-engine check — 2026-10-25

The current frozen engine `4f4d1bb5…` also completes an actual offline raw-JSON-RPC
ACPv1 text turn and three isolated allow-once/reject-once/cancelled-permission
cases, each with canonical persistence and clean EOF exit0. Source reads are
denied and HOME/config/workspace are synthetic and outside the checkout. This is
additional exact-binary evidence, not an official-SDK-on-binary or real-editor
test. A cancelled permission denies that action; it is not a process-cancel trial.
Receipts are in ignored `current-runtime-artifacts-20261024/native-acp-*`.

### Historical specification mismatch

The official Python SDK `agent-client-protocol==0.12.1`, generated against
`refs/tags/schema-v1.19.0`, was installed only in a disposable test environment.
Its wheel SHA-256 was verified against PyPI before installation:
`aaf3cc301ed87d9e2c4ccb91adfe1cd58784355f6ce7e513f50e7ca2391862ef`.
The exact SDK release identifies Apache License2.0; no project-license decision or
production SDK import follows from this test-only use.

The real `spawn_agent_process` client sent `initialize(protocol_version=1)` to
the actual parent `python -m harness.cli acp` process. Captured wire traffic shows
an integer1 request and a string0.1 reply. The SDK raised a typed validation error
specifically because protocolVersion must be an integer. This is a reproduced
compatibility failure, not a bad keyword, missing dependency or fixture timeout.

The child exited0 and stderr was drained. Only owned temporary workspace/config/
HOME paths were used; no provider call, credentials or editor interaction occurred.
No later session/prompt RPC was sent after initialization failed. Raw commands,
package versions/hashes, wire capture and cleanup evidence are retained under the
ignored `.venv/evidence/luna-t20-sdk-probe-20261023/` directory.

Primary references:
- https://pypi.org/pypi/agent-client-protocol/0.12.1/json
- https://github.com/agentclientprotocol/python-sdk/releases/tag/0.12.1
- https://raw.githubusercontent.com/agentclientprotocol/python-sdk/0.12.1/schema/VERSION
- https://agentclientprotocol.com/protocol/initialization

## Intermediate history and remaining acceptance

The earlier isolated T20a branch starts from accepted checkpoint469de56 with
negotiation and connection-owned sessions only. That incomplete intermediate is
not what is integrated: turn/update/permission/cancel/EOF behavior and the shared
deadline prerequisite are completed and reviewed before parent integration.
The accepted design and legacy compatibility exception are in
`../specs/2026-10-23-acp-v1.md` and `../adr/0003-acp-wire-adapter.md`.

A supported real-editor journey is separately required. The probe did not find
Zed in `/Applications/Zed.app` or `~/Applications/Zed.app`; that does not establish
absence elsewhere. No editor was installed, launched or configured. That tier is
unverified, and SDK-only success would not close it.
