# 08 — ColdWatch: opt-in neighborhood check-ins during dangerous cold

**Status:** Draft specification; no implementation exists.
**Inspired quality pattern:** An official alert triggers bounded outreach; humans handle meaningful exceptions.
**Target:** One neighborhood organization with an opt-in roster and named incident captain.

## Problem and hypothesis

During a severe-cold event, a small volunteer team may not know who has been reached, who needs supplies, or which requests still need a person. The hypothesis is that a queue and consent-aware check-in workflow can help volunteers coordinate without broadcasting resident details. Validate with a neighborhood organization and residents before any real deployment. No claim that an agent prevents injury or replaces emergency response.

## Product promise and boundaries

ColdWatch monitors a configured official alert feed, proposes a check-in plan for an opted-in group, records each response, and asks a human captain to decide urgent or unresolved cases. MVP runs a scripted drill against synthetic residents and a fixture alert. Live outbound calls/messages are disabled by default.

Never infer consent from roster presence. Never contact emergency services, promise a volunteer is on the way, provide medical advice, share resident data with a group, or treat no answer as “safe.” The captain owns activation, escalation, and volunteer assignment.

## Workflow and state

`ALERT_RECEIVED → PROFILE_GATE → CAPTAIN_ACTIVATION → OUTREACH_QUEUED → CONTACT_ATTEMPTED → RESPONSE_CLASSIFIED → (OK: FOLLOWUP_SCHEDULED | NEED: CAPTAIN_DECISION | URGENT/UNCLEAR/NO_ANSWER: CAPTAIN_DECISION) → VOLUNTEER_TASK → RESIDENT_CONFIRMATION → CLOSED`.

Each resident has an explicit contact consent, language/accessibility preferences, approved channel/window, and revocation state. Each attempt has a correlation ID, rate-limit bucket, transcript-retention policy, and final classification. Repeated attempts are bounded and state-machine controlled.

## Model and safety boundary

Official alert parsing and activation thresholds are deterministic and versioned. The model may conduct a short scripted check-in and propose a structured classification. Deterministic rules can only raise severity based on explicit danger phrases, protocol failure, or unresolved answer; the model cannot lower a deterministic escalation. A human captain handles all urgent, ambiguous, no-answer, or resource-allocation decisions. Outbound action is an allowlisted channel adapter with Cedar-like policy checks; tests must invoke the actual guard.

## Acceptance criteria

- **CW-01 Consent:** no contact without current resident consent, allowed local time, valid channel, and active incident; test each condition independently and in combination.
- **CW-02 Activation:** an unknown alert type or profile event cannot activate an incident; captain approval is bound to the alert, roster version, and contact plan.
- **CW-03 No-answer semantics:** no answer/voicemail/ambiguous response never becomes `OK`; retry cap and explicit captain task are deterministic.
- **CW-04 Severity monotonicity:** deterministic red-flag backstop may raise but never lower model severity; missed phrases and false alarms are separately counted.
- **CW-05 No false promises:** outbound text/voice cannot promise an arrival time or claim a volunteer/emergency service was contacted unless a verified event proves it.
- **CW-06 Privacy:** roster data is per-role; public/group messages contain no resident name, address, health condition, or sensitive response. Logs redact transcript content by default.
- **CW-07 Idempotency/resume:** duplicate alert/webhook or restart cannot double-call, double-assign, lose an acknowledged decision, or close a task without response evidence.
- **CW-08 Spend/channel caps:** test and live adapters have separate hard caps, kill switches, rate limits, and visible cost counters. Live mode requires an explicit environment flag plus partner configuration.
- **CW-09 Eval:** use synthetic residents, scripted adversarial content, nonresponse, language variants, understated red flags, and benign controls. Report recall and precision with misses; no safety claim from a simulator alone.
- **CW-10 Demo:** scripted drill shows alert, consent-filtered queue, a no-answer held for captain, one approved volunteer task, and the append-only trace. No real calls/messages occur.
- **CW-11 Manual operations:** captain can pause the incident, suppress a resident, correct a classification, and export a post-incident report without deleting history.

## Evaluation and slices

The fixture corpus must include explicit urgent, hidden/understated, benign, adversarial, multilingual, no-answer, and consent-revoked cases. Run each urgent persona repeatedly; report confidence intervals or trial counts, not just one pass. Preserve every miss in the report.

Slices: alert/profile contract → consent roster + deterministic risk and queue → scripted Strands dialogue/classifier → approval/volunteer state machine → red-team/eval → responsive captain console, deployment, and demo.

**Open questions:** local alert authority, partner-defined contact windows, language coverage, transcript deletion period, and volunteer liability policy. Resolve before real outreach.
