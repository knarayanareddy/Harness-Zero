# WS-04 — The session log is the single source of truth

| | |
|---|---|
| Status | specified |
| Depends on | WS-01 (goldens prove equivalence); WS-03a (hook ordering) convenient but not required |
| Effort | 3 days |
| Principles | P1 (primary), P3 |
| dsh reference (@86a40220) | `packages/core/session-log/` (append-only, versioned `SessionEvent` union, `deriveMessages()`), `packages/core/session-log/README.md` ("model-visible ⇒ logged"; durable vs live events), `packages/features/checkpoints/` (checkpoint failure ⇒ model request does not run), `packages/core/agent-loop/` (log-before-execute: the tool call is logged before the tool runs; waterfalls after) |
| Harness-Zero touchpoints | `harness/kernel/runner.py:206,270,290,300,585-591,766-769` (in-memory `transcript_records` mirrored to the store; **store append failure is a warning and the turn continues**), `harness/kernel/context.py:90-125` (`records_to_messages` with compaction slicing), `harness/kernel/session.py:37-65,157` (`TranscriptStore.append/read/last_compaction`, `Session.get_history`), `harness/sdk/contracts.py:120-135` (`TranscriptStore` protocol, dict records), `harness/sdk/types.py:200-230` (`TranscriptRecord`: role/content/tool_calls/tool_results/thinking/media/timestamp/metadata/step_index — no record type, no version), `harness/kernel/gate.py:716-724` (`ui.card` published live only — **cards are not persisted**), `harness/kernel/commands.py:136-152` (`sessions.get` returns history from the store; the client re-renders from it), `harness/plugins/stores/jsonl_transcript/`, `docs/events.schema.json` (no durable/live distinction), web `static/app.js` (renders live events and, separately, `sessions.get` history → two renderers, two sources) |

## 1. Outcome

1. **One log.** `TranscriptRecord` becomes a typed, versioned union (`kind` field) appended **before** the thing it describes happens, for every model-visible or user-visible fact: user prompt, assistant message (text/thinking/tool calls), tool call *issued*, approval asked/resolved, tool result, card, compaction summary, system notice, turn started/completed/error, settings change that affects the model (mode/model switch), checkpoint created.
2. **`derive_messages(records)` is the only way** a `ModelRequest.messages` list is produced; the runner no longer keeps a parallel in-memory list that can diverge from disk.
3. **Append is fail-closed.** If the store cannot append, the step errors (`turn.error`, `reason: "transcript_append_failed"`) — nothing runs that is not logged.
4. **The UI renders the log.** Live SSE events carry the record they correspond to (`record` field) and `sessions.get` returns the same records; the client has one renderer per record kind (WS-05 makes that a registry). Reload, switch session and live view are pixel-identical in `timeline.txt` goldens.
5. **Durable vs live** is explicit in `docs/events.schema.json` (`durable: true|false`); every durable event type maps to a record kind.

## 2. Current state (verified)

- Two sources already: `services.transcript_records` (memory) and the store. They are kept in sync by convention; on append failure they diverge silently (`runner.py:588-591`).
- Records are untyped dicts (`role` + bags); there is no `kind`/`version`; approvals, decisions and cards exist only as bus events, so a reloaded session shows tool results without the diff cards or the approval that preceded them (confirmed in v0.2.1 review §3: history view differs from live view).
- `records_to_messages` handles compaction by slicing at the last summary record (`context.py:100-125`) — good; keep it, rename to `derive_messages`, and add the new kinds.
- `TranscriptStore` is a plugin (`stores/jsonl_transcript`), so the format change must be versioned (`v: 2`) with an on-read upgrader for v1 files.

## 3. Design

### 3.1 Record union (`harness/sdk/types.py`)

```python
RecordKind = Literal[
  "turn.started", "turn.completed", "turn.error", "turn.cancelled",
  "user.message", "assistant.message",            # assistant.message = text + thinking + tool_calls (one per model response)
  "tool.call",                                    # logged BEFORE execution (args as sent to the gate)
  "tool.decision",                                # policy chain result (allow/ask/deny, policy name, reason)
  "approval.requested", "approval.resolved",
  "tool.result",                                  # final (post-hooks, post-prune) content, metadata, is_error
  "ui.card",                                      # card payload, tied to call_id
  "compaction.summary", "system.notice",
  "settings.changed",                             # model-affecting keys only
  "checkpoint.created",
  "job.*", "goal.*",                              # reserved for WS-10
]

@dataclass(slots=True)
class TranscriptRecord:
    kind: RecordKind
    v: int = 2
    step_index: int | None = None
    turn_id: str = ""
    timestamp: float = field(default_factory=time.time)
    # payload fields used per kind (keep flat for JSONL greppability):
    role: str = ""; content: str = ""; thinking: str | None = None
    tool_calls: list[dict] = ...; call_id: str = ""; tool_name: str = ""; arguments: dict = ...
    is_error: bool = False; metadata: dict = ...; media: list[dict] = ...; card: dict | None = None
    decision: dict | None = None; approval: dict | None = None
```

