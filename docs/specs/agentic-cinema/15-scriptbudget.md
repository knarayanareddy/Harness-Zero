# Replit 3rd alternate — ScriptBudget: production-feasibility planning from a screenplay

**Status:** Draft; standalone project proposal. **Reference:** [Project Greenlight](https://devpost.com/software/uniq-jn5e4r)[16], Replit third place.
**New audience:** Independent producers building an early budget and schedule from a screenplay.

## Product promise

Parse an authorized screenplay into a reviewable breakdown: scenes, locations, cast roles, day/night, practical effects, crowd/animal flags, and estimated production resources. Combine those claims with a small, disclosed benchmark dataset and producer-entered rates to generate scenario ranges and a draft schedule. **Do not predict box-office revenue, greenlight a script, score artistic merit, or tell a producer whether a film will succeed.**

## Data/agent boundary

Gemini on Google Cloud extracts typed scene facts and points to page/line spans. A deterministic cost/schedule engine computes totals from producer-configured rates and named assumptions. Historical comparables are opt-in, licensed, and reported as descriptive ranges with sample size and distribution; no causal or predictive claim from a small corpus. Replit Agent is used in development and the app is hosted on Replit. Use only approved Google AI and no third-party AI model.

The author controls all assumptions. Every number on a budget sheet carries an input source and formula. The agent may explain tradeoffs (e.g. grouping scenes by location) but cannot change rates, remove a scene, or publish an investor-facing report without explicit producer approval.

## Acceptance criteria

- **SB-01 Screenplay rights:** upload requires rights/permission confirmation; file hash and access scope are recorded; demo uses original synthetic scripts only.
- **SB-02 Extraction provenance:** each scene fact links to source page/line or is marked `unverified`; missing pages and ambiguous scene breaks require review.
- **SB-03 Deterministic arithmetic:** all currency uses integer minor units; costs are recomputed from explicit rate cards and rounding rules; example fixtures match hand calculations.
- **SB-04 Range honesty:** estimates display assumptions, sample count, min/median/max or stated interval, and exclude missing data; no single “predicted gross” or success score.
- **SB-05 Producer authority:** changes to rate card, assumptions, schedule, or exported report require authenticated producer approval and audit diff.
- **SB-06 Bias/sensitivity review:** do not infer cast demographics or market value; no protected-trait features. Tests verify that changing a demographic mention cannot change cost or ranking.
- **SB-07 Injection:** script text cannot trigger tools, reveal another project, or modify application policy.
- **SB-08 Eval:** synthetic screenplay corpus covers fragmented scenes, flashbacks, ambiguous locations, missing page text, and arithmetic boundaries; report extraction exactness and forecast error separately.
- **SB-09 Replit requirement:** Replit Agent provenance and Replit-hosted URL are verified; Google AI runtime is used on active extraction path.
- **SB-10 Demo:** show a grounded breakdown, one uncertain scene sent to review, producer-adjusted rate card, deterministic schedule/cost change, and a labeled draft export.

## Slices and open questions

Script parser/schema → source-span grounding → deterministic budget engine → comparable-data provenance → Replit review UI → eval, privacy/accessibility, and demo. Open questions: first production type, rate-data source/license, minimum sample policy, and retention of scripts. No investment or box-office recommendation in any release.
