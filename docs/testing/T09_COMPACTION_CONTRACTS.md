# T09 — actual hook/provider compaction contracts

Status: locally verified contract slice. At T09, R15 remained open for T10
persistence/consumption; subsequent [T10](T10_DURABLE_COMPACTION.md) supplies local
repair evidence. Global/native qualification remains non-green.

PRE_COMPACT now uses `get_hooks_for_event`, ordered `events` subscriptions and
`on(event, payload, context)` with each hook's deadline. Block/error/timeout stops
compaction with `CompactionError`; the runner publishes a non-retryable typed
`turn.error` (`compaction_error`), not a successful turn. Context includes the
actual settings and bus. The test fixture's obsolete singular event/execute API
was migrated without removing its execution assertion.

Summarizer requests have actual ContextBlock instructions and ModelParams. Their
total stream is bounded by existing `kernel.request_timeout_s`; empty text or a
stream without successful Done is rejected. Cancellation is not converted into
success. Summary persistence and cancellation-event wiring remain T10.

## Evidence (`.venv/evidence/`, ignored)

- `t09-hook-red.xml`: default hook set reproduces missing `.event`.
- `t09-params-red.xml`: after fixing hooks, required ModelRequest.params fails.
- `t09-failure-red.xml`: seven failure controls reproduce block bypass, untyped
  failures, unbounded provider wait and false completion of empty/incomplete text.
- `t09-py312.xml`: **34 passed in 20.88s**.
- `t09-py311.xml`: **34 passed in 32.08s**.
- Real bootstrapped kernel -> actual OpenAI-compatible adapter -> strict local
  TCP HTTP/SSE peer: valid summarizer request and second answer request observed.
  No vendor API, external service or paid credential is used.
- Hook block/error/timeout and provider error/timeout/empty/incomplete controls
  assert no `compaction.completed`, `turn.completed` or stored summary. The hung
  provider's cancellation finalizer is checked.
- Ruff and `git diff --check` pass.

An initial combined run (`t09-wire-green.xml`) failed the new four-second test
oracle during unrelated STOP checkpoint I/O, after compaction had already failed.
The retained four-second bound now measures compaction.started -> typed turn.error;
setup/STOP are not falsely included in a compaction-latency claim. Both final
cross-version subsets pass with the corrected observation point. Earlier
`t09-failure-green.xml` predates the strict TCP peer. No full-suite pass is claimed.
