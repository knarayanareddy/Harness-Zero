# IBM 2nd alternate — SecondUnit Match: match pickup footage to the approved scene

**Status:** Draft; standalone project proposal. **Reference:** [Shadow Cut](https://devpost.com/software/shadow-cut)[3], IBM second place.
**New audience:** Assistant editors reconciling second-unit pickups with an approved principal-unit scene.

## Problem hypothesis

Pickup footage is often captured days later, with different crews, camera settings, or available props. Editors need to know whether a pickup can intercut before the location and crew disappear. Validate with an assistant editor and a small production team; no reshoot-cost statistic is assumed.

SecondUnit Match takes an approved reference clip, a pickup clip, slate metadata, and the scene’s continuity notes. It finds candidate discrepancies in props, wardrobe, lighting, screen direction, framing, and sound; then presents a side-by-side evidence card for editor review. It does not synthesize substitute footage, modify media, or declare a shot “usable” on its own.

## Workflow and records

Create a scene reference → add pickup slate and clip → deterministic media probe checks codec, frame rate, duration, audio presence, and timestamps → local/classical image processing proposes aligned candidate crops → Gemini on Vertex AI adjudicates each crop with scene context → editor marks `acceptable`, `fix_in_edit`, `reshoot_consideration`, or `uncertain` → export an EDL/CSV note with source timecodes.

Store immutable clip hashes, the exact frame-sampling plan, candidate crops, model/version, response, editor verdict, and export hash. Keep each edit as a separate derived artifact; never overwrite source footage.

## IBM and Google integration contract

Use Google ADK and Gemini for the active review workflow; classical CV is permitted but no other AI model/detector. Use IBM Bob during development and preserve the required provenance evidence. Confluent is optional; add it only if the project can demonstrate replayable event ingestion and a clear operational benefit. Do not claim a runtime IBM integration unless the track’s official requirements verify one.

The model has no edit/render tools. Deterministic code validates timecodes, selected media IDs, frame bounds, file rights, and export format. A human editor approves every export. Model uncertainty must remain visible; confidence scores alone cannot make a clip usable.

## Acceptance criteria

- **SUM-01 Media boundary:** file size, MIME, codec, duration, and license/consent are validated; malformed and unsupported media fail with a clear state.
- **SUM-02 Provenance:** every mismatch card links to exact reference and pickup frames/timecodes and hashes; absent evidence yields `uncertain`.
- **SUM-03 Alignment controls:** camera motion, color grade, crop, and exposure changes are included as negative controls; report false-alarm rate per category.
- **SUM-04 No self-approval:** model cannot mark a take usable, alter scene notes, or publish an EDL; editor action required and logged.
- **SUM-05 Injected slate:** malicious text in slates/transcripts cannot issue tools, retrieve other productions, or change verdict policy.
- **SUM-06 Repeatability:** identical inputs reproduce the same extracted frame pairs; model adjudication record is versioned and replayable.
- **SUM-07 Export correctness:** generated EDL references valid source IDs and in-range timecodes; import/reparse round-trip test succeeds.
- **SUM-08 Eval:** at least 40 labeled reference/pickup pairs across true mismatches and artistic variations; report recall, precision, abstention, and editor overrides. Retain all misses.
- **SUM-09 Runtime gate:** demo proves active Gemini/ADK execution and IBM Bob provenance; credentials/cost must be capped and not required by default verification.
- **SUM-10 Demo:** show one defect caught, one artistic difference not overcalled, one `uncertain` case, and an editor-approved EDL.

## Slices

Media ingest/rights → deterministic alignment → Gemini review on evidence crops → editor disposition/ledger → safe export → eval and cloud demo. Do not add Veo or another generative model; it is out of contest AI scope unless explicitly allowed by official rules.

**Open questions:** production media permissions, editor’s accepted EDL flavor, the production’s reference hierarchy, and IBM track evidence format.
