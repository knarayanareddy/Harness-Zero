# Harness-Zero Architecture: the session log

The session log is what happened. The model's next request, the timeline after a page refresh, the search
index, the compaction summary — every one of them is a projection of `sessions/<id>/transcript.jsonl`, and
none of them owns a copy of the conversation. This document states the invariants that make that claim
checkable, the record vocabulary, and the upgrade policy.

The generated table of kinds lives in [`catalog/RECORD_KINDS.md`](../catalog/RECORD_KINDS.md) (rebuilt by
`harness catalog gen`, freshness-checked by `harness catalog --check`). The plugin-facing protocols are in
[`architecture/CONTRACTS.md`](CONTRACTS.md); §5 of [`../ARCHITECTURE.md`](../ARCHITECTURE.md) is the
narrative version of this page.

---

## 1. Invariants

| # | Claim | Enforced by | What it costs if it slips |
|---|---|---|---|
| 1 | **model-visible ⇒ logged.** Every fact the model sees, and every fact the user must see to understand the turn, is one appended record: prompt, assistant message (text + thinking + tool calls), policy decision, approval asked and answered, tool result, card, compaction summary, system notice, turn marker. | `tests/kernel/test_log_is_source.py::test_every_model_request_is_derivable_from_the_log`; scenario `reload_equals_live_web_dom` | Anything outside the log is invisible after a restart, a session switch, or a second client. This was the v0.2.1 bug: history showed tool results without the diff cards or the approval that preceded them. |
| 2 | **append before the fact.** The record is written, *then* the event is announced / the tool executes. | `runner.py:626` (message records), `gate.py:333` `_record` (decisions, cards, approvals), `approvals.py:59` `_record` | An announced-but-unlogged action leaves a client rendering a fact the next model request will not contain, and a crash loses it. |
| 3 | **append failure stops the turn.** `TranscriptAppendError` → `turn.error` with `metadata.reason = "transcript_append_failed"`, provider never called. | `runner.py:779` `_fail_closed`, `::test_a_store_that_cannot_append_stops_the_turn` | Continuing would run a model call against a conversation that exists only in memory, and the turn would look normal until someone reloaded and found half of it missing. |
| 4 | **one projection to the model.** `derive_messages(records)` is the only producer of `ModelRequest.messages`; it is pure (no settings, no I/O, no clock). | `context.py:186`, `tests/kernel/test_derive_messages.py` | Two producers means the golden tests and the live turn disagree, and "what the model saw" becomes unanswerable. |
| 5 | **reload = replay.** The client renders `sessions.get`'s `records[]` through the same kind switch it uses for live events; nothing is reconstructed from deltas at reload time. | `app.js:1171` `renderRecord(rec)`; scenario `reload_equals_live_web_dom` (`reload_equals_live` + `dom_reload_contains_live_lines`) | A refreshed page that shows less than the live one is a client that secretly keeps its own state. |
| 6 | **no secrets, no raw tool output.** Redaction and pruning run *before* the `tool.result` record is appended, so the redaction is not a display filter. | gate step 9 → `runner.py:614-626`; scenarios `secret_redacted_from_model_kernel`, `huge_tool_output_pruned_and_spilled` | The log is copied, grepped, backed up and indexed; it must be safe to read. |
| 7 | **records are never rewritten.** A v1 line is upgraded *on read* and left alone on disk, and `compaction` only slices the derived view. | `upgrade_record_v1`, `slice_at_last_compaction`, `tests/kernel/test_record_upgrade_v1.py` | The log is the audit trail; a projection that mutates it destroys the only evidence of what the model was told. |

`step_index` is a record's ordinal: assigned by the store on append, monotonic per session, and the cursor
`since_step` readers use. It is not a primary key across sessions.

---

## 2. Where the code lives

| File | Role |
|---|---|
| `harness/sdk/types.py:200-330` | `RECORD_KINDS` (the vocabulary), `TranscriptRecord` (`kind`, `v: 2`), `infer_record_kind` |
| `harness/kernel/transcript.py` | `TranscriptLog` — one instance per session directory, `append`/`read`/`tail_cache`, `RECORD_VERSION`, `upgrade_record_v1`, `MESSAGE_KINDS`, `dangling_tool_calls` / `repair_dangling_tool_calls`, `TranscriptAppendError` |
| `harness/kernel/context.py:107-260` | `MODEL_VISIBLE_KINDS`, `slice_at_last_compaction`, `repair_dangling_tool_calls`, `derive_messages` |
| `harness/kernel/runner.py` | log-before-execute ordering, `_append_marker`, `_fail_closed`, the deferred `model.completed` |
| `harness/kernel/gate.py:333`, `harness/kernel/approvals.py:59` | the two places that write *policy* records, and return the stored dict so the event carries the same object |
| `harness/kernel/session.py:205` | `Session.get_history()` reads through the log (so a v1 file and a fresh v2 record arrive in one shape) |
| `harness/kernel/commands.py` | `sessions.get` → `{records: [...]}` |
| `harness/plugins/stores/jsonl_transcript/` | the store: full log + compact index, `step_index` numbering, byte offsets |
| `harness/plugins/stores/fts5_search/` | the search index — derived, rebuildable (`record_row` is the shared projection) |
| `harness/frontends/web/static/app.js:1147-1230` | `reloadTranscript` → `renderRecord`, the client's single kind switch |
| `harness/kernel/catalog.py` (`RECORD_KINDS.md`) | the generated kind table |

