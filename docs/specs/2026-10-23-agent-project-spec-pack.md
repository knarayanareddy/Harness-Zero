# Agent project spec pack

**Status:** Proposed specifications; no SafeFix-family project is implemented here.
**Task:** DOC-SPEC-PACK-01
**Date:** 2026-10-23
**Owner:** Project sponsor / implementation agent to be assigned

## Purpose

This pack defines ten independent, original agent-project briefs. Each one borrows a *quality principle* observed across strong human-facing agent projects—bounded autonomy, evidence, a complete workflow, and fail-closed handoffs—without copying their code, assets, datasets, branding, or exact use case. The briefs are alternatives, not ten features of one application.

| Spec | Adjacent quality pattern | New project / target |
|---|---|---|
| [01 GrantSnap](2026-10-23-grantsnap-spec.md) | Photo intake + deterministic eligibility and arithmetic | Small nonprofits preparing grant reimbursement packets |
| [02 VolunteerBalance](2026-10-23-volunteerbalance-spec.md) | Private constraints + measured fair allocation | Mutual-aid groups assigning recurring volunteer work |
| [03 BridgeMentor](2026-10-23-bridgementor-spec.md) | Private agents + mutual consent | Adult learners seeking local mentors |
| [04 ToolTutor](2026-10-23-tooltutor-spec.md) | Evidence-gated, one-step-at-a-time workflow | Makerspaces onboarding members to low-risk equipment |
| [05 StreetVendor Radar](2026-10-23-streetvendor-radar-spec.md) | Quiet, profile-specific background monitoring | Independent mobile food vendors |
| [06 UtilityShield](2026-10-23-utilityshield-spec.md) | Deterministic deadline queue + caseworker interrupt | Utility-assistance clinics in one verified jurisdiction |
| [07 LibraryLoop](2026-10-23-libraryloop-spec.md) | Replay-tested bounded policies | Community tool-lending libraries handling return exceptions |
| [08 ColdWatch](2026-10-23-coldwatch-spec.md) | Alert-triggered outreach + human escalation | Opt-in neighborhood cold-weather check-ins |
| [09 CenterSignal](2026-10-23-centersignal-spec.md) | Voice readback + evidence-backed directory updates | One-city cooling/warming-center status directory |
| [10 SchoolNotice](2026-10-23-schoolnotice-spec.md) | Grounded multilingual document-to-action workflow | Families processing school logistics notices |

## How to hand a spec to an implementation agent

1. Give the agent **this index and exactly one project spec**. Do not combine projects or expand the vertical before its first end-to-end slice passes.
2. Start in a new, standalone repository named for that project. **Do not implement these products inside Harness-Zero** or interpret these docs as claims about its runtime.
3. Before coding, return a plan that maps every acceptance ID to a test/manual check, a file/symbol owner, and an implementation slice. Resolve open questions by writing down a conservative assumption; ask the sponsor only when the choice affects user authority, real-world communication, safety, data retention, or paid spend.
4. Pin the Python, Node, Strands SDK, model/provider, and deployment versions actually used. Verify SDK calls against the installed package. Do not invent APIs or claim optional integrations are wired merely because config exists.
5. Build the deterministic domain core and offline scripted-provider path first. A real model path may be opt-in. No real-world contact, account creation, provider action, paid inference, or production data is permitted without separate explicit authorization.
6. Work in thin, reviewable slices. Run the specified checks after each slice. Do not weaken tests, thresholds, timeouts, or eval cases to obtain green.
7. Deliver source, license, setup, architecture, operations notes, seeded demo data, a working demo, and a short video. State what is synthetic, simulated, live, and unverified.

## Shared quality contract

Every project spec inherits these non-negotiable requirements. Its local acceptance criteria add domain-specific gates; they do not weaken these.

### Authority and model boundaries

- Strands must execute on the real product path: an offline deterministic provider may replace model inference, but may not replace the agent loop, tool validation, policy engine, or state transition path.
- The model may interpret, extract, summarize, or propose. Deterministic code owns identity, authorization, consent, calculations, policy versions, state transitions, idempotency, and external side effects.
- The agent has no unconstrained write/send/delete tool. Every externally visible side effect passes through an allowlisted application function that checks actor, case state, consent, policy version, and idempotency.
- Unknown tools, malformed structured output, missing evidence, stale approvals, ambiguous responses, timeouts, and provider outages fail closed to a durable, visible pending state. They must not become a success-shaped no-op.
- A human approval binds to the exact reviewed content, recipient, attached evidence hashes, policy version, and case version. Any change invalidates it; it cannot authorize a later, different payload.

### Evidence, privacy, and product behavior

- Every material extracted fact has a source reference (user statement, file/page/span, system record, or versioned policy). The UI distinguishes fact, inference, proposal, and unknown.
- User-provided text, images, documents, OCR, and provider messages are untrusted input. Test prompt injection through every enabled channel.
- Keep personal data out of test fixtures, logs, screenshots, and model prompts unless essential. Default demos use synthetic data; strip image metadata; define retention and deletion behavior before any pilot.
- The product has useful pending, error, refusal, empty, offline, retry, and human-handoff states. “No action” is visible and explainable.
- Provide keyboard operation, mobile layouts, screen-reader labels, readable contrast, and a tested zoom/reflow path for the primary workflow.

### Verification contract

Each new repository must implement these commands and make them work from a clean clone:

