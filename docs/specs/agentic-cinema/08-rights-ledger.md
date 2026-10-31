# Parallel 2nd alternate — Rights Ledger: evidence for archival-media clearance

**Status:** Draft; standalone project proposal. **Reference:** [Consentinel](https://devpost.com/software/consentinel)[9], Parallel second place.
**New audience:** Documentary producers tracking permission scope for archival clips, stills, and music cues.

## Problem hypothesis

Rights information is scattered across licenses, emails, cue sheets, territory windows, and asset records. The hypothesis is that a structured registry plus cited public-source discovery can surface mismatches before delivery. Validate with a rights coordinator. A model must not decide that infringement occurred or issue a takedown notice.

## Product promise

For each asset, store owner, licensor, permitted media/use, territory, term, edit restrictions, attribution, source evidence, and reviewer. Before delivery, compare the proposed cut manifest to the registry and return `covered`, `mismatch`, `unknown`, or `human_review`. Parallel Search can discover publicly visible reuse or documentation; it does not prove ownership or legal status.

## Data flow and authority

Import a contract/receipt → extract candidate fields with Gemini and exact page quotes → verify quotes against the document → human corrects and approves the grant → ingest a cut/asset manifest → deterministic scope match → optional Parallel search for public evidence → evidence case card → rights coordinator disposition. Human counsel handles legal interpretation. No external notice/send tool exists in the MVP.

Consent registry fields are typed; a “cleared” outcome must cite a grant ID and match use, territory, date, and asset identity. Unmatched identity, missing contract, expired term, conflicting grants, or weak media match cannot clear. Preserve source, rights owner, rule/version, and exact approval digest.

## Acceptance criteria

- **RL-01 Quote grounding:** every extracted license field retains source page/span; quote absent from source is discarded and routed to review.
- **RL-02 Exact scope:** deterministic matcher tests rights use, territory, term, media type, edit restriction, and asset ID independently; unknown values never default to covered.
- **RL-03 Search meaning:** Parallel Search is runtime-used for a specific discovery task; search evidence is labeled “public signal,” never ownership proof.
- **RL-04 No legal verdict:** UI and exports say `potential mismatch`/`unknown`, not infringement/violation; no takedown or rights assertion can be sent by the agent.
- **RL-05 Human approval:** registry additions, scope changes, clearances, and delivery attestations require a named authorized reviewer and a hash-bound snapshot.
- **RL-06 Confidentiality:** private contracts and unreleased cuts are never sent to public search; test request-body absence of source text and asset URLs.
- **RL-07 Injection:** hostile language in contracts, metadata, search results, or filenames cannot expand tools or approve a grant.
- **RL-08 Audit:** a delivery report can be regenerated with source versions and decisions; mutation of a stored citation or approval is detectable.
- **RL-09 Eval:** synthetic corpus includes exact grants, adjacent territory, expired license, different media type, ambiguous identity, and conflicting clauses; report false clearances (target zero), false holds, and abstentions.
- **RL-10 Demo:** clear one fully licensed asset, hold an out-of-territory clip, show an ambiguous license routed to a human, and produce an evidence bundle.

## Slices / open questions

Typed registry and matcher → document ingestion/quote verifier → manifest upload → Parallel discovery adapter → reviewer workflow → generated clearance evidence report. Open questions: acceptable asset identity format, contract-retention policy, counsel review process, and Parallel Search rate/budget limits.
