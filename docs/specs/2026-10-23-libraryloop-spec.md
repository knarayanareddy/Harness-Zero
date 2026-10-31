# 07 — LibraryLoop: bounded exception policies for a community tool library

**Status:** Draft specification; no implementation exists.
**Inspired quality pattern:** Teach a repeated human decision, replay a bounded policy, then activate it explicitly.
**Target:** A community tool-lending library handling one narrow return exception: an item returned with a missing non-safety accessory.

## Problem and hypothesis

Staff repeatedly investigate similar low-value return exceptions, while near-matches can involve damage, safety-critical parts, or disputed records. The hypothesis is that only clearly bounded, reversible cases should be automated. Validate case frequency, staff cost, and dispute policy with one library before claiming savings.

## MVP behavior

An event arrives for a returned kit. The system reads authoritative inventory and check-in records, derives a typed fact set, applies hard guardrails, and either resolves under an already-active policy or produces a concise decision card. A staff member may teach a candidate rule. The candidate replays against a versioned labeled history; a staff member then separately activates it. Near-match or unknown fact always escalates.

Allowed actions in v1: create an internal inspection task; mark an item “hold for staff”; close a confirmed data-only exception. No fines, member bans, replacement charges, refunds, access revocation, or messages to patrons without human approval.

## Policy and execution design

Policies are typed data, not generated code or SQL. Schema is a fixed allowlist of domain facts, operators, bounded values, and fixed internal actions. Hard guardrails run before policy match and cannot be relaxed by policy. Facts are re-derived at action time. Side effects are idempotent and read back from the inventory system before an exception is marked resolved.

Replay has two separately reported goals: zero false automatic actions on the labeled set and useful—but not forced—coverage. A policy that does nothing cannot activate, but the system never pressures staff to widen a policy just to meet a coverage quota. Corpus quality and limitations are visible.

## Acceptance criteria

- **LL-01 Inert proposal:** model output creates only a candidate policy; it cannot execute, activate, or edit a catalog record.
- **LL-02 Schema safety:** unknown fields/operators/actions, unbounded values, code strings, SQL fragments, and cross-domain fields fail validation before storage.
- **LL-03 Guardrails:** missing item, serial mismatch, damage, safety-critical component, duplicate event, incomplete check-in, or conflicting inventory always escalates regardless of candidate policy.
- **LL-04 Replay gate:** activation requires a passing replay against the current corpus hash and an explicit staff approval bound to the exact policy diff. Corpus or policy change invalidates the report/approval.
- **LL-05 No false automatic action:** the committed corpus must have zero false automatic decisions; reports show every row, labeled outcome, coverage, and version. This is a corpus result, not a universal guarantee.
- **LL-06 Writes verified:** every action uses an idempotency key; duplicate delivery produces one effect; read-back mismatch prevents closure and raises an exception.
- **LL-07 Near-match:** one changed field that fails a policy condition escalates with the exact unmatched reason; test pairwise near-matches.
- **LL-08 Audit:** append-only hash-linked events capture fact source, policy version, replay corpus hash, approver, action, and read-back; tampering is detected.
- **LL-09 Model boundary:** the Strands agent has read-only investigation tools; mutation functions are not exposed as agent tools. Prompt injection in staff notes can only increase review/uncertainty.
- **LL-10 Demo:** show teach → replay → activation → one idempotent automated resolution → a near-match escalated. Default seed data is synthetic; no real member records.
- **LL-11 Offline operation:** clean-clone scripted provider path exercises the same agent and policy loop; optional live model checks are separate and spend-capped.

## Evaluation and build sequence

Use at least 40 synthetic historical cases with explicit labels, plus held-out near-matches and injected notes. Two reviewers independently label ambiguous cases; unresolved disagreement is labeled “review,” not forced into auto. Report false-auto, false-escalation, coverage, and replay-set provenance.

Slices: typed domain facts and fixtures → pure policy engine/guardrails → read-only Strands investigation → candidate/replay/activation → idempotent adapter/read-back → audit, UI, eval, and demo.

**Open questions:** library’s existing exception taxonomy, who can activate policies, how to handle disputes, approved retention, and whether the source inventory system has a reliable read-back API.
