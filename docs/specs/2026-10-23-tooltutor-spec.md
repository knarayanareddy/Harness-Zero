# 04 — ToolTutor: evidence-gated equipment onboarding

**Status:** Draft specification; no implementation exists.
**Inspired quality pattern:** One visible instruction at a time; verified evidence gates the next step.
**Target:** Community makerspaces onboarding members to a deliberately limited set of low-risk equipment.

## Problem and hypothesis

New members may have access to manuals but still need an instructor to translate a procedure into their exact machine and current state. A generic chatbot can skip steps or confidently describe the wrong control. Validate the need with two makerspace instructors and five adult members. The MVP teaches a single approved procedure, not arbitrary repair.

## Product promise and strict scope

ToolTutor guides an adult through a versioned, instructor-approved procedure for a catalogued **non-mains, low-energy task**. It shows one action and one “what the next photo must show” condition. A photo may support a pass/fail proposal; deterministic code alone controls step progression. A stop condition ends the session and routes to an instructor.

Initial catalog: a small set of non-powered setup/inspection tasks selected and signed off by a partner lab. Exclude mains wiring, batteries, chemicals, blades, lasers, CNC, lifting, pressurized systems, medical devices, and any procedure the lab has not reviewed. The model may not generate a new procedure.

## Workflow and state

`SELECT_APPROVED_TASK → MACHINE_CONFIRMATION → PLAN_LOADED → STEP_SHOWN → EVIDENCE_SUBMITTED → VERIFIER_PROPOSAL → (PASS: NEXT_STEP | FAIL: RETRY | STOP: INSTRUCTOR_HOLD) → COMPLETE_PENDING_MEMBER_CONFIRMATION`.

Each step stores a procedure/version/page citation, required evidence description, prohibited actions, stop condition, model verdict, image hash, and timestamp. A successful check unlocks exactly one next step. “Complete” means the member confirms the task and the instructor policy permits it; it never means the vision model proved the machine safe.

## Agent design

The active Strands agent may explain a signed procedure, request the current step’s evidence, summarize a typed verifier result, or open instructor review. It has no direct state mutation tool for advancing. A `StepGate`-style deterministic service checks job identity, current step, verdict record, policy version, and hazard stop immediately before the transition. Missing/stale state blocks. A tool result cannot change the gate’s decision.

## Acceptance criteria

- **TT-01 Catalog boundary:** unknown task or machine model returns “not supported; ask an instructor”; it cannot produce steps from free-form model output.
- **TT-02 Provenance:** every displayed instruction maps to a reviewed procedure version and page/section; the UI distinguishes source text from model paraphrase.
- **TT-03 One-step gate:** no verdict, wrong-step verdict, failed verdict, wrong job ID, stale job version, or unknown state cannot advance; tests assert the state index remains unchanged.
- **TT-04 Hazard precedence:** a stop verdict interrupts the session and blocks further tools, even if the model subsequently proposes a pass. It creates an instructor task and records the trigger.
- **TT-05 Permission:** only the member owning the session or an authorized instructor can view it; guessed IDs and cross-session IDs fail closed.
- **TT-06 Vision limits:** blurry, dark, occluded, wrong-machine, and out-of-frame images produce `uncertain`/retry or instructor review—not inferred success. Confidence values alone never authorize progression.
- **TT-07 Adversarial gate test:** use a permissive scripted model instructed to call `advance_step` immediately. The real Strands hook/service boundary blocks it before state mutation.
- **TT-08 Replay resistance:** reusing a pass from a previous step, job, or procedure version is rejected; duplicate photo submission is idempotent.
- **TT-09 Evaluation:** versioned image set includes positives, near-matches, absent evidence, hazard controls, and adversarial instructions; report per-class precision/recall and every miss. No claim that the model detects every hazard.
- **TT-10 Demo/deploy:** `make demo-e2e` shows wrong evidence blocked, correct evidence unlocks one step, hazard stops the run, and trace displays gate reason. Hosted demo uses synthetic fixture photos only.
- **TT-11 Accessibility:** phone-first, large tap targets, captions/transcripts for spoken output, keyboard and screen-reader flow, and 200% zoom pass documented checks.

## Demo and slices

Use one synthetic low-risk task with three steps. Show the procedure source, submit a wrong photo, demonstrate a blocked advance under the adversarial prompt, submit a correct photo, advance once, then trigger a synthetic hazard and show the instructor hold. Never demonstrate real hazardous equipment.

Slices: signed procedure schema/import → session state and deterministic gate → scripted Strands loop → image fixture/eval harness → mobile UI/trace → accessibility, demo operations, and release checks.

**Open questions:** partner-approved task catalog, acceptable age/consent flow, image retention, and instructor response process. Until resolved, no real workshop use.
