# Interaction-kind availability for protocol clients

**Status:** kernel prerequisite implemented, source-reviewed and locally verified;
official v1 protocol activation remains pending.  
**Owner:** parent integration owner. **Task:** T20 phase-two prerequisite.

## Problem

InteractionBroker accepts approval/question kinds, but its bus-backed availability
check currently uses only generic interactive presence in a session. A future ACP
v1 client that supports permissions but not structured questions must not make
ask_user wait for a receiver it does not have. This is a prerequisite for the new
consumer, not a claim that the unintegrated session-only T20a path already exposes
v1 turns or questions.

## Design

Allow a trusted subscriber registration to declare supported interaction kinds,
separately from its frame-type filter. Preserve the legacy default: interactive
registrations without an explicit declaration retain both existing kinds. The
kind-aware availability predicate must retain session scoping and reject passive
subscribers. InteractionBroker supplies the actual requested kind. The future v1
permission receiver explicitly declares approval-only support.

These are ephemeral receiver-availability facts, not a new permission grant,
durable store, serialized SDK field or user setting. Untrusted request arguments
cannot create registrations or modify this declaration. No auto-approval or fake
question answer is introduced. See ADR0004 for why frame filters are not reused.

## Bounded prerequisite scope

Expected code files: `harness/kernel/bus.py`, `harness/kernel/interactions.py`, and
a new `tests/kernel/test_interaction_availability.py`. Protocol activation belongs
to the later isolated T20 turn/permission slice, not this prerequisite. Reinspect
actual call sites and tests before finalizing signatures. Do not combine this
change with R22's lifecycle-cache fix or widen it into a capability registry.

## Acceptance

- [ ] Default legacy interactive presence retains approval and question behavior.
- [ ] Explicit approval-only support permits approvals but causes a real question
  request to return unavailable promptly, with no pending question or fabricated
  answer record. Test the real broker, not a fake verdict.
- [ ] Question-only, passive, empty-kind and foreign-session registrations do not
  qualify for an unsupported kind. Removing a receiver removes its availability.
- [ ] A workspace-wide legacy receiver still supports existing session behavior.
- [ ] Frame-type filtering alone does not redefine legacy interaction support.
- [ ] Existing approvals/questions, cancellation, web/CLI and ACP legacy controls
  remain unchanged. Preserve the existing headless-question1s assertion.
- [ ] New failures are observed before the implementation; no test/timeout/golden
  weakening, silent skip or dependency on an unimplemented v1 question UI.

Run new small broker/bus controls first. The parent serializes broader integration
checks after the owned browser fixture exits, binds reviews to exact candidates,
and verifies the final protocol consumer actually uses the declaration. No current
runtime capability, protocol compatibility or G1 completion follows from this spec.

## Current evidence boundary

The integrated three-file snapshot is
`a2483ca67bbce309121d763bc83f2ca76b1904ab8a4ae33227b10c45a04ad3dd`;
complete actual-before-relative patch is
`6bea9628f63f3b477d1afd9d40d187af9e1939a6549c762a13f639c1b2b2a7d2`.
Independent source review is clear. Sixty pure worker cases pass per Python
version; parent202-case bridge/bus/broker/UI groups pass per version. The actual
core consumer is InteractionBroker's requested-kind check. The future permission-
only v1 subscriber registration is not activated yet. This is not full ACP or G1
qualification. Evidence: ignored `t20-interaction-kinds-20261024/parent-integration/`.