---

## 3. What a record looks like

One JSON object per line. Payload fields stay flat so `grep` works on the log:

```json
{"v": 2, "kind": "tool.decision", "role": "system", "content": "Tool `write_to_file` needs approval: …",
 "call_id": "call#1", "step_index": 3, "timestamp": 1760000000.0,
 "metadata": {"action": "ask", "call_id": "call#1", "plugin": "fs-tools", "policy": "permission_modes",
              "risk": "write", "reason": "…"}}
```

* `kind` is required in v2 and either declared or inferred: `TranscriptRecord.__post_init__` calls
  `infer_record_kind(role, tool_calls, tool_results, metadata)` when it is empty, so a writer that only
  knows the v1 shapes lands in the same vocabulary. An unknown `kind` raises at construction.
* The payload bags (`tool_calls`, `tool_results`, `media`, `metadata`, `thinking`) are **omitted when
  empty** by `to_dict()`. That is load-bearing: idempotent upgrade and "reload equals live" are both
  dictionary comparisons, and a writer that defaults `tool_calls: []` breaks them.
* `user.message` / `assistant.message` carry the conversation; `tool.result` is a user-role record carrying
  `tool_results` — the role that the provider protocol requires, the kind that says what it actually is.
* Decisions, approvals, cards and turn markers are records *about* the conversation. `MESSAGE_KINDS`
  (`== MODEL_VISIBLE_KINDS`, pinned equal in `tests/kernel/test_derive_messages.py:237`) is the filter that
  keeps them out of the next request while keeping them in the timeline. A `system.notice` reaches the model
  only if its metadata says `model_visible: true`.

---

## 4. Order on disk for one step

```
prompt               → user.message         → turn.started (record attached)
model stream done    → assistant.message    → model.completed (deferred until the record exists)
  per tool call      → tool.decision        → tool.decision event
    if ask           → approval.requested   → approval.required
                     → approval.resolved    → approval.resolved
                     → tool runs; hooks, prune, redaction
                     → tool.result          → tool.result (paired by call_id, not carried)
    card from a tool → ui.card              → ui.card
turn end             → turn.completed | turn.error | turn.cancelled (best-effort marker)
```

Two orders are deliberate and surprising:

* the **decision is recorded before it is announced**, so a client that renders the approval cannot be
  ahead of the log that justifies it;
* `model.completed` waits for its record (`flush_pending_model_completed`) so a live view and a reload
  agree on which assistant message the event describes.

Turn markers are appended *best effort* at the end of a turn (`_append_marker`): the work they summarise is
already logged, so failing to write a summary line logs a warning rather than destroying a finished turn.
Mid-turn records are not best effort — see invariant 3.

`tool.call` and `model.tool_call` are **live-only**: the tool-call fact is inside the `assistant.message`
record that produced it, so a second record would be a second source. This is the one place the design
diverges from the porting plan, and it is recorded in `review/CONSOLIDATION_LOG.md`.

---

## 5. Durability: three kinds of event

Declared on the payload classes (`harness/sdk/events.py`), exported to
[`../events.schema.json`](../events.schema.json) as `durable` / `record_kind` / `transient` by
`harness catalog gen`. `durable` is *derived* from `record_kind`, so a doc cannot claim durability the
payload does not:

* **durable and carried** — the event's `record` field *is* the stored dict: `compaction.completed` →
  `compaction.summary`, `turn.started` →
  `user.message`, `model.completed` → `assistant.message`, `tool.decision`, `ui.card`, `approval.required` →
  `approval.requested`, `approval.resolved`, `turn.completed` / `turn.error` / `turn.cancelled`.
* **durable, paired by id** — `tool.result`: the record is written before its paired event, but
  the payload carries a call ID rather than a copy,
  because the tool output is too large to mirror on the bus.
* **transient** — `model.text.delta`, `model.thinking.delta`, `tool.output.delta`, `tool.result.pruned`,
  `tool.repeat_detected`, `cost.updated`, `context.assembled`, `compaction.started`, `model.started`,
  `session.*`, `plugin.*`, `settings.changed`, `hook.fired`, `task.*`. Progress, not history: a reload does
  not replay deltas, it renders the message they added up to.

