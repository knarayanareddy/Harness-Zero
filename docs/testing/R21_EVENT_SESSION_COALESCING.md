# R21 — queued deltas must retain their session identity

Severity: P1 against BC-05/Q-07. Status: locally repaired and independently
source-reviewed; end-to-end UI/ACP and release qualification remain separate.
Owners: parent-owned T19/T23 prerequisite and T29 supplemental qualification.
The original R1–R19 and T01–T39 IDs are unchanged.

## Measured defect

EventBus coalesced queued model-text, thinking and tool-output deltas when their
event kind, call, stream and turn identifiers matched, without comparing session
identity. Valid typed SDK envelopes with shared or missing turn IDs could merge
two sessions' text under the first envelope. If the owned envelope came first,
its text contained a distinct foreign canary; if the foreign envelope came first,
filtering by session could lose the owned output.

This is a cross-conversation event-correlation/presentation defect, not a claimed
tenant authorization bypass, outside-file read or remote exploit. Session-scoped
subscription filtering is not added by this repair. Consumers still need their
own correct session checks.

## Repair and evidence (`.venv/evidence/`, local/ignored)

- The pre-code parent contract and before bytes are in `r21-parent-base/`.
  Writable scope is only `harness/kernel/bus.py` and
  `tests/kernel/test_bus_delta_fanout.py`.
- **24 cases fail first** on actual canary/ordering assertions in `r21-bus-red.xml`.
  They cover typed text/thinking/tool-output payloads, both publication orders,
  different/absent session IDs and shared/absent turn IDs, with two subscribers.
- One production condition adds session equality to the existing merge key.
  Same-session and None/None behavior is retained; no new state store, API,
  setting, capability or subscription semantics is introduced.
- **33 bus/fanout checks pass on each Python version** (`r21-bus-py312.xml` and
  `...-py311.xml`), including same-session typed coalescing and existing flood,
  fanout and published-object immutability controls.
- The partial ACP candidate's real EventBus/typed forwarding control passes
  after the explicit input amendment (`luna-t19/event-forwarder-r21-green.xml`).
  That is not acceptance of the incomplete ACP frontend or a wire/client test.
- Independent read-only source/integrity review verifies snapshot
  `0d301bc0afb1916520c1c3d066918c5e54899544fd828b4234caece965f0161b`
  and actual complete patch SHA
  `28118c0564086df748df97ca658eae844d9764859b6f66fa2575abeacc5c2159`
  with no findings. Reviewer does not rerun tests. Parent rechecks both before
  accepting; see `r21-acceptance-receipt.json`.
- An earlier restricted reviewer could inspect source but could not execute the
  required hashes. That response is not used as a digest-bound acceptance receipt.

The accepted one-file bus dependency is deliberately applied to the paused T19
and T23 trees. Their original720-file seed manifests remain unchanged; separate
`seed-v2-r21.json` and `input-amendment-r21.json` receipts identify the exact
before/after bytes and new input digest. This is not silently relabelled drift.

T23 rendering and T24 stale-history reconciliation still require their own source
and browser proof. No full-suite, timing-under-load or release claim follows from
this local bus repair.
