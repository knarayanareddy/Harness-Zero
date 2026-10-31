# 10 — SchoolNotice: grounded school-logistics notices for families

**Status:** Draft specification; no implementation exists.
**Inspired quality pattern:** Document facts are grounded before the agent explains or drafts actions.
**Target:** Families using one participating school district’s transportation and enrollment notices, supported by a family liaison.

## Problem and hypothesis

Families may receive school notices with dates, routes, forms, or next steps in a language or format they find difficult to use. The hypothesis is that a document-grounded action card can reduce missed logistics without pretending to interpret education law or replace a school liaison. Validate with a district/family liaison and adult caregivers before impact claims.

## Product promise and scope

Upload or photograph a transportation, enrollment, meal-program, or school-event notice. SchoolNotice extracts candidate sender/type/date/time/location/required action, checks each against a source span, explains it in a supported language, gives only approved official contact routes, and offers a reminder or draft question for caregiver approval. Unsupported or unreadable facts are omitted and flagged. It does not decide eligibility, diagnose a child, alter an IEP/504 plan, or message a school autonomously.

MVP supports one district, a small curated set of notice types, English plus one partner-reviewed translation language, synthetic notices, and no child account.

## Grounding and privacy model

For digital documents, use the document text layer; for photos, retain a separate transcription result and disclose that the verifier checks against that transcription. Every date/time/action fact requires a source span and deterministic value check before display. The explanation/draft stages receive only verified facts and curated official routes. Any new number, date, route, child name, or school name in generated text is rejected unless it matches an allowed source value.

Minimize child data: names and student IDs are masked before inference when not necessary; raw uploads are encrypted, access-scoped, and deleted per configured retention. All demo examples are synthetic. No data is used for model training.

## Acceptance criteria

- **SN-01 Source grounding:** every displayed date, time, amount, route, sender, and required item points to an exact page/span; unsupported values are absent, not merely warning-colored.
- **SN-02 Extraction refusal:** unreadable, cropped, conflicting, or low-quality notices produce an incomplete card and a human question; no guessed deadline or route.
- **SN-03 Cross-stage guard:** explanation, translated output, reminders, and draft questions cannot introduce an ungrounded date, phone, address, URL, or student identifier; test each output stage.
- **SN-04 Translation boundary:** source facts remain unchanged across languages; a reviewed glossary and human-checked translation fixtures are required. Unsupported language is refused rather than silently translated by an unvalidated path.
- **SN-05 Safe action:** reminder creation requires caregiver confirmation; any school message requires preview, recipient verification, and exact-payload approval. MVP provides draft/copy only—no autonomous send.
- **SN-06 Prompt injection:** instruction-like text inside the notice is treated as document content and cannot alter system behavior, call tools, expose another family’s case, or invent an official route.
- **SN-07 Child privacy:** access checks prevent cross-family reads; logs and traces contain no raw notice text, images, student IDs, or contact details; deletion and retention tests cover all stores and caches.
- **SN-08 Eval:** at least 50 synthetic notices include normal, multi-page, cropped, conflicting, multilingual, malicious, and near-date cases. Report exact-match extraction, grounded precision, refusal/abstention, and false-date count; false displayed dates must be zero in the fixture suite.
- **SN-09 Accessibility:** printable one-page card has readable contrast, 200% zoom, screen-reader order, clear “verified / not verified” labels, and a human-support route.
- **SN-10 Demo:** show a clean notice, a cropped deadline withheld, a prompt injection ignored, a bilingual grounded card, and a reminder requiring approval. State that district-specific rules and translations are not live-verified unless they truly are.

## Delivery plan

1. Choose one district and obtain permission to use only public/synthetic notice formats.
2. Build upload/text extraction and typed fact schema with span references.
3. Implement deterministic grounding and a refusal UI before adding translation or drafting.
4. Add one reviewed language path, action card, reminder approval, and isolated family storage.
5. Run adversarial/eval corpus, accessibility review, deployment smoke, and demo capture.

**Open questions:** partner district, allowed notice set, translation reviewer, data retention period, family-support escalation, and whether reminders are calendar-only or copyable. No official district route becomes active until a named owner verifies it.
