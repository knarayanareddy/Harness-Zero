# QA32 — descriptor-instrumented endurance preparation

**Status:** in-review; preparation only, no new soak authorized
**Owner:** qualification integration owner
**Date:** 2026-11-02
**Code base:** `f3129d00dfc2ca67cd95d425a45b627e66075edb`

## Problem and decision

The completed T29 run recorded 120 turns over 7,226.308 seconds with no integrity
errors, pending jobs or interactions. Its original evaluator remains
`review-required` / `passed=false`: aggregate FD count rose from 16 to 17 after
the first turn and then stayed flat. No per-descriptor identities were captured.
Bounded same-source and SQLite-only diagnostics reproduce one retained database
descriptor that is reused and released on owner teardown. Independent review
supports that narrow explanation, but cannot identify the original soak's FD.

The owner selected **Prepare stronger soak**, rather than accepting the limited
resource explanation. Prepare stronger evidence and reviewed execution commands;
do not launch a new two-hour run as part of this task.

## Scope and non-goals

Implementation allowlist: `scripts/qualify_release.py` and new
`tests/reliability/test_fd_evidence.py`, in an isolated worktree. Parent owns this
spec and `tasks/plan.md` / `tasks/todo.md`. Preserve all existing tests and raw
evidence, the seven untracked filmmaking files, and the published checkpoint.

No kernel/store/provider repair, dependency addition, baseline warm-up/reset,
threshold change, shortened soak, restarter, credential use or OS setting change.
Do not alter the meaning of the existing `fd_count`, RSS or child metrics.
Do not convert the old evaluator result to green. No commit/push is authorized
merely by preparing this new candidate.

## Additive collection mode

Add explicit diagnostic CLI opt-in `--fd-evidence`. Legacy/default execution and
the existing `evaluate_soak` contract remain unchanged. The stronger invocation
must propagate the option from parent to worker, bind it in start receipts, and
reject any parent/worker disagreement or silent downgrade.

In enabled mode, collect a descriptor table for every existing cadence sample,
immediately after the first acknowledged turn and session read, and after actual
plugin unload but before worker exit. The real 120-turn run therefore retains
121 cadence snapshots plus the two lifecycle captures. Keep the first baseline
before the first turn. Do not move the baseline past SQLite initialization.

Keep the original aggregate sampler unchanged. Collect identities separately
with the host's verified `lsof -nP -p <own-worker-pid> -F0ftnDi` format: process
header, descriptor, type, name, device and inode where supplied. Preserve numeric
FDs separately from nonnumeric `cwd`/`txt`/other records; do not reinterpret those
pseudo-records as numeric FDs. Missing optional device/inode data must be explicit.
Names are untrusted data, never paths to open or commands to execute.

The collector targets only its own worker PID. Preserve raw bytes losslessly
alongside safe decoded fields, so NULs, newlines or non-UTF-8 names cannot forge
record boundaries. The exact host format has been probed and retained locally
under `.venv/evidence/qa32-fd-evidence-preparation-20261102/`.

Use the existing ten-second sampling ceiling for the entire helper lifecycle,
including stop/reap. Reserve cleanup time inside that budget. Read stdout/stderr
incrementally: buffer at most 1 MiB of stdout and 64 KiB of stderr, using bounded
chunks and at most one extra byte to detect overflow. A post-communicate size
check alone is insufficient. Limit parsed tables to 4,096 descriptor records;
on overflow/timeout stop the exactly owned helper, reap within the remaining
budget and fail explicitly. Unconfirmed cleanup is a collection failure, not
success. Do not return an empty/zero table on failure. Document collector-created
transient pipes or other observer effects; do not claim perfect transparency.

## Durable evidence and validation

Write each capture promptly to a new, private, exclusive file beneath the owned
run output, with predictable relative names, phase/index, PID, monotonic capture
time, byte count and SHA-256. Close the artifact writer between captures; do not
retain open output handles or accumulate all raw tables in memory for two hours.
Report small references to the captures. Do not upload raw tables automatically.

Each capture is a JSON envelope with schema `t29.fd-evidence.v1`, phase/index,
worker PID, capture start/end monotonic times, raw stdout encoded as base64,
raw-byte count/SHA-256, and parsed records. Encode the envelope as compact,
sorted-key, ASCII JSON followed by one newline. The report reference hashes
**all exact serialized envelope bytes, including that newline**, and records
their length. Cap the serialized file at 4 MiB. Decode names safely for display
while retaining exact raw bytes; validation reparses raw bytes and compares the
records rather than trusting the decoded table alone. Reject duplicate JSON
keys. Readback must enforce limits while reading, not after unbounded allocation.

The existing `start-receipt.json` precedes worker launch. Add a separate private,
exclusive `worker-launch-receipt.json` immediately after `Popen` returns, using
the parent's actual `Popen.pid`, parent PID, expected argv, source pin and enabled
flag. The parent retains that handle/PID for terminal validation. Do not require
the child to wait for this later file: it validates the existing prelaunch
receipt/flag and reports its own PID; the parent later compares its independently
observed PID with the launch receipt, report and every table's raw process header.
Any discrepancy fails. No new polling or startup race is introduced by this file.

Add a separate FD-evidence validator; do not relax or replace `evaluate_soak`.
When the opt-in is enabled, the actual parent/controller terminal path must run
this validator and cannot report success if evidence is missing, incomplete,
foreign, path-escaped, symlinked, corrupted or inconsistent with sample coverage.
Bind every table to the actual launched worker PID. Missing post-unload evidence
is a failed evidence gate, not proof that teardown released a descriptor.

Successful evidence capture is not acceptance of resource growth. The original
resource evaluator can still require review, even when all new evidence is valid.

The after-unload capture is a lifecycle postlude, not an extension of the original
measured soak interval. Keep existing start/end duration fields unchanged and
validate this capture against its explicit lifecycle timing/phase instead.

## Testable acceptance

- [ ] New parser tests fail first, then accept the captured native format and
  preserve delimiter-sensitive names, numeric/pseudo separation and optional
  identity fields; reject malformed/truncated/foreign/duplicate-numeric input.
- [ ] Timeout, helper failure and byte/record overflow fail explicitly and reap
  the exactly owned helper; no arbitrary PID/group signals or new suppressions.
- [ ] Exclusive artifact writing and bounded readback reject symlink/path escape,
  duplicate filenames, wrong hashes, missing phases and inconsistent coverage.
- [ ] An actual startup-only kernel/tool/unload control retains real descriptor
  evidence without claiming a shortened test is endurance. A separate negative
  test proves the controller cannot bypass the enabled evidence validator.
- [ ] Existing timing, replay, integrity, ownership, error and resource assertions
  remain intact. New source/control files pass Ruff and relevant tests on both
  Python 3.11.17 and 3.12.15. Unsupported native capture tiers remain explicit.
- [ ] Independent review binds the exact source/base/digest. Fresh serial source
  gates pass on the final diagnostic candidate before a new soak is proposed.
- [ ] Prepare a fresh clean manifest-bound execution tree and one-shot command
  with the opt-in, unchanged >=7,200 seconds, 120 turns, 60-second cadence and
  8,100-second worker ceiling. Do not start it without the owner's next approval.

## Open decisions and rollback

Confirm remaining nested reference field spellings in the scoped implementation
review without changing the byte/hash scope above. Record provenance/observer
overhead and any platform
limitations before execution. No numeric resource-growth tolerance is introduced.
Default/legacy runs remain available without the opt-in, but cannot be presented
as descriptor-instrumented evidence. Retain old artifacts on rollback; never
delete an earlier run to make the new preflight or evaluator pass.
