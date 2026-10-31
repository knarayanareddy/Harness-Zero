# Grafana 2nd alternate — SportsCaption SLO: reliability for live accessibility tracks

**Status:** Draft; standalone project proposal. **Reference:** [Raccord](https://devpost.com/software/raccord-accessible-experience-reliability-for-live-media)[6], Grafana second place.
**New audience:** A small live-sports streaming operator responsible for captions and alternate audio.

## Problem hypothesis

The stream can be “up” while captions lag, alternate audio is silent, or a player’s accessibility controls fail on a subset of devices. The hypothesis is that measuring accessibility as an owned service objective, tied to player/device/locale evidence, improves incident response. Validate with an accessibility specialist and streaming operator. Never infer a viewer’s disability or treat an SLO as certification.

## Product behavior

Collect test-probe results and delivery telemetry for a synthetic live event. Track caption delay/readability, audio-description presence, track discoverability, player keyboard operation, and locale/device coverage. The agent correlates signals and proposes likely causes; a human incident commander approves any mitigation. After mitigation, the system reruns checks, including adjacent locales/devices, before closing.

## Required Grafana integration

Use Grafana MCP at runtime for metrics, logs, traces, alerts, dashboard search, and an approved incident annotation. A required-evidence precondition blocks analysis completion if required Grafana MCP calls did not return valid source data. Do not query Prometheus/Loki/Tempo directly in place of the MCP requirement. Google ADK/Gemini is the only AI path.

## Acceptance criteria

- **SCS-01 Measurement scope:** every result has locale, device, player build, stream version, probe time, and evidence; missing probe is `not measured`.
- **SCS-02 SLO semantics:** objective, window, budget, and threshold are versioned and shown; never display “certified accessible” from a model judgment.
- **SCS-03 Incident correlation:** correlate by event/time/track/device and show evidence for each hypothesis; model cannot mutate the underlying metric.
- **SCS-04 Partner usage:** live Grafana MCP query plus an approved safe annotation appear in the trace; missing MCP capability prevents a green completion state.
- **SCS-05 Repair authority:** proposed mitigation is a typed plan; no auto-switching tracks, suppressing alerts, or closing incident without operator permission and post-change probes.
- **SCS-06 Regression protection:** after a fix, run both the affected slice and unaffected control locales/devices; any regression keeps incident open.
- **SCS-07 Accessibility:** the console itself meets keyboard, screen-reader, contrast, captions, and zoom checks; status is never communicated by color alone.
- **SCS-08 Adversarial tests:** prompt injection in log lines/caption text cannot change tools or policy; Grafana errors and partial results fail closed.
- **SCS-09 Eval:** deterministic probes test drift, silence, absent track, player menu trap, and good controls. Report recall/false alarms by dimension, and all misses.
- **SCS-10 Demo:** show a synthetic caption lag across one device cohort, inspect Grafana evidence, approve a simulated remediation, verify adjacent control cohort, then close with evidence.

## Delivery

Start with one synthetic live event and two device classes. Implement probe schema and SLO engine, then the MCP connection, then correlation/incident workflow, then approval and re-measurement. Open questions: partner-specific MCP auth, probe ownership, on-call routing, and accessible-player test devices.
