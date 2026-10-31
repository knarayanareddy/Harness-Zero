# 06 — UtilityShield: utility-disconnection case triage for an assistance clinic

**Status:** Draft specification; no implementation exists.
**Inspired quality pattern:** Deterministic deadline arithmetic, transparent queue ranking, and a durable caseworker interrupt.
**Target:** One community utility-assistance clinic in one utility/jurisdiction pair.

## Problem and hypothesis

Clinic staff may receive disconnection notices with different dates, amounts, service addresses, and payment-plan details. The hypothesis is that a queue can reduce missed urgent cases by structuring and verifying facts before a caseworker reviews them. Validate with clinic staff and counsel. This product is not legal advice and must not tell a customer that service is protected or that a deadline is legally extended.

## Product promise and launch boundary

Read a notice, extract cited fields, calculate only explicitly configured operational dates, prioritize the clinic’s review queue, and draft a caseworker-approved inquiry or assistance packet. Initial release supports one utility and one locally reviewed rulebook. It does not send a dispute, promise continued service, negotiate a payment plan, or contact a utility without explicit client consent and caseworker approval.

## Core data and deterministic clock

`Notice` stores source file hash/page spans, utility, service account token (pseudonymous), stated amount, notice date, stated shutoff date, extracted conditions, and verification state. `Rulebook` stores jurisdiction/utility, source URLs and versions, effective interval, checked-by, and deterministic formulas. `CaseDeadline` stores computed date, source basis, trace steps, uncertainty flags, and rulebook version.

The deterministic engine may compute a date only from verified inputs and a reviewed rule. If dates conflict, source coverage is missing, timezone/holiday logic is unknown, or the notice is unreadable, it refuses to finalize and surfaces the case. “Operational target” and “legal deadline” are different fields and labels.

## Queue and human authority

Use a discrete severity ladder: immediate service-risk signal; date within configured interval; unresolved/no reliable clock; monitor; closed/confirmed resolution. The model may extract facts and explain ranking; it cannot set the final severity, make legal conclusions, close a case, or issue advice. Caseworker capacity changes which cases interrupt now, not the underlying severity; held cases retain rank and reason.

Approval binds to the exact cases, evidence, computations, rationales, and destination content shown. If anything changes, approval is invalid. A timeout or malformed response leaves the case pending and the run visibly incomplete, never green.

## Acceptance criteria

- **US-01 Jurisdiction gate:** unsupported utility/jurisdiction refuses deadline computation; tests prove no fallback to generic rules.
- **US-02 Evidence gate:** every date/amount used in ranking or draft is linked to a notice span and verified against it; unreadable/conflicting values are held.
- **US-03 Deterministic rules:** calendar and arithmetic tests pin exact expected dates/amounts, boundaries, missing data, daylight/timezone cases, and source-version changes.
- **US-04 No advice:** generated text cannot say eligibility, legal entitlement, protected status, or guaranteed continuation unless a human-authored approved template explicitly states the supported fact; adversarial tests cover these claims.
- **US-05 Capacity fairness:** queue capacity never deletes, lowers, or hides cases; it changes interrupt allocation only and preserves a durable “held because capacity” reason.
- **US-06 Approval binding:** stale candidate digest, omitted case, changed rationale, wrong actor, invalid response, or replayed decision is refused before any side effect.
- **US-07 No unauthorized contact:** offline/demo mode has no network egress. Any future outbound utility contact is separate opt-in capability, client-consented, caseworker-approved, and idempotent.
- **US-08 Recovery:** process restart preserves pending approvals and audit history; uncertain external outcomes require reconciliation, not automatic retry.
- **US-09 Eval:** 60+ synthetic notices exercise date conflicts, unreadable scans, duplicate notices, multiple service addresses, notice correction, and capacity overload. Report undertriage and overtriage separately; every missed urgent case is retained.
- **US-10 Demo:** show a high-risk case, an exact deterministic trace, an ambiguous notice refused, an approval bound to the displayed packet, and a stale approval denied.
- **US-11 Scope disclaimer:** every screen/export states the supported jurisdiction and rulebook date; never presents a prototype calculation as legal advice.

## Delivery slices

1. One jurisdiction’s source-reviewed rulebook and pure calculation engine.
2. Notice upload/extraction with evidence spans and refusal path.
3. Deterministic ranking and capacity-held queue.
4. Caseworker approval, digest binding, append-only audit, and restart recovery.
5. Responsive queue, synthetic corpus, demonstration, and operations runbook.

**Open questions:** utility/clinic partner, authoritative source owner, counsel review, retention/consent, rule update cadence, and fallback contact path. No live use until all are signed off.
