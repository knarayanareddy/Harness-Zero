# Replit 2nd alternate — CaptionProof: human-reviewed caption QA for event recordings

**Status:** Draft; standalone project proposal. **Reference:** [FrameKind](https://devpost.com/software/framekind-tz85od)[15], Replit second place.
**New audience:** Small event producers checking captions and speaker cues before publishing recordings.

## Product goal

Take an authorized recording plus an existing VTT/SRT, find likely omissions, timing errors, speaker ambiguity, and non-speech audio needing a caption, then present timestamped evidence and editable suggestions. The editor decides whether to apply each change. CaptionProof is a review aid, not an accessibility certification or substitute for Deaf/hard-of-hearing user review.

## Workflow

Upload media and captions → validate media/format → transcribe/align with Google AI tools → compare against existing captions → propose specific repairs with time ranges → editor previews audio/video context and accepts/rejects → generate a new versioned VTT/SRT → run deterministic format/timing checks → export with a change report. Original media and caption file remain immutable.

Use Gemini/Google Cloud AI only, with Replit Agent as the development tool and Replit as the production host. FFmpeg and non-AI parsing libraries are permitted. Never use third-party speech AI, off-platform model APIs, or borrowed media without license.

## Acceptance criteria

- **CP-01 Replit track:** development provenance is retained and the public project runs from a Replit-hosted deployment. State clearly if any external Google service is unavailable.
- **CP-02 Input preservation:** input caption/media hashes are recorded; edits create a new version and can be reverted.
- **CP-03 Evidence:** each suggestion cites the audio/video time range and observed issue; transcript-only mode explicitly says visual/sound-effect dimensions were not reviewed.
- **CP-04 No silent edits:** no caption changes are applied without editor approval; approved batch carries a digest of source version and chosen edits.
- **CP-05 Deterministic validation:** output parses as valid WebVTT/SRT; timecodes are monotonic, non-overlapping unless explicitly allowed, and within media duration.
- **CP-06 Uncertainty:** low confidence, overlap, noisy audio, or multiple speakers yields review/abstain; no fabricated words or speaker names.
- **CP-07 Injection/privacy:** text in captions cannot trigger tools or leak to third parties; uploads have size/type validation, metadata stripping, access isolation, and deletion tests.
- **CP-08 Eval:** licensed/synthetic short clips include speech, music, sound cues, overlapping speakers, proper nouns, silence, and timing drift; report word error, omission precision/recall, timing error, and human edit rate.
- **CP-09 Accessibility:** review interface supports keyboard, screen readers, captions for UI audio, visual waveform alternatives, and reduced-motion mode.
- **CP-10 Demo:** show a missing cue proposed with evidence, a false positive rejected, corrected captions exported, and output validation; keep video under contest limit.

## Delivery slices / open questions

Input parser and immutable media store → Google transcription/vision adapters → typed repair suggestions → editor review and diff → deterministic export validator → eval and Replit production deployment. Open questions: first target language, accepted delivery profile, retention, and whether partner-user testing is available.
