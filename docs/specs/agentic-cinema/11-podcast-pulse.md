# ClickHouse 2nd alternate — Podcast Pulse: evidence-based retention diagnosis

**Status:** Draft; standalone project proposal. **Reference:** [Retention Room](https://devpost.com/software/retention-room)[12], ClickHouse second place.
**New audience:** Podcast editors reviewing episode completion, rewinds, and playback failures.

## Problem hypothesis

Podcast teams receive aggregate completion curves but cannot tell whether a drop reflects story structure, an ad marker, an audio defect, or a delivery failure. The hypothesis is that a queryable event model plus transcript segments can guide editorial review without turning correlation into causation. Validate with an editor and analytics owner.

## Product and data design

Ingest synthetic or licensed playback events (`start`, `heartbeat`, `seek`, `rebuffer`, `exit`) and a versioned transcript/segment map. ClickHouse materialized views compute completion, rewind rate, and rebuffers by segment, device, region, and CDN. Gemini via Google ADK can ask a fixed question sequence: locate anomaly → segment-join → check delivery signals → retrieve the transcript excerpt → write a cited hypothesis and alternative explanations. The editor decides what to change.

The data model separates viewer aggregate metrics from transcript facts. No identity-level histories, demographic inference, or personalized recommendations. The agent’s ClickHouse access is read-only through the official MCP server; the source query and result summary are shown beside every recommendation.

## Acceptance criteria

- **PP-01 Runtime ClickHouse:** active `mcp-clickhouse` queries execute against the demo cluster; static JSON cannot masquerade as the live path.
- **PP-02 Deterministic metrics:** tests reconcile counts, denominators, window boundaries, and segment joins against hand-calculated fixtures.
- **PP-03 Confounder check:** before attributing a drop to content, workflow checks rebuffers, ad markers, and missing telemetry; absent dimensions are explicitly unknown.
- **PP-04 Causal humility:** recommendations are framed as hypotheses with supporting and disconfirming evidence; no claim that a scene caused an exit from correlation alone.
- **PP-05 Query safety:** read-only DB role, query timeout/row cap, no arbitrary mutations, and injection tests in transcript segments and metadata.
- **PP-06 Privacy:** aggregate events only; no raw IP, account ID, or re-identifiable row in agent prompts, logs, or screenshots.
- **PP-07 Eval:** synthetic scenarios include story cliffhanger, long exposition, ad insertion, CDN rebuffer, missing event data, and no anomaly. Report diagnosis accuracy and abstention, not only top-1 accuracy.
- **PP-08 Demo:** show one synthetic editorial drop, rule out a delivery issue using ClickHouse evidence, show an alternative explanation, and export a review note with exact segment/time range.
- **PP-09 Partner docs:** schema, generated OpenAPI, ClickHouse DDL, MCP configuration, and local seeded dataset are included in a fresh-clone runbook.

## Build slices and open questions

Typed event schema and fixture calculator → ClickHouse tables/materialized views → MCP query layer → ADK analysis workflow → editor note UI → eval and hosted demo. Open questions: consent/aggregation thresholds, minimum cohort sizes, and an editor’s accepted timecode convention.
