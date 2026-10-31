# ClickHouse 3rd alternate — AssetFinder: rights-aware reuse of studio footage

**Status:** Draft; standalone project proposal. **Reference:** [Locaish](https://devpost.com/software/locaish)[13], ClickHouse third place.
**New audience:** Editors searching a studio’s approved b-roll and archival library for reusable shots.

## Problem hypothesis

Studios often reacquire or reshoot material while suitable footage exists but is difficult to locate. The hypothesis is that a typed asset catalog plus multimodal retrieval can find candidate clips quickly without misrepresenting rights or visual similarity. Validate with a production librarian and editor.

## Product flow

Ingest only studio-owned or licensed footage with source, rights, territory, expiry, duration, frame rate, resolution, audio, and permitted-use fields. A background process derives non-AI metadata and Google Gemini Embedding 2 vectors; all original media remains in private object storage. Editor describes a shot or uploads an authorized still → ADK agent translates intent into filters and candidate search → ClickHouse MCP retrieves matching metadata/vector neighbors → Gemini reviews a small set of frames and explains fit → editor selects and previews a candidate → rights gate checks the current license before export.

The system returns “candidate match,” never “same shot” or “cleared” from embedding distance alone. Rights status is deterministic and time-bounded. No media is sent to an unapproved external model, no output includes raw content from restricted assets, and no clip is placed in a final timeline without human review.

## Acceptance criteria

- **AF-01 Rights gate:** missing/expired/territory-mismatched license prevents preview/export beyond the approved thumbnail policy; no semantic score can override it.
- **AF-02 Ingest integrity:** asset hash, derivative lineage, owner, license source, and model/embedding version are stored; duplicate ingest is idempotent.
- **AF-03 ClickHouse runtime:** active MCP query retrieves candidates from the actual ClickHouse cluster; direct in-memory search is not the only path.
- **AF-04 Retrieval evaluation:** labeled query set reports recall@k, precision@k, rights-filtered results, and no-result behavior. Exact metric thresholds are set with the librarian before model tuning and frozen during evaluation.
- **AF-05 Query grounding:** every candidate shows why it matched (structured filters and similarity) and its rights metadata; the agent may not invent camera/lens attributes.
- **AF-06 Uncertainty:** low similarity, missing rights, or ambiguous visual match returns “no suitable cleared match” rather than forcing a result.
- **AF-07 Injection/security:** prompt text in captions/metadata cannot change data filters, reveal another production, or bypass access controls; DB account is read-only.
- **AF-08 Data protection:** media URLs are signed and short-lived; previews and embeddings follow per-asset retention/deletion; logs contain IDs, not raw captions or URLs.
- **AF-09 Demo:** use original/cleared footage only; show a natural-language request, rights-filtered candidate search, one rejected expired license, and a human-selected export record.
- **AF-10 Reproducibility:** `make setup`, `make verify`, and `make demo-e2e` work without cloud credentials using a compact synthetic asset set and scripted Gemini adapter.

## Build slices / open questions

Rights schema and private asset store → ClickHouse/MCP metadata and vector query → Gemini frame review → editor selection and export receipt → rights/security eval and deployment. Open questions: allowed asset classes, licensing source of truth, embedding deletion guarantees, and acceptable preview policy.
