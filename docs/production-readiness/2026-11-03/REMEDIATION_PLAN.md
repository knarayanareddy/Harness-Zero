# Harness-Zero — remediation plan

This plan is proposed, not executed. Finding IDs refer to [REVIEW.md](REVIEW.md). Commands are **future checks**, not evidence of a pass. New test and release-document paths require agreement.

## Phases and dependencies

| Phase | Entry → exit | Tasks |
|---|---|---|
| 0 Baseline | Owner approves isolated test environment → reproduce findings with synthetic data and capture current CI | T1 |
| 1 Trust boundaries | T1 complete; T2 precedes T3 → credential and stream negative cases fail closed | T2, T3 |
| 2 Correctness and supply chain | T4 follows T1; T5 follows T2–T4 → final-call invariants and reproducible gates demonstrated | T4, T5 |
| 3 Operations and UX | T2–T5 complete; owner selects platform → recovery and usable safety decisions observed | T6 |
| 4 Independent acceptance | T2–T6 evidence available → independent reviewer repeats negative and recovery checks | T7 |

Dependency map: `T1 → T2 → T3`; `T1 → T4`; `{T2,T3,T4} → T5`; `{T2,T3,T4,T5} → T6`; `{T2,T3,T4,T5,T6} → T7`. D0 safeguards documentation publication; it does not turn internal exploratory checks into accepted results.

### D0 — Documentation disclosure safeguards

**Status:** the owner already authorized commissioned defensive documentation; this is not a new authorization, remediation or deployment approval. **Observed path:** `SECURITY.md:128–133`; additional restricted-evidence handling is **proposed**.

1. Review the commissioned documents for real credentials, personal or customer data and operational exploit payloads; hold or redact genuinely sensitive material beyond the source-grounded defensive report.
2. Keep future outside vulnerability reports and additional restricted evidence private; do not assume the listed `.local` address works or contact it as part of this task.
3. Before any public PR, verify the exact document bytes and source revision against the documentation QA record, and separately check any deployment or automation risk; do not treat documentation approval as application approval.

**Output:** documentation QA record and any necessary restricted-evidence handling. **Verification:** compare public-bound bytes to the QA record and check that genuinely sensitive material is excluded. **DoD:** [ ] sensitivity review recorded; [ ] revision and exact bytes checked; [ ] applicable deployment risks separately reviewed; [ ] restricted evidence held or redacted. No contact or publication was performed here.

### T1 — Establish a reproducible baseline (HZ-01–06)

**Prerequisite:** isolated machine or disposable users and workspace; no real secrets. **Paths:** `harness/frontends/web/server.py`, `harness/kernel/gate.py`, `tests/frontends/test_web_server.py`, `.github/workflows/ci.yml`.

1. Record pinned commit, environment, effective security preset and existing dependency versions; inspect the complete bus, bootstrap, policies and test paths before attributing root causes.
2. Write isolated failing probes for public bootstrap, ticket route scope, pre-existing daemon mode, raw streaming canaries and argument mutation; distinguish demonstrable impact from a static hypothesis.
3. Run the repository's documented checks only after authorization in a disposable environment, including planned `ruff check .` and `pytest -q -rs`; capture failures and skips rather than changing thresholds.

**Output:** baseline matrix and failing reproductions. **Verify:** probes reach the shipped boundary and fail before changes without production accounts. **DoD:** [ ] baseline captured; [ ] unconfirmed impacts labeled; [ ] failure and skip logs retained.

### T2 — Restrict bootstrap, tickets and token file (HZ-01, HZ-03, HZ-04)

**Prerequisite:** T1 and owner-approved local-user threat model. **Paths:** `harness/frontends/web/server.py`, `tests/frontends/test_web_server.py`.

1. Design an owner-bound bootstrap with no public response containing a reusable master token; specify local cross-user assumptions and migration for CLI and browser clients.
2. Bind ticket redemption to the intended SSE endpoint and method; enforce expiration and one use. Write daemon state via securely created restrictive, non-symlinked file, validate existing mode/ownership, and avoid insecure replacement races.
3. Add integration cases using separate clients/users where feasible: unauthenticated bootstrap, ticket on commands and files, expired/replayed ticket, permissive pre-existing file and symlink target; test legitimate startup and SSE too.

**Output:** hardened authentication and token-file lifecycle with regression tests. **Verify:** independent client cannot obtain a command credential; invalid tickets fail; inspect files on supported OSes. **DoD:** [ ] negative and positive cases observed; [ ] no master token in public HTML/cookie; [ ] recovery and migration documented.

### T3 — Protect streamed and durable output (HZ-02)

**Prerequisite:** T1, with T2 for authenticated SSE testing. **Paths:** `harness/kernel/gate.py`, `harness/kernel/bus.py`, `harness/frontends/web/server.py`, `tests/kernel/test_cooperative_timeout.py`.

