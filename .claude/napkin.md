# Napkin Runbook

## Execution and validation
1. **[2026-10-23] An unread subprocess pipe can deadlock a healthy stdio peer.**
   Do instead: drain stderr concurrently and drain remaining stdout during owned-child shutdown within the original deadline; require zero exit and retain a pipe-capacity regression. Do not attribute every close timeout to the product or host load.
1. **[2026-10-22] A tool-completion deadline does not bound transport shutdown.**
   Do instead: include response flushing in the remaining EOF budget, bound ordinary stalled writes, and test both half-closed short reads and active-work cleanup. Run original CLI probes as well as new subsets; publish fixture readiness atomically after installing signal handlers.
1. **[2026-10-22] Per-event type-hint resolution can dominate high-volume output.**
   Do instead: cache bounded static declarations by class, not payloads or validation verdicts; keep malformed/missing/extra-field checks on every event and deterministic noisy-output controls.
1. **[2026-10-22] A failed Seatbelt probe does not establish that the host forbids profiles.**
   Do instead: compare the actual profile against a harmless allow-default `/usr/bin/true` control and inspect the exit status. Current dyld requires `file-read-data (literal "/")`; do not replace that exact-directory rule with global reads or `(subpath "/")`. Retain native outside-file denial controls.
1. **[2026-10-21] An append receipt is not the cache's last record after an await.**
   Do instead: resolve the returned step with `record_at_step`; keep interleaved-notice controls. Owned filesystem commits add scheduling points, so also consume queued completions after their processes have already exited.
1. **[2026-10-21] Numeric process-group existence is not a stable identity token.**
   Do instead: fail closed on unconfirmed ownership after leader exit; retain both redirected-child and synthetic reused-group regressions. Keep the live supervisor identity anchor; see accepted ADR 0002.
1. **[2026-10-21] Shielding cancellation work is not enough to own its lifecycle.**
   Do instead: retain a strong cleanup-task reference and the request until the cleanup worker's own finally releases it; guard already-settled futures while still publishing terminal facts. Keep repeated requester-cancel/held-lock regressions; do not rely on callback ordering.
1. **[2026-10-20] Search confinement is enforced by acquired descriptors, not resolve-then-open.**
   Do instead: retain `dir_fd` + `O_NOFOLLOW` for every child; preserve ignored explicit roots and regular-file/non-blocking checks. Keep deterministic leaf/parent/root swap tests and inside controls; never reintroduce a pathname subprocess fallback.
1. **[2026-10-20] Match the baseline CI toolchain before diagnosing product failures.**
   Do instead: use clean Python 3.11/3.12 and Node 22; prepend the venv bin for child `python`; run `scripts/verify_environment.py`. Do not vendor the prior Homebrew 3.13 workarounds.
2. **[2026-10-20] Keep known-defect evidence distinct from functional passes.**
   Do instead: run `pytest review/regressions` explicitly and retain its 19 red reasons; use `docs/testing/review_regressions.json` for owners. Default tests exclude that diagnostic directory; release qualification still requires closure.

## Paths and environment
1. **[2026-10-24] Temporary Python bases can become unusable while venv links survive.**
   Do instead: keep qualification toolchains in durable ignored project storage. After interruption, verify `encodings` and `pytest` before tests; retain damaged bases, recover exact patch versions, and refresh affected checks rather than counting codec/setup failures as product failures.
1. **[2026-10-23] Shorthand evidence paths and system Python lose verification provenance.**
   Do instead: pass absolute ignored evidence/base paths verbatim and invoke the actual `.venv/py311/bin/python` or `.venv/py312/bin/python`. Verify a claimed missing base directly; `No module named pytest` is a setup failure, never behavioral evidence. Preserve misplaced owned logs by hash before relocating them.

## Qualification evidence
1. **[2026-10-25] Source probes can leave ignored bytecode that invalidates bundle-input checks.**
   Do instead: keep the clean artifact source separate from operational/SDK test roots; run frozen resource/browser binding before importing that source. Preserve caches rather than deleting unknown files or weakening the physical input guard.
1. **[2026-10-24] A listed-file snapshot verifier does not detect additional build inputs.**
   Do instead: compare complete path sets, modes and hashes before/after builds, including ignored files in recursively bundled resource trees; stop on additions rather than deleting unknown files.
2. **[2026-10-24] Serial scenario lanes share their `_last_run` directories.**
   Do instead: archive each lane's outputs before the next lane runs; never present later passing output as the raw evidence for an earlier failed lane.
3. **[2026-10-24] Approval details contain validated canonical paths.**
   Do instead: compare the resolved exact owned target and allowed content, not a relative-path spelling; retain negative outside-target and wrong-content controls.

## Browser verification
1. **[2026-10-24] Canonical tool identity is not an invented event-record field.**
   Do instead: read calls from assistant records' `tool_calls[].id` and results from `tool_results[].tool_use_id`. Inspect saved real records before retrying a failed predicate; ordinary USER-carried tool results have empty `message.text()` and are not a new prompt.
2. **[2026-10-24] Harness errors must not erase completed stages or look like product defects.**
   Do instead: enforce preflight success inside the runner before launching children, save each completed stage immediately, and resume only unfinished stages. Use DOM text for semantic assertions, computed/rendered values for CSS checks, and preserve failed attempts by hash before correcting fixtures.

## Scope and reporting
1. **[2026-10-22] Resumed Luna sessions can exceed token-rate limits despite narrow prompts.**
   Do instead: archive partial allowed-file changes/evidence, then use a fresh bounded-context child to validate and finish that candidate; do not restart completed work, loop retries, or count a rate-limited run as completion.
1. **[2026-10-22] Zide-derived workflow requirements are process, not product delivery.**
   Do instead: follow `tasks/WORKFLOW.md`, bind reviews/tests to the exact candidate and recheck the destination; keep runtime ZIDE-01–04 acceptance pending until its owning tasks pass.
1. **[2026-10-20] Baseline contracts are requirements, not completed repairs.**
   Do instead: report targets and current violations separately; consult `CONSTRAINTS.md` and `docs/testing/VERIFICATION_MATRIX.md`; never claim native/live/artifact evidence from mocks or source inspection.
