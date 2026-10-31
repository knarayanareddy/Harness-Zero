# Parallel 3rd alternate — Archive Dossier: research packets for documentary episodes

**Status:** Draft; standalone project proposal. **Reference:** [The Boxes](https://devpost.com/software/the-boxes)[10], Parallel third place.
**New audience:** Documentary researchers preparing a source-grounded packet for one episode topic.

## Problem hypothesis

Research packets accumulate clippings and links but often hide gaps, contradictions, source age, and rights. The hypothesis is that an agent can build a declared research plan, fill evidence gaps, and keep every claim traceable without pretending the dossier is exhaustive. Validate with a documentary researcher.

## Workflow and domain objects

Researcher enters an episode question and selects an approved source policy → Gemini proposes a bounded topic taxonomy (“boxes”) → researcher approves/edits it → Parallel Search queries each objective → Parallel Extract obtains source text → deterministic provenance/quality checks → Gemini proposes atomic claims and contradictions → reviewer accepts, rejects, or asks for another search → export cited dossier. The agent may suggest a new research category only as a proposal; it cannot expand spend or crawl without bounds.

Each evidence item stores source URL, publisher, published/accessed dates, extract status, license/rights status, objective ID, text/media hash, and quote span. Claims store relation, cited evidence, contradiction state, reviewer disposition, and version. Coverage is computed from planned objectives and reviewed evidence, not the model’s confidence alone.

## Parallel integration and safeguards

Parallel Search and Extract must be active runtime services. Google ADK/Gemini handles planning and synthesis; vector search may use Google embeddings. No other model/agent platform. Restricted interview material, embargoed sources, copyrighted full text, and private notes are never sent to web search. Search results are untrusted prompt-injection inputs and cannot call tools.

## Acceptance criteria

- **AD-01 Plan bounds:** approved objective count, per-objective query cap, total request cap, token/latency cap, and stop condition are enforced in code.
- **AD-02 Query provenance:** every query and result is linked to an objective and run; duplicate sources are deduplicated without erasing distinct editions.
- **AD-03 Extraction states:** no result, fetch failure, blocked page, partial extract, and useful passage are distinct states; no failure becomes a factual “nothing supports this.”
- **AD-04 Evidence grounding:** every factual sentence in the dossier maps to one or more stored source spans; unsupported text is refused or labeled hypothesis.
- **AD-05 Contradiction discipline:** Gemini may propose a conflict; deterministic code preserves both source claims and the reviewer resolves it. The system cannot silently choose a “winner.”
- **AD-06 Rights/injection:** content license and source trust are separate fields; injection in result text cannot call tools, alter the plan, or modify records.
- **AD-07 Change control:** adding a box/query objective after approval requests confirmation and shows the incremental API/cost impact.
- **AD-08 Eval:** source corpus contains corroborating, conflicting, outdated, low-quality, no-result, and adversarial sources; report citation precision, claim support, source diversity, abstention, and stale citations.
- **AD-09 Export:** dossier includes all citations, unresolved conflicts, coverage denominator, and run date; verify PDF/HTML links and text extraction from the built artifact.
- **AD-10 Demo:** show a research plan, one supported and one conflicting claim, an uncovered objective, and a human decision without claiming exhaustive research.

## Slices / open questions

Research schema and static corpus → bounded Parallel Search/Extract adapters → evidence store → claim/contradiction review → dossier export → multimodal media as optional later scope. Open questions: first topic, source allowlist, rights policy, and whether local archives can be linked without ingestion.