---

## 6. Versioning and upgrade

New compaction records use `metadata.covers_through_step` to identify the exact
summarized prefix. The summary's physical append may follow the current user
prompt: `derive_messages` retains uncovered records in their original order, so
that prompt and outstanding calls are not lost. Legacy summaries without the
coverage field retain their physical-boundary slicing behavior. Raw history is
never truncated. When a text prompt itself exceeds the available budget,
`metadata.summarized_prompt_step` explicitly identifies its preserved raw record;
assembly uses the summary rather than appending the huge prompt a second time.
Unresolved calls and current image attachments cannot use that text-only fallback.
See [T10 evidence](../testing/T10_DURABLE_COMPACTION.md).

`RECORD_VERSION = 2`. A line without `kind` (or with `v: 1`) is upgraded on read by `upgrade_record_v1`:

| v1 shape | v2 |
|---|---|
| `role: user`, no `tool_results` | `user.message` |
| `role: assistant` | `assistant.message` (`tool_calls` kept, `tool_results` split out) |
| `role: system` / `type: COMPACTION_SUMMARY` | `system.notice` / `compaction.summary` |
| `tool_results` inside an assistant line | separate `tool.result` records |
| `type`/`source` instead of a role | mapped by `_ROLE_FROM_LEGACY_TYPE` |

The upgrade is idempotent and never written back: the file keeps whatever the writer wrote, and readers do
the work. `is_v2(data)` is how the log decides to skip it. That is what lets the store plugin stay a
plugin — the format version lives with the record, not with a migration step.

**A dangling tool call is repaired by appending, not by patching memory.** If a session was killed between
the assistant record with a `tool_calls` entry and the matching `tool.result`, `derive_messages` would hand
a provider a `tool_use` with no `tool_result` (Anthropic answers 400), which makes a crashed turn
permanent. `TranscriptLog.repair_dangling_tool_calls` appends a real `tool.result` record marked
`metadata.synthetic: true, is_error: true` on resume, so the fix is visible in the log (`transcript.py:276`).

---

## 7. The client renders the log

`reloadTranscript()` fetches `sessions.get` and calls `renderRecord(rec)` for each record; live events
paint through the same methods. The mapping (`app.js:1171`):

| Record kind | Painter | Notes |
|---|---|---|
| `user.message` | `appendUserTurn` | |
| `assistant.message` | `appendTurnBlock` + `streamAssistantText` + `finalizeStreamingBlock` | the text arrives whole, so it is painted without a caret |
| `assistant.message.tool_calls[]` | `appendToolCall` | appended as `running`, then updated by its own `tool.result`/`tool.decision` — a call whose result never came stays visibly unfinished |
| `tool.result` | `updateToolResult` | matched by `call_id` |
| `tool.decision` | `updateToolDecision` | badge, reason, and the `plugin · risk` subtitle the live view got from `tool.call` |
| `ui.card` | `appendCard` | cards a tool emitted (`📝 diff`, `📄 skill playbook`, …) survive a refresh |
| `approval.requested` / `approval.resolved` | `showApproval` / `resolveApprovalUI` | |
| `compaction.summary` | `appendCard` as markdown | |
| `turn.error` / `turn.cancelled` | `appendNotice` | |
| `system.notice` | `appendNotice` | |
| `turn.started` / `turn.completed` / `checkpoint.created` / `job.*` | — | bookkeeping; the timeline does not need a line per turn boundary |

Two honest differences between live and reloaded, both accepted:

* **order.** Live paints in *event* order; a reload paints in *record* order, and one assistant record holds
  a step's whole batch of calls, so sibling tool cards land together and the approval card after them. The
  scenario asserts every line the live view showed is present, not that they are in the same positions.
* **no chrome.** The streaming caret, the "idle" badge and the running spinner are live state; a reload has
  nothing to animate.

`tests/frontends/ui_audit.py --strict` keeps the client's `data-*` bindings and card kinds honest;
`docs/catalog/UI_CONTRACT.md` lists events that have no client renderer.

---

## 8. Derived indexes

`harness sessions reindex [--workspace PATH] [--dry-run]` rebuilds the FTS5 search table from the log
(`harness/plugins/stores/fts5_search/index.py:rebuild_from_sessions`). It is safe at any time precisely
because the index is derived: rows are deleted and re-projected from `transcript.jsonl`, and
`conversation_summaries` step counts are recounted. Run it after a crash, a partial write, or a record
format upgrade. `FTS5SearchIndex`'s live hook and the rebuild walk share `record_row`, so a session indexed
while it ran and a workspace reindexed afterwards produce identical rows — the test that pins this is
`tests/kernel/test_log_is_source.py::test_the_search_index_is_rebuildable_from_the_log`.

