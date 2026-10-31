# 03 — BridgeMentor: mutual-consent mentor introductions for adult learners

**Status:** Draft specification; no implementation exists.
**Inspired quality pattern:** Personal agents discover matches while sharing only an approved public card.
**Target:** Adult learners in a community-college, library, or workforce program seeking volunteer mentors.

## Problem and hypothesis

Programs often rely on manual spreadsheets and repeated outreach to match learners with volunteer mentors by goals, skills, location, and availability. The hypothesis is that coordinators spend time on low-fit introductions and participants hesitate to share personal context broadly. Validate with one program coordinator and at least five adult learners/mentors before claiming improved match quality.

## Product promise

Create a consent-based introduction queue: the system finds a plausible match, each person sees a bounded proposal, both may accept or decline, and contact details are exchanged only after both accept. The product does not supervise the relationship or imply that a mentor is licensed to provide legal, medical, financial, or therapeutic advice.

## Participants and flow

Roles: adult learner, volunteer mentor, program coordinator. Onboarding collects goals, skills offered/sought, languages, meeting modes, availability windows, accessibility preferences, and explicit public-card fields. Private notes can guide the participant’s own agent but are never placed on the public card.

`PROFILE_DRAFT → USER_APPROVED_CARD → MATCH_CANDIDATE → BOTH_REVIEW → BOTH_ACCEPTED → INTRO_SENT → FIRST_MEETING_CONFIRMED`; decline, timeout, block, report, or coordinator review are terminal/side states. A one-sided yes never creates an introduction. A decline is final for that pair unless the declining participant explicitly reopens it.

## Matching, privacy, and safety design

Deterministic filters enforce hard eligibility (adult participants, program membership, language/accessibility requirements, availability overlap, blocks, capacity, and active status). A model may explain a candidate match from public-card fields; it cannot infer sensitive traits, make eligibility decisions, contact people, or expose private notes. Ranking is explainable and avoids protected-trait proxies. Participants can inspect and edit their card before activation.

Messages are generated from approved card facts and a fixed introduction template. The system does not reveal phone/email until both sides accept and the coordinator policy permits it. Rate limits, block/report controls, coordinator review, abuse audit, and immediate match suspension are mandatory. No automated conclusion about trustworthiness or safety is allowed.

## Acceptance criteria

- **BM-01 Consent:** no match invitation, intro, or contact exchange occurs before the required participant consents; assert zero outbound effects for missing, declined, expired, or stale consent.
- **BM-02 Card boundary:** public profile output contains only allowlisted, participant-approved fields. Canary private facts must not appear in other profiles, prompts, search indexes, logs, or emails.
- **BM-03 Eligibility:** blocked, inactive, under-18, capacity-full, and nonmember test accounts never appear as candidates, even when the model recommends them.
- **BM-04 Match explanation:** each candidate cites the public fields and deterministic filters that supported it; unsupported personal assertions are rejected.
- **BM-05 Pair consent:** one-sided acceptance is insufficient; duplicate and reordered responses produce one correct state, not duplicate introductions.
- **BM-06 Revocation:** a participant can block/report or revoke an unaccepted match; all pending introductions are cancelled and the action is visible to the coordinator without exposing private report text to the other participant.
- **BM-07 Injection:** malicious instructions embedded in profile fields cannot expand tools, skip consent, reveal other profiles, or send a message.
- **BM-08 Data rights:** profile deletion removes searchable and active user data under the documented retention policy; audit events retain only non-sensitive references needed to prove actions.
- **BM-09 Eval:** synthetic corpus reports hard-filter precision/recall, availability overlap, consent errors, harmful-message reports, and false exclusions separately. Consent violations must be zero.
- **BM-10 UX:** each participant can preview exact outgoing text and accept/decline with keyboard and mobile; no dark patterns, countdown pressure, or default-checked consent.
- **BM-11 Offline path:** `make demo-e2e` runs two participants and coordinator against the same Strands tool/state path using scripted responses; no real account, email, or external network is required.

## Demo and delivery

Demo two adult synthetic profiles with complementary goals, show a match based only on their approved cards, show the exact mutual invitation, demonstrate one-side acceptance does not reveal contact data, then accept on both sides and show the audited introduction. Include a blocked profile control and a prompt-injection attempt that produces no side effect.

Slices: profile schema and visibility projections → deterministic filters → Strands match explanation → pair-consent workflow → block/report and audit → eval, accessibility, deployment, and demo. Do not use real student records in screenshots or seed data.

**Open questions:** program-approved eligibility, safe meeting policy, coordinator escalation hours, and retention. These are partner decisions, not model-generated defaults.
