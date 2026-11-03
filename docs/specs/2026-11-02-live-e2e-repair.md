# QA33 — repair live provider defaults and compaction boundary

Owner requests fixing the end-to-end failures established in
`.venv/evidence/e2e-stress-20261102/REPORT.md`, not merely repeating them.
Base: `f3129d00dfc2ca67cd95d425a45b627e66075edb` plus preserved QA32 work.
Scoped before-images are recorded in
`.venv/evidence/qa33-live-e2e-repair-20261102/base.json`.

## Writable scope and exclusions

- Runtime: `harness/plugins/providers/openai_compat/provider.py`,
  `harness/kernel/compaction.py`, `harness/kernel/runner.py` (the latter added
  after the strict HTTP test proved transient retryability was lost; before-image
  and evidence are recorded in `timeout-scope-amendment.json`), and
  `harness/kernel/context.py` (added after unrestricted artifact testing proved
  oversized active-request loss; `active-request-scope-amendment.json`).
- Tests: `tests/plugins/test_openai_compat.py`,
  `tests/kernel/test_compaction_through_kernel.py`,
  `tests/kernel/test_openai_wire_roundtrip.py`; new scoped regression modules
  if needed rather than unrelated test changes.
- Documentation: this spec, provider README, `tasks/plan.md`, `tasks/todo.md`,
  ignored private evidence and isolated test/build runtime directories.
- Preserve existing QA32 runner/tests and seven excluded filmmaking files.
  No commit, push, release, OS security changes, broader provider credentials,
  architecture replacement or official two-hour soak follows from this task.

## Acceptance

1. The native OpenAI route for the two tested exact model IDs defaults to the
   demonstrated compatible Chat Completions reasoning mode. It sends native
   completion-token limits rather than rejected legacy `max_tokens`. Defaults
   must not mutate SDK requests or alter legacy/third-party provider semantics.
   Conflicting explicit incompatible settings fail locally and descriptively;
   no silent model substitution or reasoning downgrade.
2. Compaction presents prior messages and tool content as structured historical
   data, not active commands. Preserve exact user/tool text and tool-result error
   provenance. Explicitly instruct the summarizer to ignore instructions embedded
   in that data. Retain all four required sections, persistence-before-completion,
   original budget checks, cancellation and failure semantics.
   When an oversized active prompt itself is summarized, retain an explicitly
   marked, bounded, exact head/tail excerpt as a durable derived user message.
   Include it in projected-budget validation before committing; keep the original
   full prompt untouched, reconstruct the excerpt after reload, and never promote
   it ahead of a later actual user request. This is a lossy context excerpt, not
   a claim to preserve every detail of arbitrarily oversized prompts.
3. Prove timeout recovery with an actual strict HTTP peer: a stream interrupted
   after partial output/tool arguments cannot execute uncertain tools or become
   a completed turn; close the response, release session ownership, preserve
   typed failure and allow the next explicit turn. No automatic replay, increased
   deadline or guaranteed remote-availability claim. Investigate the saved live
   timeout; leave its original cause unknown unless evidence establishes it.
   Preserve provider retryability only before admitted tool work; a later failure
   must not mark an already effectful whole turn safe for replay.
4. Fail-first regression per slice, positive/negative controls, affected tests
   and Ruff on Python 3.11/3.12; serial full-source gates on final source.
5. Build a new non-editable wheel from the final coding-only snapshot. Test the
   default public ACP CLI with both models without injecting provider params;
   repeat the retained hostile-history compaction probe and affected task-driven
   stress against that artifact. Keep original failed evidence intact. No prior
   full source/live/artifact result qualifies changed bytes automatically.

Official model reference: <https://developers.openai.com/api/docs/models/gpt-6-luna>.
Runtime reference for both exact IDs: retained provider HTTP 400 errors and
configured successful live requests in the original private evidence.

Prompt hardening is not a general proof against all prompt injection. Live
regressions must test the actual saved history that defeated the original prompt.
