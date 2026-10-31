# T24 — transcript and pending-work reconciliation

Status: UI and saved-session metadata repairs are locally integrated and
source-reviewed. T24 remains open for final combined qualification alongside
the pending shared approval-expiry and stream-shutdown repairs. This is not a
release or full-G1 receipt.

## Current UI identity

Five-file snapshot:
`6398f1453d0f6b40e36623ade525429b2771027342ab4c7e66751c45c5701285`.
Complete follow-up patch against the captured25e base:
`f19c7cac922a19239a4d88ec44af4a5ea753d62502ac5adeab61b7bca2244e71`.
Independent re-review is clear after addressing tool-only follow-up ownership and
sanitized Markdown preservation. The parent rechecks its exact destination before
integration; earlier rejected candidates and their fail-first evidence remain.

Canonical transcript identity is session ID plus `step_index`, not text, arrival
order or an inferred persistent turn ID. Actual tool-use IDs bind tool cards and
orphaned stream nodes to their canonical assistant record. `model.started` closes
the previous model request's pending tool grouping without dropping its exact
call-ID bindings. A delayed completion must not finalize a different live stream.

The timeline's children retain intrinsic height so a full stream scrolls rather
than clipping cards. The actual renderer/CSS browser component fails first with
41px cards containing121px of content; the repair yields121px cards and scrolling.

## Parent checks

-25 focused UI/static/turn/log/original-R07 checks pass per Python3.11/3.12 after
  integration; the required Node22 wrapper executes all26 semantic/static cases.
- The complete `tests/frontends` group subsequently passes85 cases per version.
  This is a frontend group, not the full default suite or a claimed91-case run.
- The earlier202-case bridge/privacy/bus/broker/UI groups predate the latest
  orphan-stream changes. They are historical combined evidence, not a substitute
  for final integration checks after the pending kernel/protocol repairs.

## Actual browser stages

These stages span separate owned fixtures and qualified snapshots. They are not
one uninterrupted journey against the latest source. Completed stages are retained
rather than erased by failed continuation harnesses.

| Stage | Observed behavior | Snapshot boundary |
|---|---|---|
|1|Held actual A history cannot repaint selected B|Earlier95b3 UI, earlier backend|
|2|Older actual B snapshot preserves canonical order, distinct identical prompts and one correctly owned tool card/output|Earlier95b3 UI, repaired R22 backend|
|3|Held native A completion, actual tool boundary and busy snapshot retain one A node; same-turn B has a distinct step|Current6398 UI, a248 broker/bus input|
|4|Pending B approval survives refresh; a controlled absent replay ID triggers automatic canonical reload; native approval writes B once|Earlier25e UI, a248 broker/bus input|
|5|Fresh A is denied through actual fixture policy while disconnected; persisted user-approved B reloads idle without re-execution|Current6398 UI, a248 broker/bus input|

Stage5 observes two legitimate new-A model requests (tool call and continuation)
and zero new-process B model requests. B's two historical requests are not relabeled
as executions in the resumed process. Old A/B compact and full transcript hashes
remain unchanged. A's marker is absent and B's marker is exactly `APPROVED_B`.
The parent inspects the settled B screenshot: approved state, resolution text and
tool output are visible, with no card self-clipping. Owned children exit0 and
their browser contexts, SSE streams and ports close after passing stages3 and5.

The replay-gap test injects an explicitly labeled fixture notice and an absent
checkpoint; it does not establish natural500-event ring-buffer expiry. Structured
question-browser, broad accessibility/viewport, native app, live-provider and
artifact tiers are not inferred from these checks.

## Harness failures and remaining persistence repair

Failed attempts are preserved. Their corrected fixture-only causes include an
empty USER-carried tool-result text being mistaken for a prompt, invented canonical
`metadata.call_id` and notice-code fields, a decorative-symbol exact-text locator,
CSS-uppercase `innerText` used for a DOM-text assertion, and a screenshot helper's
shadowed binding. These are not counted as product failures or browser passes.
Each corrected classifier is checked against actual saved records before reuse.

The real product findings are the reproduced orphan-stream duplication, clipped
card layout, and session metadata mutation on restart. The first two are locally
repaired. The last is repaired in a separate reviewed slice: loading existing sessions
previously rewrote original `created_at` values. Browser fixtures preserve and
report those changes rather than silently restoring them or calling them tool
re-execution. The repaired two-file metadata snapshot
`e10ee3d54888286079e45878eeb70d550b17dc8b58b2f771115aefc67918d4bd`, patch
`45cdf5f45c6450255d5ca183fef9547f0cef2470d5bebc2a894045aabe4eda05`, is
source-clear and integrated. A shared read-only reader gives get/list consistent
finite numeric timestamps and non-persisted malformed-shape fallbacks. Parent71
metadata/lifecycle/Commands/log/UI cases pass per Python version, including31 new
metadata cases and real restart/no-reexecution controls. Earlier browser metadata
mutation remains historical evidence. Original frozen candidate c651 predates
the repair; rebuilt190b includes it and verifies read-only metadata preservation
through actual frozen startup/list/get. Final complete-product qualification still
waits for the shared-kernel/protocol integration and remaining artifact tiers.

## Retained evidence

All paths below are local, ignored `.venv/evidence/` directories:

- `t24-browser-20261023/`, `t24-browser-continuation-20261024/`: stages1/2 and original failures.
- `t24-layout-fix-20261024/`: CSS fail-first geometry and source review.
- `t24-orphan-stream-fix-20261024/review-followup/`: exact candidate, re-review and parent25-case receipts.
- `t24-parent-frontends-20261024/`: complete85-case groups on both versions.
- `t24-browser-stage3-replay-20261024/`: original failed stage replayed green.
- `t24-browser-approval-20261024/`: completed stage4 and prior harness errors.
- `t24-browser-stage5-canonical-20261024/`: completed stage5, trace, screenshots and actual-file preservation.
- `t24-session-metadata-20261024/`: distinct metadata reproduction and pending repair.