---

## 9. Deliberately not here

* **No SQLite transcript.** JSONL stays the format: greppable, append-only, diffable in a golden file. The
  FTS5 table is an index, not a store, and is rebuildable (§8).
* **No telemetry, no remote upload, no `session-log-deepseek`.** The upstream project ships a log-export
  plugin; this repo's stance is that the log stays in the workspace (`docs/research/` has the analysis).
* **`derive_messages` stays pure.** No settings, no I/O, no clock — otherwise the goldens cannot call it
  offline and invariant 4 is not testable.
* **Compaction never deletes.** `compaction.summary` is appended and `slice_at_last_compaction` hides the
  prefix from the derived view; `read(compact=True)` is the same view, not a different file.
* **Not scoped yet:** `checkpoint.created` records for the checkpoints hook (§3.6 of the porting plan) and
  the reserved `job.*` / `goal.*` kinds, which arrive with WS-10. `settings.changed` is declared so the
  vocabulary matches the plan; nothing writes it yet, which the generated table shows as
  "0 scenario transcripts holding it".

---

## 10. Keeping this document true

```bash
python -m pytest tests/kernel/test_log_is_source.py tests/kernel/test_derive_messages.py \
                 tests/kernel/test_record_upgrade_v1.py tests/kernel/test_durable_events.py -q
python -m pytest tests/scenarios -q -k "reload or session or approval"   # DOM lane: needs `npm ci`
harness catalog gen && harness catalog --check        # RECORD_KINDS.md / events.schema.json freshness
grep -rn "transcript_records" harness/ | wc -l        # 0 — no parallel in-memory conversation
```

Adding a record kind means, in this order: `RECORD_KINDS` and (if it is a message) `infer_record_kind`; a
painter case in `renderRecord`; a row in §7; a durable event whose payload declares `record_kind`, or an
explicit note that it stays live-only; and a scenario that reloads it. The catalogue table and the
model-visible flag follow from the code — do not hand-edit `docs/catalog/RECORD_KINDS.md`.

## 11. T03 acceptance addendum: durability and recovery limits

This addendum qualifies earlier implementation claims. The reviewed baseline
does **not** satisfy every listed invariant: R15 blocks compaction, R16 loses a
post-crash append, and R17 aborts multi-call durable repair. Tests and source
anchors are recorded in `../testing/REVIEW_REGRESSIONS.md`. “Durable” in the
event catalogue identifies intended persisted facts; it is not a certificate
that all runtime paths or power-loss handling have been verified.

### Acknowledgement boundary (BC-10)

The initial release target is process-crash/restart durability on the same
filesystem. After store append returns successfully, the record must be readable
after reconstructing store/log objects; both full and compact writes must have
succeeded. Existing buffered writes use `flush()` without `fsync()`, so sudden
power loss, device failure and multi-process atomicity are **not guaranteed**.
No documentation may claim those guarantees without a separate design and fault
test. A partial dual-log write must fail closed, preserve damaged bytes and not
consume the first subsequently acknowledged record during recovery.

Store step indices remain monotonic within one session, recovered from valid
persisted indices, not the number of nonempty/corrupt physical lines. Corrupt
tails are preserved/quarantined explicitly; normal committed history is not
rewritten. The repair operation itself needs a recoverable interruption boundary.

### Interrupted tool calls

Repair every unanswered call, including multiple calls in one assistant record.
Append synthetic error results, return the actual appended records and make a
second repair pass a no-op. The record must say **outcome uncertain** where the
process may have acted before crashing. Never retry/re-execute such work merely
to fill a missing result. Pure `derive_messages` may repair a request view, but
that does not close an on-disk recovery defect.

### Compaction (BC-09)

Build a valid summarizer request; invoke actual hooks; append the summary before
announcing successful completion; rebuild the next request from the persisted
summary and retained suffix. Keep current objective/constraints and unanswered
tool dependencies, do not duplicate the newest prompt, and do not delete raw
history. Failed/cancelled summarization must not announce compaction success or
discard usable context. Required proof crosses the real threshold, inspects the
next actual request, restarts, and repeats compaction.

### Reload and session state (BC-05)

Record equality is not enough to establish correct session ownership. Every
history response/live event must still belong to the selected session/generation.
Pending approvals, questions, active turns and uncertain completion must be
reconciled after refresh/reconnect. The live-only caret is disposable; an active
turn's cancellability or an unanswered approval is not merely cosmetic chrome.

Numerical fault/cancellation/soak gates are in root `CONSTRAINTS.md`; their
acceptance owners are T05–T10 and T23/T24/T29, not marked passed by this addendum.
