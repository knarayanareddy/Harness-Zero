# Agentic Cinema — 15 alternate project specifications

**Status:** Draft portfolio. These are proposals, not implemented products.
**Date:** 2026-10-23
**Scope:** One new concept per official prize placement: 5 partner tracks × 1st/2nd/3rd place.

## Scope and evidence

The official rules define 15 prize placements across IBM, Grafana Labs, Parallel, ClickHouse, and Replit.[1]

Each specification is a new adjacent use case, not a clone. No winner code, assets, private data, or prose are reused. Project descriptions are self-reported and treated as design references, not independently verified results.

- The fetched THE GATE and Retention Room pages did not expose usable repository links.[11][12]
- The fetched wevr and Project Greenlight pages did not expose usable repository links.[14][16]

The event's submission period ended on September 9, 2026.[1] This pack is a blueprint for future independent builds, not an entry to that closed contest.

**Source-review boundary:** I retrieved shallow snapshots and repository metadata/file inventories for 11 linked repositories, but did not run their tests, inspect every implementation path, or exercise their live services. The specs are therefore based on public project descriptions and official contest requirements—not a claim that every submitted implementation was independently verified.

| Placement | Reference winner | New specification |
|---|---|---|
| IBM 1st | [Eyeline](https://devpost.com/software/eyeline-autonomous-on-set-continuity-copilot)[2] · [repo](https://github.com/helenkwok/eyeline) | [StageLine](01-stageline.md): continuity ledger for live theatre and touring productions |
| IBM 2nd | [Shadow Cut](https://devpost.com/software/shadow-cut)[3] · [repo](https://github.com/patriceai-code/shadow-cut) | [SecondUnit Match](02-secondunit-match.md): cross-unit shot-match review for episodic production |
| IBM 3rd | [HOLD](https://devpost.com/software/hold-wrp4nq)[4] · [repo](https://github.com/StephenSook/hold) | [CrewClock](03-crewclock.md): schedule feasibility and crew-rest planning |
| Grafana 1st | [The Media Customs](https://devpost.com/software/the-media-customs)[5] · [repo](https://github.com/tdries/devpost-hackathon-agentic-cinema) | [PremiereWatch](04-premierewatch.md): release-pipeline compliance and delivery observability |
| Grafana 2nd | [Raccord](https://devpost.com/software/raccord-accessible-experience-reliability-for-live-media)[6] · [repo](https://github.com/Marc-Dvci/Raccord) | [SportsCaption SLO](05-sportscaption-slo.md): accessibility reliability for live sports |
| Grafana 3rd | [Shot Clock](https://devpost.com/software/shot-clock)[7] · [repo](https://github.com/biyonjose10/shot-clock) | [MixWatch](06-mixwatch.md): audio-post render incident investigation |
| Parallel 1st | [CurrentCut](https://devpost.com/software/currentcut)[8] · [repo](https://github.com/tanaka-cell/currentcut) | [Fieldnote Desk](07-fieldnote-desk.md): evidence-grounded radio/documentary production desk |
| Parallel 2nd | [Consentinel](https://devpost.com/software/consentinel)[9] · [repo](https://github.com/PRACHIT27/Consentinel) | [Rights Ledger](08-rights-ledger.md): rights-scope evidence for archival footage and music |
| Parallel 3rd | [The Boxes](https://devpost.com/software/the-boxes)[10] · [repo](https://github.com/rapha18th/agentic-cinema-boxes) | [Archive Dossier](09-archive-dossier.md): source-grounded research for documentary episodes |
| ClickHouse 1st | [THE GATE](https://devpost.com/software/the-gate-yx5z2b)[11] · repo link unavailable | [RunOfShow](10-runofshow.md): live-event cue coverage and recovery planning |
| ClickHouse 2nd | [Retention Room](https://devpost.com/software/retention-room)[12] · repo link unavailable | [Podcast Pulse](11-podcast-pulse.md): evidence-based audience-retention diagnosis |
| ClickHouse 3rd | [Locaish](https://devpost.com/software/locaish)[13] · [repo](https://github.com/JoshuaA1292/Locaish) | [AssetFinder](12-assetfinder.md): queryable, rights-aware reuse of a studio’s existing footage |
| Replit 1st | [wevr](https://devpost.com/software/wevr-01crv6)[14] · repo link unavailable in fetched page | [MuseumWorld](13-museumworld.md): authorable, stateful interactive exhibition stories |
| Replit 2nd | [FrameKind](https://devpost.com/software/framekind-tz85od)[15] · [repo](https://github.com/Abhinav0905/Google_All_Agentic_Cinema) | [CaptionProof](14-captionproof.md): human-reviewed caption QA for event recordings |
| Replit 3rd | [Project Greenlight](https://devpost.com/software/uniq-jn5e4r)[16] · repo link unavailable | [ScriptBudget](15-scriptbudget.md): grounded production-feasibility planning without box-office prediction |

## Non-negotiable contest constraints

The official rules require a functioning, publicly hosted project using Google Cloud/Gemini and the chosen partner’s required product; the allowed AI tools are restricted to Google Cloud AI plus the partner’s built-in AI.[1] Do not use OpenAI, Anthropic, AWS AI, another agent framework, or an unapproved third-party model. The track integrations are not decorative: Grafana must be used through its accepted MCP server at runtime; Parallel Search must be called at runtime; ClickHouse must be queried through `mcp-clickhouse`; Replit entries must be built with Replit Agent and deployed on Replit; IBM entries must document IBM Bob as part of development. Verify exact partner acceptance against the current official rules/resources before coding; if a requirement cannot be verified, stop and resolve it rather than guessing.

Official rules: [1]. Partner documentation and the exact current SDK/tool behavior must be independently checked before implementation.

## Shared implementation and verification contract

Use the project-specific spec plus this guide. Start in a separate project repository; do not add these projects to Harness-Zero. Pin Python/Node/Google SDK/partner SDK versions and generate OpenAPI 3.1 from the API implementation. Provide a default scripted/offline path that exercises the real agent/tool/policy path, but keep the required real Google Cloud and partner integration wired and demonstrable at runtime. No paid live call, real data, or external write is enabled in ordinary tests.

Required commands in every project: `make setup`, `make verify`, `make demo-e2e`, `make eval`, and opt-in `make live-smoke`. `make verify` must fail on collection/setup errors; `make demo-e2e` uses the app’s actual public workflow and a sandbox adapter; `make eval` regenerates a versioned report including misses and denominator; `make live-smoke` requires an explicit flag and hard cost cap. An acceptance matrix maps each criterion to files, executable/manual check, and retained evidence.

Shared authority rules: models may interpret and propose; deterministic code controls identities, entitlements, calculations, data lineage, policy versions, state transitions, idempotency, spend and side effects. Unknown state, stale approval, missing evidence, malformed output, timeout, and provider failure must fail closed into a visible resumable state. Every external write is allowlisted, content-hash-bound to the user approval, idempotent, and read back. No project claims safety, legal correctness, or impact from a finite synthetic test set.

Shared demo requirements: maximum 3 minutes, show the real app, include one successful workflow and one consequential refusal/uncertain result, display the evidence/tool trace, and label simulated/synthetic/live components. No copyrighted film or private person’s likeness/audio in demo assets; use original or explicitly licensed material. A spec passing review is not an implementation, a live integration, or a prize guarantee.

## Sources

[1] https://agentic-cinema.devpost.com/rules
[2] https://devpost.com/software/eyeline-autonomous-on-set-continuity-copilot
[3] https://devpost.com/software/shadow-cut
[4] https://devpost.com/software/hold-wrp4nq
[5] https://devpost.com/software/the-media-customs
[6] https://devpost.com/software/raccord-accessible-experience-reliability-for-live-media
[7] https://devpost.com/software/shot-clock
[8] https://devpost.com/software/currentcut
[9] https://devpost.com/software/consentinel
[10] https://devpost.com/software/the-boxes
[11] https://devpost.com/software/the-gate-yx5z2b
[12] https://devpost.com/software/retention-room
[13] https://devpost.com/software/locaish
[14] https://devpost.com/software/wevr-01crv6
[15] https://devpost.com/software/framekind-tz85od
[16] https://devpost.com/software/uniq-jn5e4r
