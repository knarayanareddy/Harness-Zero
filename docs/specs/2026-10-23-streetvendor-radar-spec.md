# 05 — StreetVendor Radar: relevant city changes for a mobile food vendor

**Status:** Draft specification; no implementation exists.
**Inspired quality pattern:** Background monitoring that stays quiet, with a visible reason log for items it filters.
**Target:** One independent mobile food vendor operating in one city.

## Problem and hypothesis

Mobile vendors must monitor scattered notices that can affect a route, operating location, permit, or product. The hypothesis is that a profile-aware radar reduces irrelevant reading while making missed/filtered items auditable. Validate the exact sources and workflow with one vendor association and two operators; do not claim the radar replaces permit counsel or official notices.

## Product promise and scope

Once per scheduled run, ingest a short, enumerated list of public sources and compare candidate items against one vendor’s approved profile (service area, vehicle/permit identifiers, menu categories, operating dates, and known routes). Surface only items with a concrete possible action; retain every reject with reason and source link. First release supports one city and a few enumerated feeds. No legal advice, permit filing, or automatic route/permit changes.

## Data and workflow

Source record: `source_id`, `external_id`, canonical URL, fetched time, publication time, body hash, source freshness, parser version, source status. Vendor profile: explicit facts and their owner/source. Triage record: decision (`ALERT`, `REJECT`, `INVESTIGATE`), profile fact IDs, evidence snippets, action type, confidence/uncertainty, rule/model versions, reviewer disposition.

Cheap deterministic deduplication and metadata filter → title/summary triage → only `INVESTIGATE` items receive expensive attachment extraction → decision proposal → deterministic source/fact validation → owner inbox + rejection log. A feed outage is a separate visible `SOURCE_STALE` condition, never “no relevant items.”

## Agent and policy boundary

Strands performs semantic relevance assessment and concise explanation; deterministic code owns profile matching for hard constraints, date parsing, deduplication, source freshness, and notification policy. Model output is schema-validated and must quote a span from the source. It cannot claim a legal obligation from an unsupported text fragment or change the vendor profile. Any alert requiring a payment, application, route change, or compliance action is advisory until the owner approves it.

## Acceptance criteria

- **SV-01 Source inventory:** each source has owner, license/terms review, fetch schedule, parser, freshness threshold, and outage behavior; unapproved sources are disabled.
- **SV-02 Provenance:** every surfaced item and every cited fact is traceable to the fetched source/version/span; broken or changed URLs are shown as unavailable.
- **SV-03 Quiet is auditable:** every triaged item, including rejects and investigation failures, appears in a searchable decision log with a reason. Failed attachment parsing is `REVIEW_REQUIRED`, not a silent reject.
- **SV-04 Source staleness:** if required feeds exceed their configured age, dashboard status becomes stale and the “all clear” message is suppressed.
- **SV-05 Profile control:** profile changes require owner confirmation and produce versioned diffs; the model cannot edit or infer a profile fact.
- **SV-06 Injection:** prompt instructions inside city notices, PDFs, or HTML cannot alter tools, profile, classification policy, or external actions.
- **SV-07 Duplicate safety:** the same external item delivered twice creates one decision; meaningful source revisions create a linked new version.
- **SV-08 Triage eval:** at least 100 synthetic, source-derived cases include relevant, irrelevant, ambiguous, stale, duplicate, attachment-only, and adversarial examples; report recall, precision, abstention, and per-source breakdown. Do not optimize only for quietness.
- **SV-09 Notification controls:** per-owner cadence, quiet hours, and notification caps are deterministic; high-priority configured items bypass only the cap under an explicit rule.
- **SV-10 Cost:** `make eval` uses scripted inference by default; optional live run has item and dollar caps, a kill switch, and cost output.
- **SV-11 Demo:** show source fetched → relevant item with cited evidence → irrelevant item with rejection reason → stale source warning → owner-approved next step. No real permit is filed.

## Evaluation and delivery

Primary product metric is useful-alert precision **and** relevant-alert recall, with false negatives reviewed manually. A second metric is the percentage of items whose source evidence remains readable. The demo uses a labeled snapshot, not a claim of current city-feed coverage.

Slices: source adapters and fixtures → normalization/dedup → deterministic profile and freshness policies → Strands triage with schema/provenance checks → rejection/search UI → evaluation, cost controls, and hosted demo.

**Open questions:** chosen city, feed licenses/availability, profile fields vendors will maintain, and source outage tolerance. Do not expand sources until the first city is validated.
