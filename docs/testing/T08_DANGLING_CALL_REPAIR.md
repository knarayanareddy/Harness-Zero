# T08 — durable dangling-call repair

Status: locally verified; global/native qualification remains incomplete.

The first repair previously persisted a synthetic result and then raised
`AttributeError` by accessing nonexistent `TranscriptLog._records`. Later calls
were left unanswered. Repair now resolves each append's returned step through
the canonical `records` projection, including when an unrelated notice appends
before the caller resumes. No tool is executed by recovery. Results remain
explicitly synthetic errors that warn the user to check whether the tool ran.

## Verification

- `t08-red.xml`: real bootstrapped session with two unanswered calls reproduces
  the original AttributeError after the first synthetic result.
- `t08-py312.xml`: **44 passed in 48.71s**; `t08-py311.xml`: **44 passed in
  25.07s**. Scope: durable events, crash recovery, source-of-truth and ledger tests.
- `t08-r17.xml`: original R17 probe passes; 18 unrelated probes deselected.
- Permanent regressions in `tests/kernel/test_durable_events.py` check both
  returned records, both persisted logs, a second no-op repair, interleaved
  receipt selection, and a simulated write interruption between the first and
  second repair followed by a genuinely reconstructed store/log. Restart writes
  only the second missing result; neither ID receives duplicate repair records.
- Existing runner/provider regression checks that the resumed model request
  includes the synthetic result. Recovery has no executor/handler dependency.
- Ruff and `git diff --check` pass. No test bound, golden or assertion weakened.

Evidence is local under ignored `.venv/evidence/`. The between-results failure
is deterministic storage fault injection, not an independently killed engine.
T07 separately exercises real killed-writer restart. No concurrent recovery
writers, power-loss durability, full qualification or remote delivery is claimed.