`v1` dicts (`role` only) are upgraded on read by `upgrade_record_v1(d)` in `jsonl_transcript` (user/assistant/system → `user.message`/`assistant.message`/`system.notice`; `tool_results` inside an assistant record → separate `tool.result` records).

### 3.2 `derive_messages` (`harness/kernel/context.py`)

```python
def derive_messages(records: Iterable[TranscriptRecord]) -> list[Message]:
    """The ONLY producer of ModelRequest.messages. Pure. Deterministic. Tested by goldens."""
    recs = slice_at_last_compaction(records)              # existing behaviour
    out: list[Message] = []
    for r in recs:
        match r.kind:
            case "user.message": out.append(Message(role=USER, content=blocks_from(r)))
            case "assistant.message": out.append(Message(role=ASSISTANT, content=[*text_blocks(r), *tool_use_blocks(r.tool_calls)]))
            case "tool.result": out.append(merge_tool_result(out, ToolResultBlock(tool_use_id=r.call_id, content=blocks_from(r), is_error=r.is_error)))
            case "compaction.summary": out.append(Message(role=USER, content=[TextBlock(summary_text(r))], metadata={"compaction_summary": True}))
            case "system.notice" if r.metadata.get("model_visible"): out.append(Message(role=USER, content=[TextBlock(r.content)]))
            case _: pass                                    # decisions, approvals, cards, turn markers: user-visible, not model-visible
    return repair_dangling_tool_calls(out)                 # a tool.call without tool.result (crash) gets a synthetic error result — logged as such, not invented in memory
```

`repair_dangling_tool_calls` **appends a real `tool.result` record** (`is_error`, `metadata.synthetic=True`) on session resume rather than patching in memory — P1.

### 3.3 Runner flow (log-before-execute)

```
user prompt            → append user.message            → emit turn.started (record attached)
model response done    → append assistant.message       → emit model.completed
for each tool call     → append tool.call               → gate.run(call) [gate appends tool.decision, approval.*, tool.result, ui.card]
                                                       → emit tool.result (record attached)
compaction             → append compaction.summary
turn end               → append turn.completed|error
```

Appends go through `Session.log.append(record) -> step_index`; failure raises `TranscriptAppendError` which the runner turns into `turn.error` (P3). The in-memory list is replaced by `Session.log.tail_cache` (read-through cache invalidated on append) so hot paths don't re-read the file.

### 3.4 Events

Every durable event's payload gets `"record": {...}` (the record as stored, including `step_index`). `docs/events.schema.json` gains per-type `durable` and `record_kind`. Live-only: `model.text.delta`, `model.thinking.delta`, `tool.output.delta`, `cost.updated`, `context.assembled`, `compaction.started/progress`, `task.*`, `model.started`.

### 3.5 Client

`app.js`: delete the separate history renderer; `sessions.get` → `records[]` → same `renderRecord(kind, record)` used by the live path (`event.record`). Reload = replay of records. (WS-05 turns `renderRecord` into a registry.)

### 3.6 Checkpoints

`hooks/checkpoints` snapshots at `PRE_TOOL_USE` for mutating tools today; add `checkpoint.created` record (with checkpoint id) so WS-07 can diff `turn.started`…`turn.completed`. If the snapshot fails and `plugins.hooks-checkpoints.required: true` (default **true** when the plugin is enabled), the hook returns `block` (P3).

## 4. Files

### ADD

```
harness/kernel/transcript.py              # TranscriptLog (append/read/tail_cache/upgrade), TranscriptAppendError
tests/kernel/test_log_is_source.py        # P1 enforcement (see §5)
tests/kernel/test_record_upgrade_v1.py
tests/kernel/test_derive_messages.py      # table-driven: records → messages, incl. dangling tool call repair, compaction slice
tests/scenarios/{reload_equals_live,append_failure_fails_closed,crash_between_call_and_result}/…
docs/architecture/SESSION_LOG.md          # record kinds table (generated section via catalog), invariants, upgrade policy
```

### MODIFY

```
harness/sdk/types.py                      # TranscriptRecord v2 union (+ from_dict upgrader entry point)
harness/sdk/contracts.py                  # TranscriptStore.append(record: TranscriptRecord) -> int; read() -> list[TranscriptRecord]
harness/kernel/context.py                 # records_to_messages → derive_messages (keep alias one minor version)
harness/kernel/runner.py                  # remove transcript_records list; log-before-execute order; TranscriptAppendError → turn.error
harness/kernel/gate.py                    # append tool.decision / approval.* / tool.result / ui.card records; events carry record
harness/kernel/session.py                 # Session.log: TranscriptLog; get_history returns records
harness/kernel/commands.py:136            # sessions.get → {"records": [...]} (+ keep "history" key one minor version)
harness/kernel/approvals.py               # ask/resolve write approval.requested/resolved via the log passed in
harness/plugins/stores/jsonl_transcript/  # v2 lines, v1 upgrader, fsync policy setting
harness/plugins/hooks/checkpoints/        # checkpoint.created record; required → block on failure
harness/frontends/web/static/app.js       # one renderer path (see §3.5)
docs/events.schema.json                   # durable/record_kind (via catalog gen)
```