1. Trace raw output through producer, bus, replay, SSE, transcript and metadata, and enumerate exact points where secrets may occur.
2. Implement one consistent protection policy before untrusted publication/persistence, accounting for secrets split over chunks, cancellation, multiple subscribers and bounded buffering; specify behavior when the secret store is unavailable.
3. Exercise synthetic canaries through the real executor-to-client path, including timeout and restart; independently inspect SSE, replay and persisted records.

**Output:** tested stream protection and documented limitations. **Verify:** deliberately bypassing the sanitizer makes canary tests fail, while ordinary output and cancellation still work. **DoD:** [ ] no canary in checked sinks; [ ] chunk-boundary cases pass; [ ] residual unscanned sinks listed.

### T4 — Validate the final tool call (HZ-05)

**Prerequisite:** T1. **Paths:** `harness/kernel/gate.py`, `tests/kernel/test_gate_invariants.py`.

1. Map hook and policy mutation semantics and decide whether mutation is permitted after security evaluation.
2. Validate the final call against its declared schema and ensure every jail/risk/approval decision corresponds to the exact arguments executed; reject unsafe mutation rather than trusting earlier checks.
3. Add real-gate cases for invalid hook mutation, policy mutation after jail evaluation, traversal and legitimate transformations; assert handler non-invocation on rejection.

**Output:** explicit final-call invariant and adversarial tests. **Verify:** disabling the final validation or passing pre-mutation arguments to a policy causes the appropriate negative test to fail. **DoD:** [ ] final call and policy decisions match; [ ] reject cases never call handler; [ ] legitimate calls still work.

### T5 — Meaningful, reproducible automation (HZ-06; preventive)

**Prerequisite:** T1; regression tests from T2–T4. **Paths:** `pyproject.toml`, `package-lock.json`, `.github/workflows/ci.yml`, `package.json`.

1. Resolve and record supported Python dependency versions; audit Python and Node lock/dependency trees using approved advisory sources and review action revisions.
2. Adopt a reproducible Python lock/constraints strategy and pin verified action commit SHAs; preserve the existing OS/Python matrix and scenario/strict UI gates.
3. Add focused security tests to the existing CI job, report skips explicitly and run a deliberate broken-control test in an isolated branch.

**Output:** dependency baseline and CI evidence. **Verify:** clean environments resolve intended versions; broken controls fail tests; no test is skipped merely to pass. **DoD:** [ ] lock and advisory review recorded; [ ] intended gates run; [ ] skips explained.

### T6 — Recovery, release and accessible safety UX (preventive)

**Prerequisite:** T2–T5; owner chooses release target, retention and recovery objectives. **Paths:** `README.md`, `tests/scenarios/security_preset_badge_web_dom/scenario.yaml`, `scripts/package_macos.sh`.

1. Inventory transcript, artifacts, configuration and secrets storage; agree backup scope, permissions, retention and restore objectives without placing credentials in backups or logs by default.
2. On the chosen platform, package and sign only with owner-approved credentials; record release provenance, rollback steps and an actual isolated restore and reindex observation.
3. In a real browser and keyboard/screen-reader session, test approval, denial, timeout and unavailable-sandbox badge; correct ambiguous states and record evidence. Verify a reachable private vulnerability-reporting channel.

**Output:** release/recovery runbook, restore record and accessibility findings. **Verify:** restored workspace reproduces expected transcript and index without real secrets; release rollback is rehearsed; safety status is perceivable and operable without a mouse. **DoD:** [ ] restore measured against owner objectives; [ ] signing/rollback evidence retained; [ ] UX issues resolved or accepted.

### T7 — Independent acceptance (all findings)

**Prerequisite:** T2–T6 and complete evidence. **Paths:** changed source and tests above; acceptance record path is **proposed**, not yet present.

1. A reviewer independent of implementation inspects the final diff and maps each finding to the live consumer and its negative test.
2. Repeat cross-user auth, split-secret SSE, mutation and restore tests in a clean environment; deliberately break a control to establish that its regression test detects the failure.
3. Document remaining risks, skipped platforms and owner decisions, then approve or reject release based on observed results rather than test counts.

**Output:** signed-off acceptance record. **Verify:** results include exact revision, environment, commands and negative-control outcomes. **DoD:** [ ] independent review complete; [ ] evidence reproducible; [ ] unresolved risks explicitly owned.

## Acceptance, approvals and rollback

- [ ] D0 documentation safeguards are checked before any public documentation PR; genuinely sensitive additional evidence remains private or redacted.
- [ ] HZ-01–05 reproduced and fixed or formally accepted with scoped mitigations; HZ-06 dependency and CI risks reviewed.
- [ ] Auth, sandbox, streaming and UI negative cases demonstrate failure when safeguards are removed; no mirror-only tests.
- [ ] Restore, chosen release/signing and rollback observed on supported targets; no claim extends beyond tested environments.
- [ ] Owner approves threat-model changes, credential use, external exposure, data retention, signing and deployment before those actions occur.

Roll back to a previously verified artifact and configuration, preserve affected transcript backups, rotate any credential proven exposed, and rerun the relevant boundary checks before resuming. Do not publish or deploy as part of this documentation review.
