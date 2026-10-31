# IBM 1st alternate — StageLine: continuity ledger for live theatre

**Status:** Draft; standalone project proposal. **Reference:** [Eyeline](https://devpost.com/software/eyeline-autonomous-on-set-continuity-copilot)[2], IBM first place.
**New audience:** Stage managers and prop/costume leads for a small touring theatre production.

## Problem hypothesis and outcome

After a rehearsal or performance, a stage manager must reconcile prop state, costume state, blocking marks, and cue changes across scenes and venues. A missed change can break a later performance, but a general-purpose video model may mistake artistic variation for a continuity error. Validate with two stage managers and one touring company; do not assert reduced incident rates before a pilot.

StageLine turns approved rehearsal photos/video snippets and stage-manager notes into a scene-by-scene continuity ledger. It flags candidate mismatches, points to the exact moments and prior reference, and lets the stage manager accept, dismiss, or update the authoritative note. It never stops a performance or changes a cue automatically.

## V1 workflow and records

Create a production, import a scene/cue list, define a named reference take, and upload short authorized rehearsal clips. A classical frame/timecode pass proposes candidate differences. Gemini on Vertex AI compares only the bounded image crops and structured scene context, returning `match`, `candidate_difference`, or `uncertain` with evidence timestamps. The stage manager resolves each candidate. The next rehearsal compares against the resolved ledger, not the model’s prior narrative.

Core records: `Production`, `Scene`, `Cue`, `ReferenceMoment`, `MediaAsset(hash, license, consent)`, `DifferenceCandidate(before_ref, after_ref, timecodes, crops)`, `HumanDisposition`, and append-only `AuditEvent`. Cast/crew names are optional; default IDs are role labels. Originals are immutable; annotated derivatives retain source hashes.

## Partner, agent, and boundary design

Use Gemini/Google Cloud for multimodal candidate adjudication and a typed Google ADK workflow. IBM Bob is used during development and its allowed provenance artifact is retained with the project. If current IBM track rules require a runtime IBM product beyond Bob, resolve that from the official track resource before implementation; do not invent a partner endpoint. No non-Google AI detector or generative video repair.

The agent may explain a candidate and draft a note. It cannot create or dismiss a continuity fact, modify cue sheets, identify a person, or notify a cast member. A stage manager’s decision is the only authoritative mutation. Uncertain or missing media produces `NOT_ASSESSED`, not “continuous.”

## Acceptance criteria

- **SL-01 Media rights:** reject missing license/consent metadata; test unauthorized file and expired consent.
- **SL-02 Grounding:** each candidate names both media hashes, timecodes, scene/cue IDs, and crops; no candidate without verifiable references reaches the review queue.
- **SL-03 Conservative comparison:** intentional lighting/camera changes, absent frame, blur, and no-reference cases are labeled uncertain or dismissed by a human, never auto-recorded as a continuity error.
- **SL-04 State authority:** model output cannot create, update, or dismiss ledger facts; all mutations require authenticated stage-manager action and produce audit events.
- **SL-05 Injection:** instructions embedded in slate text, captions, or uploaded filenames cannot expand tool access or leak other productions.
- **SL-06 Reproducibility:** same media hashes, cue data, and algorithm version produce deterministic candidate regions; Gemini verdict variance is separately measured.
- **SL-07 Eval:** versioned dataset includes real authorized rehearsal footage and synthetic controls; report candidate recall/precision, false alarms by category, abstentions, and human overturn rate. No unlicensed commercial footage.
- **SL-08 Demo:** compare two authorized rehearsal references; show a genuine candidate, an intentional-change control, the human disposition, and the updated continuity card.
- **SL-09 Runtime partner proof:** demo and CI evidence show Google AI runtime and IBM Bob development provenance as required by current rules; if the partner gate is not met, mark the project unqualified.

## Build slices and rollout

1. Scene/cue schema, media licensing gate, and immutable asset hashes.
2. Deterministic frame alignment/difference extraction and replayable fixtures.
3. Gemini/ADK candidate adjudication with evidence schema and scripted model.
4. Human review ledger, search, export, and audit.
5. Evaluation, 3-minute demonstration, accessible UI, cloud deployment, and rollback.

First deployment is a private, synthetic production. A real troupe pilot requires written consent from the production and media participants, retention/deletion policy, and a human owner for every flagged event.

## Open questions

- Which task catalog and reference-shot conventions will the first troupe approve?
- What consent, retention, and access controls apply to rehearsal footage and cast likenesses?
- Who receives and resolves instructor/stage-manager review tasks during a live production?
