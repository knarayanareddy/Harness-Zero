# IBM 3rd alternate — CrewClock: schedule feasibility and crew-rest planning

**Status:** Draft; standalone project proposal. **Reference:** [HOLD](https://devpost.com/software/hold-wrp4nq)[4], IBM third place.
**New audience:** Production coordinators planning a small documentary crew’s multi-location day.

## Problem hypothesis

Location travel, access windows, cast availability, equipment, meals, and rest constraints interact. A spreadsheet can appear feasible while hiding an impossible handoff or an assumed rule. Validate the scheduling workflow with a production coordinator; no savings or labor-law correctness is claimed before partner review.

CrewClock accepts scenes, required roles/equipment, locations, travel durations, availability, and a versioned set of production constraints. It computes feasible candidate schedules and exposes infeasibility reasons. A coordinator chooses and publishes a schedule; the agent cannot issue a call sheet or override a hard constraint.

## Data and deterministic engine

Records: `ProductionDay`, `Scene`, `RoleRequirement`, `AvailabilityWindow`, `Location`, `TravelEstimate(source, method, timestamp)`, `ConstraintRule(source, version, reviewed_by)`, `ScheduleCandidate`, and `ApprovalSnapshot`. All time arithmetic uses timezone-aware datetimes and explicit setup/teardown buffers. Solver output includes objective, bound, status (`OPTIMAL`, `FEASIBLE`, `INFEASIBLE`, `UNKNOWN`), and constraint IDs. `UNKNOWN` is never described as valid.

The optimizer separates hard rules from soft preferences. Hard constraints are reviewed, versioned, and enforced in code; soft objectives (minimize travel, overtime exposure, idle gaps) are displayed separately. Any statutory/union rule is supplied by a human-curated rule pack and is not inferred by Gemini.

## Partner workflow

Use Gemini/Google ADK to parse a call sheet or explain conflicts; use deterministic solver code for schedule feasibility and costs. IBM Bob is required as a documented development tool. If the selected IBM track resource requires a runtime IBM integration, resolve and implement the exact approved product rather than substituting a README mention.

## Acceptance criteria

- **CC-01 Input validation:** duplicate scenes, timezone-less times, overlapping exclusive resources, or invalid travel duration are rejected or held for coordinator clarification.
- **CC-02 Rule provenance:** every hard constraint has source/version/reviewer; unreviewed rule packs cannot drive a compliance verdict.
- **CC-03 Solver honesty:** `OPTIMAL`, `FEASIBLE`, timeout, and `INFEASIBLE` are distinct; objective and lower bound are reported; no “optimal” claim without solver proof.
- **CC-04 Infeasible case:** impossible schedule returns named conflicting constraints and an explicit human action path, not a relaxed solution.
- **CC-05 Approval:** publishing requires coordinator approval bound to exact schedule, recipients, and rule version; edits invalidate it.
- **CC-06 Recompute:** changing a travel estimate, availability, or scene duration invalidates prior candidate and reruns the solver; stale approval is refused.
- **CC-07 Determinism:** fixed input/solver version returns stable constraints and objective; property tests verify no participant is double-booked.
- **CC-08 Privacy:** cast/crew availability is role-scoped; private reason fields are not sent to the model or included in the call sheet.
- **CC-09 Eval:** synthetic productions cover multi-location days, weather/access window, overtime thresholds, missing inputs, and infeasible cases; report feasibility accuracy, objective cost, and human edits.
- **CC-10 Partner compliance and demo:** Gemini/Google is active; IBM Bob development evidence is retained; demo includes one feasible schedule, one infeasible schedule, a change and a stale publish approval refusal.

## Delivery slices

Input schema and pure solver → versioned rule pack → ADK extraction/explanation → candidate review and approval → call-sheet export → eval, demo, and rollback runbook. No real union rules ship until reviewed by an authorized production specialist.

**Open questions:** first jurisdiction/union, travel-time source and update policy, which roles can approve, and acceptable schedule export format.
