# 0002. Preserve process-group identity through cancellation

Status: accepted for implementation — owner explicitly approved the supervisor approach.
Date: 2026-10-21. Qualification remains incomplete until the stated gates pass.

## Problem and evidence

T06 now stops ordinary foreground commands, bounds cooperative cancellation and
preserves cancellation through STOP hooks. Independent review found that a
numeric process-group ID is not a durable ownership token after its leader
exits. An inherited child can remain alive with redirected streams, so neither
leader exit nor pipe EOF proves the group is empty. Signalling any later group
with that numeric ID can target an unrelated reused group.

Both sides are executable regressions in
`tests/kernel/test_cancel_process_tree.py`: redirected/live-child cleanup and
`test_dead_leader_cannot_authorize_signalling_reused_numeric_group`. The latter
uses a synthetic reused-ID probe; no unrelated real process was signalled.

The earlier conservative checkpoint refused a signal/escalation when known
leader exit makes ownership unconfirmed. It reports uncertainty rather than
claiming termination. At that checkpoint **two descendant-cleanup tests remained
red**. The implemented supervisor subsequently passed these tests; final local
evidence and native/global limitations are in `docs/testing/T06_SUPERVISED_CANCELLATION.md`.

## Recommended decision

Add an owned POSIX process-group supervisor/identity anchor for foreground
execution on supported macOS/Linux hosts. Its lifetime must pin group ownership
until cleanup completes; retain it through graceful termination, issue any final
group signal only while identity is established, and reap it exactly once.
Do not merely check `killpg(id, 0)` and call existence ownership.

### Chosen protocol and ownership boundary

An isolated stdlib supervisor remains the live session/group leader and catches
TERM. It is launched by absolute trusted file with the running interpreter, or
by the frozen engine's private entrypoint. The engine uses private pipes: a versioned
READY/EXIT status channel and TERM/KILL control channel. Only the live supervisor
signals its own group (`os.getpgrp()`); the engine never reconstructs authority from a numeric
PGID. Owner pipe EOF stops the group. Command exit status is reported separately
from the supervisor's final signal status, and normal payload completion closes
the owned foreground scope, including untracked descendants. Independent work
must use the existing Jobs/background ownership handoff, not an orphaned `&`.

Payloads retain standard IO, literal argv and signed signal exit status; private
descriptors close before exec. The first shell prototype was superseded because
dash cannot set up its multi-digit FDs and shell status loses signed signal exits.
The SDK helper ships in the wheel; a frozen build relaunches its own binary using
trusted actual worker state (not caller overrides) to preserve leader identity.
Installed-wheel and actual one-file smokes passed; native qualification remains.
Native sandbox wrapping remains around the payload command; the
trusted supervisor is outside that wrapper, like the engine. Linux bubblewrap's
new session/parent-death behavior requires a real native qualification lane.
Unexpected supervisor loss yields an unconfirmed outcome, never numeric-PGID
fallback. Deliberate group escape or arbitrary same-user code is not isolated.

Implementation must first specify/test: command exit-status versus supervisor
exit-status, stdin/stdout/stderr transparency, cancellation during startup,
signal-ignore/redirected descendants, repeated cancellation, group-ID reuse,
normal completion, background-job handoff, cleanup failure and lifetime bounds.
No new authority may be granted. Descendants deliberately escaping the group and
arbitrary trusted in-process Python remain outside an inherited-group guarantee.

## Why explicit approval

This is an execution-backend/lifecycle change, not another local boolean guard.
It adds process/protocol overhead and touches packaging plus native sandbox
compatibility. The implementation must work from installed wheels and frozen
engines, without assuming an ambient Python executable or helper path is
available inside a sandbox. Background ownership/normal-completion semantics
must be explicit before implementation. Split T06 into smaller follow-up tasks
if the existing five-file implementation scope is exceeded.

## Alternatives

| Option | Consequence |
|---|---|
| Owned supervisor/anchor (recommended) | Can close the identity/descendant gap; needs explicit protocol, resource and native/artifact tests |
| Retain fail-closed unconfirmed termination | No claim that redirected descendants are stopped; T06/R3 remain incomplete and unattended qualification stays blocked |
| OS-specific stable identity mechanisms | Possible later design; Linux and macOS APIs differ, so a portable implementation cannot be asserted from one host's API |
| Keep signalling an existing numeric group | Rejected: existence does not establish ownership after reuse |

## Rollout

Keep current tests visibly red until the chosen implementation satisfies both
ownership and cleanup. No skipped assertions, stale-ID safety weakening or
unrelated-process kill is an acceptable way to make them green. Requalify normal
commands, jobs, native confinement and delivered artifacts. No commit, release,
new credentials or user applications are authorized by this accepted ADR.
