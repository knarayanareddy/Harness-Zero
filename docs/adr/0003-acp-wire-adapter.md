# 0003. One ACP adapter with temporary legacy wire compatibility

- **Status:** accepted design, implementation pending
- **Date:** 2026-10-23
- **Decider:** parent integration owner under delegated technical judgment

## Context

The kernel's real Commands binding now works, but its internal0.1 wire shape is
incompatible with the official v1 client. Original regression and legacy controls
must remain unchanged; notably R05 creates a session without initialization.
Official requests use different fields and bidirectional permission RPCs. Neither
protocol may grant authority outside the existing launched workspace and gate.

## Decision

Keep a single ACP server/adapter over the existing runtime. Select the official
path using explicit integer-version initialization. Preserve a private legacy
path for existing missing-version initialization and no-initialize legacy session
requests; explicit malformed v1 version values must not fall back to it. Lock a
connection to its selected dialect. Official-intent operations without successful
initialization are rejected. Document this deliberate legacy ambiguity rather than
claiming every missing-version request satisfies the official schema.

The official Python SDK is a pinned test client only. Production does not import
it or construct a second session/permission subsystem.

## Alternatives

- Replace all legacy behavior immediately: rejected because it invalidates the
  unchanged regression contract instead of proving the binding remains repaired.
- Run two independent protocol runtimes: rejected because session authority,
  cancellation and cleanup would diverge.
- Auto-detect each request independently: rejected because a client could mix or
  downgrade wire semantics after initialization.
- Use the official SDK as the whole production server: deferred; current code
  already owns strict IO, redaction and kernel-bound lifecycle invariants, and a
  runtime replacement has not demonstrated a measured benefit.

## Consequences and revisit

One runtime retains the current security boundary and enables real-client tests,
but the private compatibility branch adds temporary maintenance and means some
missing-version inputs intentionally select legacy rather than strict v1 errors.
Do not advertise the intermediate session-only slice as official compatibility.
Revisit legacy removal only with an explicit regression-contract migration after
official-client/editor qualification; never silently weaken the old assertions.
