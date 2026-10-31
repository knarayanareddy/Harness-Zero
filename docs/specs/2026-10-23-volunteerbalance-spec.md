# 02 — VolunteerBalance: fair, consent-aware mutual-aid scheduling

**Status:** Draft specification; no implementation exists.
**Inspired quality pattern:** Private constraints, measurable allocation, human decisions for authority changes.
**Target:** A small mutual-aid organization assigning recurring, non-emergency volunteer shifts.

## Problem and hypothesis

Volunteer coordinators often assign recurring work using spreadsheets and group chats. People may have constraints they do not want to explain publicly; equal task counts can still produce an unfair load. Validate with three coordinators and five adult volunteers. This is a problem hypothesis until that discovery is done; do not claim that the optimizer increases retention without a pilot.

## Product promise and boundaries

Each volunteer can privately state availability, maximum workload, travel limits, and task preferences. The system proposes a feasible schedule, shows a deterministic fairness report, lets each person accept or counter their own assignments, and escalates uncovered or disputed work to the coordinator.

No employment, medical, caregiving, or emergency allocation; no inference of private reasons; no penalty, public ranking, or automatic reassignment of an accepted shift. Participation is opt-in, and the coordinator remains accountable for the published rota.

## Workflow and state

`PERIOD_DRAFT → PRIVATE_INTAKE → PROPOSAL_READY → VOLUNTEER_REVIEW → COORDINATOR_REVIEW → PUBLISHED → IN_PROGRESS → CLOSED` with `NEEDS_HUMAN`, `DECLINED`, and `CANCELLED` branches. Any change after a volunteer accepts reopens only affected assignments and requires that volunteer’s new response.

Inputs: tasks with duration, location, skill requirement, priority, date/window, and fixed prerequisites; volunteers with private constraints and explicit visibility settings; coordinator-set workload caps. Outputs: proposed assignments, deterministic load report, exceptions, responses, and a versioned final schedule.

## Fairness policy and model boundary

The agent may summarize preferences and propose candidate swaps. A deterministic solver/rule engine validates hard constraints and computes workload metrics. The fairness metric is visible and configurable only by the coordinator before the period begins; it cannot override hard limits. Capacity-adjusted burden is reported with its formula, inputs, and caveats; it is not presented as an objective moral truth. No assignment is considered accepted until the volunteer accepts it. The agent cannot message, publish, or change capacity by itself.

Private reason text remains in the volunteer’s own record. The allocator receives only opaque constraint IDs and permitted effects (e.g. “unavailable Tue”), never the reason. The public rota receives accepted assignments only. The privacy boundary is tested across prompts, tool payloads, logs, notifications, and exports.

## Acceptance criteria

- **VB-01 Offline reproducibility:** clean setup and `make verify` work without credentials or network; fixed seed produces byte-stable proposals.
- **VB-02 Hard constraints:** no generated proposal violates availability, skills, max shifts, location, or rest interval; property tests cover boundary values.
- **VB-03 Fairness explanation:** every score is recomputable from persisted inputs and versioned formula; changing order of input rows does not change the result.
- **VB-04 Infeasible plan:** if no feasible allocation exists, preserve uncovered tasks and surface a coordinator decision. Never relax a hard limit to make the rota look complete.
- **VB-05 Privacy isolation:** a seeded private reason is absent from every other volunteer’s API projection, model request, log, notification, and exported rota. Include canary tests for direct, paraphrased, encoded, and split text; no claim of perfect semantic leak detection.
- **VB-06 Consent:** no volunteer-to-volunteer contact or schedule publication before both relevant participants consent; test conditional language, silence, decline, stale consent, and duplicate taps.
- **VB-07 Authority:** unknown tool/action denied by default. The agent cannot publish, reassign an accepted shift, alter someone’s cap, or reveal a reason.
- **VB-08 Stale state:** approval is bound to the exact proposal version and affected volunteers; a changed task or availability makes the old approval invalid.
- **VB-09 Resilience:** duplicate event delivery does not double-assign; restart preserves pending decisions and accepted assignments; uncertain writes are shown as uncertain, never replayed blindly.
- **VB-10 Eval:** versioned synthetic scenarios report hard-constraint violations (must be 0), fairness distribution, unfilled work, and human override rate. Publish all failures and the baseline formula.
- **VB-11 UX/accessibility:** a volunteer can set availability privately, understand one proposed shift, accept/decline, and see the effect without needing to use a group chat; keyboard, screen-reader, and narrow-screen checks pass.

## Demo and rollout

Five-minute demo: coordinator defines a shift set; two volunteers enter different private availability; the agent proposes a rota; code rejects a tempting but infeasible swap; volunteers accept separately; one decline reopens only the affected assignment; audit view shows why the final plan changed. Use synthetic adult profiles. Pilot only after a partner approves privacy language, retention, coordinator authority, and a manual fallback.

## Build slices

1. Domain types, deterministic allocator, constraints, fairness report, property tests.
2. Private intake and role projections; canary privacy tests.
3. Strands proposal/summary path with scripted model; action allowlist tests.
4. Consent workflow, version-bound approvals, persistent event log, restart tests.
5. Mobile/accessibility, eval corpus, deployment, demo, and operations guide.

**Open questions:** partner’s actual scheduling rules; whether travel burden is measured or excluded; retention period; who may publish a period. Resolve these with a partner; keep conservative defaults until then.
