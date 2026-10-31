# Parallel 1st alternate — Fieldnote Desk: source-grounded radio documentary edit desk

**Status:** Draft; standalone project proposal. **Reference:** [CurrentCut](https://devpost.com/software/currentcut)[8], Parallel first place.
**New audience:** A small public-radio or oral-history team preparing a short factual audio feature.

## Problem hypothesis

Producers must turn interviews and field recordings into a short script while checking names, dates, quantities, and public claims. Confidential remarks and embargoed material create a second risk: they must not leak into external search. Validate with one producer/editor before claiming faster turnaround.

Fieldnote Desk accepts authorized audio/transcripts, segments speakers and claims, searches only approved public claims through Parallel Search, and produces a source-linked script draft plus an editor’s cue sheet. It highlights unsourced claims and restricted segments instead of filling the gap. It does not publish or edit the source audio without approval.

## Workflow and records

`UPLOAD → SPEAKER/SEGMENT REVIEW → CONFIDENTIALITY LABEL → CLAIM EXTRACTION → EGRESS GATE → PARALLEL SEARCH → EVIDENCE REVIEW → SCRIPT DRAFT → EDITOR APPROVAL → EXPORT`.

Every segment has a timestamp, transcript text, speaker alias, consent/embargo status, retention policy, and label (`PUBLIC`, `EDITORIAL_ONLY`, `CONFIDENTIAL`, `OFF_RECORD`, `PERSONAL_DATA`, `NEEDS_HUMAN_REVIEW`). Claims have a self-contained subject/attribute/value, source evidence, freshness, and exact status. A deterministic egress gateway refuses restricted labels and raw transcript text; only approved, minimal queries may reach Parallel Search.

## AI/partner boundary

Google ADK + Gemini perform transcription review, claim decomposition, and script drafting. Parallel Search API is called at runtime for public claims. Deterministic code decides whether a segment may leave the system, whether a source supports a claim, and whether an export is approved. A model cannot mark a claim verified or release a confidential segment. No OpenAI/Anthropic/other AI services.

## Acceptance criteria

- **FD-01 Confidentiality first:** label gate runs before search or embeddings. A restricted segment produces zero outbound calls; tests inspect actual request payloads, not only UI state.
- **FD-02 Search provenance:** every search event records provider, request ID, query hash, time, returned source IDs, and outcome. No-result is distinct from unsupported.
- **FD-03 Claim grounding:** subject, value, and context must match a cited passage; a bare number or quote without attribution cannot be marked confirmed.
- **FD-04 Confidence honesty:** results distinguish `confirmed`, `disputed`, `unverified`, and `not_searched`; error/timeout never maps to “no evidence exists.”
- **FD-05 No publication:** the system creates drafts only; export or send requires editor approval bound to exact script/cue-sheet hashes and rights/consent state.
- **FD-06 Injection:** malicious instructions embedded in audio transcripts or search pages cannot trigger tools, reveal restricted speech, or modify verdicts.
- **FD-07 Edit provenance:** each script line links to source segment IDs and external citations; edits preserve a before/after diff and cannot silently detach evidence.
- **FD-08 Eval:** synthetic transcript corpus includes reliable claims, disputed values, outdated sources, off-record remarks, injection, and unsupported claims. Report false confirmations and unnecessary withholds separately.
- **FD-09 Partner proof:** `make demo-e2e` shows a real Parallel Search API call in an opt-in, capped environment; offline CI uses a recorded response fixture but identifies it as simulated.
- **FD-10 Demo:** restricted quote blocked before egress, public claim cited, unsourced claim withheld, editor approves a script export.

## Slices / open questions

Consent and segment model → transcript/claim schema → egress gateway → Parallel adapter → evidence verifier → script/cue export → eval, deletion and hosted demo. Open questions: first language, interview consent form, data residency, retention, and what source types editors accept.
