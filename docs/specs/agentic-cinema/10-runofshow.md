# ClickHouse 1st alternate — RunOfShow: cue coverage and live-event recovery planning

**Status:** Draft; standalone project proposal. **Reference:** [THE GATE](https://devpost.com/software/the-gate-yx5z2b)[11], ClickHouse first place.
**New audience:** A small live-event producer coordinating stage cues, camera coverage, and segment deadlines.

## Problem hypothesis

When a live event loses time, producers must decide which cues or shots to protect, defer, or cut. The hypothesis is that historical execution data plus a coverage matrix can expose feasible options faster than memory. Validate with one event producer; this tool is a planning aid, not a live-show switcher.

## Data and decision flow

Import a versioned run-of-show, cue dependencies, required camera/graphics/audio coverage, crew/room constraints, and historical cue durations. ClickHouse stores event samples and aggregate quantiles. An MCP-connected Gemini/ADK agent queries only allowlisted read operations, explains bottlenecks, and proposes a changed schedule. A deterministic optimizer computes feasibility and tradeoffs. A stage manager selects an option; publishing/export requires approval.

Every estimated duration names sample count, cohort, quantile, data window, and fallback. Empty or out-of-distribution history must be an explicit uncertainty, not a fabricated zero. The optimizer reports feasibility/optimality status and constraint IDs.

## ClickHouse requirement

The runtime uses the official `mcp-clickhouse` server to query an actual ClickHouse cluster. Partner proof includes an MCP call ID, SQL/tool name, result row count, latency, and sanitized query; direct SQL in the agent or a static fixture is not counted as live ClickHouse use. Writes to ClickHouse are not exposed to the model.

## Acceptance criteria

- **ROS-01 Import:** schema validates cue IDs, dependencies, durations, time zones, and media coverage; invalid graphs are held for producer correction.
- **ROS-02 Coverage:** each cue requirement is satisfied, explicitly waived by a human, or listed as uncovered; agent cannot invent required coverage.
- **ROS-03 Solver honesty:** deterministic engine returns `FEASIBLE`, `OPTIMAL`, `INFEASIBLE`, or `UNKNOWN`; only proven states are labeled as such.
- **ROS-04 Estimate quality:** ClickHouse-derived duration estimates expose population and quantile; insufficient data prevents a numeric confidence claim.
- **ROS-05 Source separation:** model explanation never supplies cost/time figures; code recomputes totals from cited rows and rule versions.
- **ROS-06 Approval:** a schedule change is bound to exact cue graph, data snapshot, and export target; any change invalidates approval.
- **ROS-07 MCP proof:** tests and demo verify actual ClickHouse MCP query execution and result parsing; malformed/empty result sets remain distinct.
- **ROS-08 Injection:** cue names/comments and query results cannot make the agent run arbitrary SQL or mutate a plan.
- **ROS-09 Eval:** synthetic past events include dropped cues, slow segments, resource conflicts, no-history cohorts, and reordering controls. Report plan feasibility and historical estimate error.
- **ROS-10 Demo:** show original coverage gaps, a solver-produced alternative, the quantified tradeoff with provenance, and a producer-approved export; never control a live show.

## Slices / open questions

Cue schema and graph validator → synthetic ClickHouse dataset and MCP read path → deterministic optimizer → ADK explanation/proposal → approval/export → evaluator, deployment, and demo. Open questions: first event type, partner-provided cluster, historical data rights, and who can waive a cue.
