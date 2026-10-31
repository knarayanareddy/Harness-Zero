# Replit 1st alternate — MuseumWorld: authorable interactive exhibition stories

**Status:** Draft; standalone project proposal. **Reference:** [wevr](https://devpost.com/software/wevr-01crv6)[14], Replit first place.
**New audience:** Small museums creating accessible, branching audio/visual stories for adult visitors.

## Problem hypothesis

Small museums may want interactive narrative experiences but lack a game studio. A generic chatbot can forget exhibit facts, violate authored choices, or invent collection history. Validate with one curator and visitor-experience designer; do not claim educational gains without a visitor study.

MuseumWorld lets a curator create a compact world of scenes, approved facts, choices, and endings without code. A visitor plays turn-by-turn; Gemini may narrate and propose transitions, but deterministic code validates state changes against curator-authored conditions. The app is deployed directly on Replit, and Replit Agent is used during development with required provenance documented.

## Data and authority

Curator-authored facts include object ID, approved text, source citation, rights/credit, reading level, reviewed translations, and expiration/review date. Play state is isolated by session. Gemini sees only the current scene and relevant approved facts; it cannot invent collection facts or change access policy. Every proposed tool call is validated against the world schema before state mutation. Media generation is out of scope for v1; use licensed museum assets.

## Acceptance criteria

- **MW-01 Replit compliance:** development evidence documents Replit Agent use; deployed public app is served from the required Replit domain. Local test deploy is not presented as the hosted runtime.
- **MW-02 Google AI restriction:** all model inference uses approved Google Cloud/Gemini APIs; dependency scan and source grep find no unapproved AI provider/framework.
- **MW-03 Authored-state integrity:** invalid choice, nonexistent entity, unapproved fact, or impossible ending cannot mutate state; schema and transition tests are deterministic.
- **MW-04 Provenance:** factual narration is linked to curator-approved fact IDs; unsupported questions produce a clear abstention and a “ask the curator” route.
- **MW-05 Session isolation:** one visitor’s state, choices, uploads, or contact data cannot appear in another session; test IDOR and guessed IDs.
- **MW-06 Accessible play:** keyboard-only, screen-reader, reduced-motion, captions/transcripts, language choice, and restart/exit flows are tested.
- **MW-07 Prompt injection:** uploaded or curator-entered hostile text cannot add tools, alter game rules, or expose other sessions.
- **MW-08 Offline core:** deterministic game state and scripted Gemini adapter run without credentials; optional live model has spend cap and is explicitly labeled.
- **MW-09 Eval:** authored scenarios test valid and invalid transitions, fact grounding, abstention, adversarial choice text, and narrative consistency. Report per-category failures.
- **MW-10 Demo:** show curator authoring and preview, a visitor branching choice, a grounded answer, a rejected state-violating proposal, and a working Replit-hosted URL.

## Delivery slices

World schema and deterministic engine → curator authoring UI → ADK/Gemini narration/tool proposal → session store and access boundary → Replit deployment → accessibility/security eval and 3-minute demo. Open questions: asset rights, curator review frequency, and whether the first installation is kiosk or BYOD.
