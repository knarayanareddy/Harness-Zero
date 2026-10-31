# 09 — CenterSignal: verified status for cooling and warming centers

**Status:** Draft specification; no implementation exists.
**Inspired quality pattern:** Provider readback becomes evidence; a user-reported failure repairs the verification queue.
**Target:** One city’s small network of cooling/warming centers and the community group maintaining their public status page.

## Problem and hypothesis

People may travel to a listed center whose hours, capacity, accessibility, or entry conditions have changed. The hypothesis is that a regularly verified, time-bounded status feed is more useful than a static directory. Validate with center operators and community navigators; do not claim every center can be reached or every listing is current.

## Product promise and scope

CenterSignal verifies only a short, explicit set of center facts: currently open/closed, staffed-through time, approximate capacity band, wheelchair-accessible entrance, pet policy, and languages currently supported. A provider call or approved source update creates an evidence-backed status with a freshness expiry. A public user can report a closed door; the report elevates the center’s verification task but does not directly publish a closure.

Initial deployment: one city, synthetic center records, sandbox voice call/turn simulator, public read-only directory. Real provider contact requires separate partner authorization, approved disclosure, calling hours, and number allowlist.

## Evidence and update policy

Provider statement → structured extraction proposal → exact readback of changed fields → explicit provider confirmation → deterministic field policy → atomic status update with quote/time/source → expiry timer. A failed or ambiguous readback becomes `NEEDS_REVIEW`; a closure/removal, eligibility rule, or safety-related change requires a human curator. A `capacity` value is always a range/band, never a model-invented exact count.

Store `Center`, `VerificationTask`, `CallAttempt`, `ProposedChange`, `EvidenceQuote`, `PolicyDecision`, `StatusSnapshot`, and `UserFailureReport`. Audit events are append-only. User failure report and task promotion share one database transaction; duplicate reports merge into the existing open task.

## Acceptance criteria

- **CS-01 Disclosure and consent:** every sandbox script clearly identifies the automated caller; live call is impossible without center consent, approved window, per-center cap, number allowlist, and explicit live-mode flag.
- **CS-02 Readback evidence:** auto-update requires provider-stated value, assistant readback containing the full canonical value, and explicit provider confirmation in order; “maybe,” correction, or silence fails.
- **CS-03 Field allowlist:** only configured low-judgment fields can auto-update. Closure/removal, capacity beyond configured band, contradictory response, and unrecognized field route to curator.
- **CS-04 Atomicity:** when any field in one call requires review, no field from that call is partially published; test mixed safe/unsafe changes.
- **CS-05 Freshness:** every public status shows last verified time and expires to `UNKNOWN` after its configured TTL; stale status is not rendered as open.
- **CS-06 Failure report:** user report creates or raises one open verification task in the same transaction; duplicate submissions do not multiply tasks; the status remains unverified until provider evidence or curator decision.
- **CS-07 Evidence integrity:** every public field maps to a source quote/event and policy version; append-only audit rejects update/delete and detects tampering.
- **CS-08 Privacy:** no audio retained by default; transcripts redact personal contact details; reporting user identity is optional and never exposed to centers without consent.
- **CS-09 Eval:** scripted provider personas include exact confirmations, hedges, contradictions, stop requests, wrong speaker, prompt injection, and no answer. Zero unsafe auto-updates; report safe-change coverage and over-escalation.
- **CS-10 Directory UX:** user can filter by open/unknown, accessibility, and language; “unknown” is visually distinct from “closed”; mobile keyboard/screen-reader flows pass.
- **CS-11 Demo:** simulate a verified status, an expired status, a false/ambiguous readback held for curator, and a user-reported closed door promoted to the queue.

## Demo and rollout

The demo contains no real phone calls or real shelter claims. Every record is synthetic and labeled. Before pilot, a named city partner must approve the field vocabulary, verification cadence, call consent, retention, and a staffed fallback for users who cannot rely on the page.

Slices: directory/status schema + TTL → task queue and transactional failure report → voice turn simulator/readback verifier → deterministic policy and audit chain → public/private UI → evaluation, consent operations, and hosted demo.

**Open questions:** chosen city, authoritative center list, definitions for capacity/accessibility fields, status TTLs, and source-of-truth dispute handling.