### Skeleton: `tests/kernel/test_log_is_source.py`

```python
@pytest.mark.asyncio
async def test_model_request_is_derivable_from_disk(tmp_path):
    rt = await bootstrap_kernel(workspace=ws, config_dir=conf)
    p = ScriptedProvider(models=[MODEL]); p.add_response([... two tool calls ...]); p.add_response([Done("end_turn")])
    rt.registry.register_provider(p, plugin_name="t")
    s = await rt.session_manager.create_session()
    await rt.runner.execute_turn(session=s.info, prompt="go", options={"provider": "scripted", "model": MODEL.id})
    live_requests = [normalize_messages(r.messages) for r in p.requests]
    await rt.registry.unload_all()

    rt2 = await bootstrap_kernel(workspace=ws, config_dir=conf)          # fresh process
    records = await rt2.session_manager.get_session(s.info.session_id).log.read()
    # messages the model saw at request k == derive over the records that existed at that moment
    for k, req in enumerate(live_requests):
        upto = records_before_request(records, k)                        # by step_index of the k-th assistant.message
        assert normalize_messages(derive_messages(upto)) == req, f"request {k} not derivable from log"

@pytest.mark.asyncio
async def test_append_failure_fails_closed(tmp_path, monkeypatch):
    ... monkeypatch store.append to raise after the user.message → execute_turn returns error; provider.requests == [] (model never called)

def test_every_durable_event_has_record_kind():
    schema = json.loads(Path("docs/events.schema.json").read_text())
    for t, spec in schema["events"].items():
        if spec["durable"]: assert spec["record_kind"] in RECORD_KINDS, t
```

## 5. Tests (fail-first)

| test | fails on main because |
|---|---|
| `test_log_is_source.py::test_model_request_is_derivable_from_disk` | cards/approvals aside, the dangling-call case and the compaction case diverge; also `records` are v1 dicts without step boundaries for "before request k" |
| `::test_append_failure_fails_closed` | append failure is a warning; model is still called |
| `::test_every_durable_event_has_record_kind` | schema has no `durable` |
| `test_derive_messages.py::test_dangling_tool_call_is_repaired_on_disk` | no repair; provider gets an assistant message with a tool_use and no result (some providers 400) |
| scenario `reload_equals_live` (web) | history pane lacks cards/approvals vs live pane |
| scenario `crash_between_call_and_result` | kill the turn after `tool.call` is logged; resume must show synthetic error result |

## 6. Acceptance

```bash
pytest tests/kernel/test_log_is_source.py tests/kernel/test_derive_messages.py tests/kernel/test_record_upgrade_v1.py -q
pytest tests/scenarios -q                       # all pre-existing goldens unchanged except where events now carry `record` (update once, review the diff)
python - <<'EOF'
# v1 file upgrade smoke: copy a pre-WS-04 session dir from tests/fixtures/sessions_v1/ into a tmp ws; sessions.get must return v2 records
EOF
grep -rn "transcript_records" harness/ | wc -l    # 0
harness catalog --check
```

## 7. Non-goals / cautions

- No remote upload, no telemetry (dsh's `session-log-deepseek` is explicitly **not** ported).
- Keep JSONL; no SQLite migration of the transcript in this WS (FTS index stays a derived index, rebuildable from the log — add `harness sessions reindex`).
- `derive_messages` must stay pure (no I/O, no settings) so WS-01 goldens and the P1 test can call it offline.
- Do not log secrets: sanitisation (gate step 9) runs **before** `tool.result` is appended; `settings.changed` records never contain `secrets.*`.
- Compaction: the summary is a record; the *removed* records stay on disk (they are the audit trail). `read(compact=True)` keeps the current slicing behaviour for callers that want model-view only.

## 8. Agent brief

> Implement WS-04 from `review/dsh-integration/WS-04_session_log_single_source.md` in three PRs: (a) `TranscriptRecord` v2 union + `TranscriptLog` + jsonl v1→v2 upgrader + `derive_messages` with table tests (no runner change yet; `records_to_messages` becomes a thin alias); (b) runner/gate/approvals log-before-execute, remove `transcript_records`, fail-closed append, events carry `record`, `docs/events.schema.json` durable/record_kind via `harness catalog gen`; (c) client single renderer path + `reload_equals_live` scenario + `docs/architecture/SESSION_LOG.md`. Write `tests/kernel/test_log_is_source.py` first and keep it red until (b). Preserve the v0.2.1 public shapes (`sessions.get.history`) for one minor version with a deprecation note in the catalog. Open PRs `WS-04a/b/c: session log single source`.