| Command | Contract |
|---|---|
| `make setup` | Installs pinned dependencies without requiring cloud credentials. |
| `make verify` | Runs offline unit, domain, security, API, UI, lint, and type checks; fails on missing suites or zero-test collection. |
| `make demo-e2e` | Exercises the full synthetic user journey through the real app, agent/tool loop, policy boundary, persistence, and sandbox adapter. No external contact or paid provider call. |
| `make eval` | Regenerates a versioned report from committed synthetic cases; shows misses, abstentions, false positives, false negatives, and run configuration. |
| `make live-smoke` | Optional and explicitly enabled; identifies provider, model, region, and estimated spend; refuses to run without a configured hard budget. Not part of default CI. |

### Recommended standalone implementation design

- **Stack:** Python 3.12, FastAPI, Pydantic v2, a pinned Strands Agents SDK, React + TypeScript + Vite, and Node 22. Pin exact versions in lockfiles. Verify all Strands calls against the installed SDK; do not invent hook or interrupt APIs.
- **Layers:** browser → authenticated API → application service/state machine → deterministic domain policy → Strands interpretation/proposal → sandbox adapter → persistence and append-only audit. Keep the domain kernel callable without a model or web server.
- **Persistence:** SQLite WAL is acceptable only for a single-process synthetic demo. Declare that limit. Use a transactional database before enabling multiple workers or a real pilot; do not imply multi-worker guarantees from SQLite tests.
- **Model path:** default tests and demo use a scripted Strands `Model` implementation, but exercise the real agent loop, schemas, tools, authority checks, and state transitions. Bedrock/AgentCore is opt-in and cannot be required for `make verify`.
- **API contract:** FastAPI-generated OpenAPI 3.1 is the source of truth. Every write requires an idempotency key and expected resource version. Use 201 for creation, 409 for stale version/approval, 422 for semantically invalid content, and one stable error envelope with `status`, human `detail`, and `trace_id`. Return 404 for another user’s resource. CI validates the generated schema against the running app.
- **Files:** validate size and detected media type, hash uploads, strip image metadata, use non-public storage, and keep raw files out of logs and prompts unless a specific model step needs them. Tests use synthetic assets.
- **Audit and retries:** events record actor, action, resource version, evidence hashes, policy/model versions, approval digest, outcome, and idempotency key. Replayed requests return the original outcome; unknown side-effect outcomes are reconciled, not blindly repeated.
- **Deployment:** one documented HTTPS demo backed only by synthetic data and a sandbox effect adapter. Health/readiness, backups, expiry, cost cap, kill switch, and rollback are documented. A real-provider integration is a separate opt-in lane with separate evidence.

Each individual spec must name its concrete resources, state transitions, actors, source-of-truth fields, and domain-specific errors before implementation. Do not ship a hand-written API description that differs from FastAPI’s generated OpenAPI.

The exact implementation can differ, but each command must be truthful and fail nonzero on setup failure. A mock-only path is not proof that a live provider or deployment works. A live-provider result is separate from offline, browser, native, and artifact results.

### Spec-to-test traceability

Before implementation, create `docs/acceptance-matrix.md` in the project repository. It must map **every** criterion ID in the chosen spec to: implementing module, automated test or manual procedure, expected result, evidence artifact, and status. A criterion without a verification method is unresolved, not implicitly passed. Every authority/security criterion needs a fail-first negative case and a positive control. Never delete or relabel a failing case to make the headline score improve.

At handoff, include the exact source revision, lockfiles, `make verify` output, `make demo-e2e` output, generated `make eval` report, accessibility notes, outstanding failures, and a data/feature matrix identifying each path as scripted, live-provider, or not implemented. Do not claim “production ready,” measured user impact, legal/medical correctness, or award likelihood from local tests.

### Demo deployment and rollback

- Public demo uses synthetic records, sandbox adapters, server-side credentials only, and a hard spend limit. No real phone call, email, text, payment, filing, or service change is enabled by default.
- Live endpoints have rate limits, health/readiness checks, an operator kill switch, and a documented rollback to a known working revision. On rollback, preserve audit evidence and revoke public write access before deleting demo state.
- Any real pilot is a separate approval gate requiring a named partner, approved consent/data-retention policy, human on-call owner, and a rehearsed disable/restore procedure. A successful hackathon demo is not pilot authorization.

### Competitive bar

The goal is not to promise a prize. The project should make a judge able to verify, in one short demo, (1) the specific user pain, (2) the work the agent completed, (3) a consequential action it correctly refused or escalated, (4) the evidence behind the result, and (5) the measured limits. Prefer one polished complete workflow over a broad feature list. For every public number, a test must regenerate the figure from named data and report its denominator.

## Portfolio-level acceptance

- [ ] Exactly ten standalone project specs exist and each names a distinct new/adjacent target problem.
- [ ] Every spec includes explicit non-goals, actors, workflow/state, agent boundary, domain policy, testable acceptance criteria, negative cases, evals, demo, and implementation slices.
- [ ] No spec describes an implemented Harness-Zero feature or claims project impact before validation.
- [ ] No spec requires live provider spend, real tenant/student/community data, or external contact by default.
- [ ] The ten specs do not depend on copying winning-project source, assets, text, or private implementation details.

**Implementation status:** none of the ten projects is built by this documentation change. A specification passing review is not a test pass, deployment, pilot, or award guarantee.
