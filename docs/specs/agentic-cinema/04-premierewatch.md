# Grafana 1st alternate — PremiereWatch: release-pipeline risk and delivery observability

**Status:** Draft; standalone project proposal. **Reference:** [The Media Customs](https://devpost.com/software/the-media-customs)[5], Grafana first place.
**New audience:** A distributor’s release-operations team coordinating localized media packages for a festival premiere.

## Problem hypothesis

A premiere may depend on many localized versions, captions, audio tracks, rights records, and delivery vendors. A package can be technically present but wrong, incomplete, or stale. Validate with one festival/distributor operator; this spec does not assert market-specific legal rules or that a dashboard guarantees compliance.

PremiereWatch correlates package readiness, validation events, caption/audio QC results, and delivery acknowledgements by title/territory/platform. It identifies blockers, cites the artifact or check that produced them, and creates a human-owned incident when deadlines are threatened. It does not rewrite video, clear rights, or mark a title released automatically.

## Grafana is a required runtime dependency

The active agent uses the accepted Grafana Cloud MCP server to query alerts, logs, metrics, traces, and dashboards. All operational evidence shown in the case file records the MCP tool and query/result identity. No direct Prometheus/Loki/Grafana API bypass may satisfy this requirement. The agent may create a dashboard/annotation only through an allowlisted MCP tool after operator approval; the app’s deterministic adapter validates scope and rate limits.

Google ADK/Gemini handles incident summarization and evidence-grounded hypotheses. Deterministic code owns package completeness, required-track matrix, expiry, severity thresholds, and release state. A human release manager authorizes any “ready to ship” transition.

## Acceptance criteria

- **PW-01 Partner proof:** live demo connects to Grafana MCP and executes at least one real query plus one safe write/annotation; dashboard screenshots alone do not count.
- **PW-02 Evidence contract:** every blocker names a title, territory, platform, artifact/check ID, time, and source; no source means `unknown`, not pass.
- **PW-03 Staleness:** metrics/logs past TTL suppress green/all-clear state and create a stale-source warning.
- **PW-04 No auto-release:** agent cannot mark release complete or bypass a required artifact; only authorized human action may publish the release state.
- **PW-05 Scope:** one demo title, three territories, two platforms, and versioned package requirements are enough; one changed artifact invalidates prior readiness.
- **PW-06 Injection:** content/metadata in an uploaded package cannot change query scopes, credentials, dashboards, or release policy.
- **PW-07 Error boundaries:** Grafana MCP outage, partial query, malformed panel result, or denied write produces a visible incomplete run with trace ID; no stale result is silently reused.
- **PW-08 Cost/permissions:** read tools are default; dashboard mutations and any model calls have budgets, quotas, and a kill switch; secrets are server-side only.
- **PW-09 Eval:** synthetic release runs cover missing captions, wrong version, partial upload, delayed acknowledgment, Grafana outage, and false-green data. Report detection and false alarms separately.
- **PW-10 Demo:** ingest → query Grafana evidence → show blocker and cited source → approval-required annotation → recheck after fixture fix. No copyrighted media in public demo.

## Slices and questions

Schema/package matrix → synthetic telemetry and Grafana MCP connector → deterministic readiness engine → Gemini incident brief → approval-bound annotations → end-to-end demo and runbook. Open questions: target festival workflow, acceptable evidence TTLs, and who owns the final greenlight.
