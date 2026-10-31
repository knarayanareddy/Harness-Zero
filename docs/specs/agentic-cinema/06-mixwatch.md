# Grafana 3rd alternate — MixWatch: audio-post render incident investigation

**Status:** Draft; standalone project proposal. **Reference:** [Shot Clock](https://devpost.com/software/shot-clock)[7], Grafana third place.
**New audience:** Audio-post supervisors coordinating dialogue, Foley, music, and deliverable renders.

## Problem hypothesis

Audio renders can finish successfully while containing silence, clipping, missing stems, or a channel-layout error. A render farm dashboard alone cannot tell whether the delivered audio is perceptually wrong; a listening check alone cannot correlate the issue to a machine or pipeline event. Validate the workflow with an audio-post supervisor; the demo does not claim to replace a mastering engineer.

MixWatch joins render metrics/logs/traces with a bounded audio sample and a versioned deliverable spec. It identifies the affected job, separates infrastructure evidence from content-quality evidence, estimates schedule impact using deterministic code, and creates a human-approved incident annotation. It never automatically overwrites, requeues, or publishes a master.

## Architecture and authority

Google ADK agents use Gemini on Vertex AI. A `RenderInvestigator` can query Grafana MCP for job/worker/queue metrics, logs, traces, and dashboards. A `SampleReviewer` receives only authorized excerpts and produces structured observations (silence interval, clipping candidate, channel count) with time ranges. Deterministic code computes durations, peak/LUFS checks, due-time projections, and policy outcomes. A human engineer approves retry or artifact replacement.

The official Grafana MCP connection is mandatory and load-bearing; do not substitute direct Prometheus/Loki queries. The default demo uses synthetic renders and a local Grafana fixture; a live demo adapter queries a real, isolated Grafana workspace. All third-party audio is original or licensed.

## Acceptance criteria

- **MW-01 Source separation:** infrastructure and content findings remain distinct; a successful render job is not equated with a valid audio master.
- **MW-02 Partner runtime:** actual Grafana MCP queries for metrics/logs/traces are visible in the trace; a simulated MCP response cannot be presented as live.
- **MW-03 Deterministic facts:** duration, timestamp, channel count, peak, configured threshold, and schedule projection are recomputed in code from typed inputs.
- **MW-04 Uncertain listening result:** model cannot create a false “audio is clean” result from silence, missing samples, unsupported codec, or timeout; state becomes `NOT_ASSESSED`.
- **MW-05 No automatic media mutation:** model tools cannot replace, delete, requeue, or publish a master. Retry requires a human preview of exact job, output, and effect.
- **MW-06 Injection:** instructions embedded in filenames, embedded metadata, or waveform-transcription text cannot expand MCP access or authorize a job change.
- **MW-07 Schedule simulation:** projected completion is labeled an estimate with input distribution/version and is tested against deterministic fixtures; no probability is invented from an empty history.
- **MW-08 Idempotency and recovery:** duplicate completion events do not create duplicate incidents; restart preserves pending approval and evidence hashes.
- **MW-09 Eval:** synthetic set includes clean, silent, clipped, missing-channel, wrong-stem, slow-worker, queue-backlog, and false-alarm controls. Report false alarms, missed defects, uncertainty, and run latency.
- **MW-10 Demo:** show one infrastructure failure, one content-quality candidate, a clean control, and a human-approved sandbox retry; confirm artifact read-back after retry.

## Slices / open questions

Metrics schema and synthetic event generator → Grafana MCP adapter → deterministic incident/cost kernel → Gemini structured analysis → approval-bound sandbox action → eval, accessibility, and hosted demo. Open questions: accepted loudness spec, audio-retention policy, approved sample window, and production source integration.
