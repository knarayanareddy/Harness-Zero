# 0004. Separate interaction support from event-frame filters

- **Status:** accepted design, implementation pending
- **Date:** 2026-10-23
- **Decider:** parent integration owner under delegated technical judgment

## Context

An interactive registration currently represents generic human-client presence.
Its event-type filter controls frames delivered to that particular queue, not all
interaction kinds the connected client can handle. The legacy ACP path uses a
session-specific question queue alongside a separate approval forwarding channel.
Inferring client capabilities from that single queue's filter would break it.

## Decision

Use an explicit optional interaction-kind declaration on trusted in-memory
registrations. Legacy registrations default to their current support. A kind-aware
predicate is used by the existing broker; a future permission-only v1 receiver
declares approval-only support. Retain session and lifecycle ownership.

## Alternatives

- Infer support from event types: rejected because transport queue partitioning is
  not the same fact as peer interaction support, and legacy behavior would change.
- Keep generic presence only: rejected for the new permission-only consumer because
  unsupported questions could wait indefinitely for an absent interface.
- Auto-answer or cancel every unsupported question after publication: rejected
  because it creates false user decisions or mislabels protocol inability as a
  user cancellation.
- Add persistent per-session capability settings or another registry: rejected;
  availability belongs to the lifetime of the actual connected receiver.

## Consequences and revisit

There is one additional ephemeral registration fact, but no second authorization
authority. Defaults preserve existing clients while new clients can be narrower.
Registration declarations are still trusted application code, not plugin isolation
or proof that a human saw an event. Revisit only when another real interaction kind
or transport consumer requires a tested extension; do not advertise this design as
implemented until both its broker seam and protocol consumer are verified.
