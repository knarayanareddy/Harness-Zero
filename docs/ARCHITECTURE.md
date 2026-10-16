# Harness-Zero architecture (v0.2.1)

This document describes the system **as built** in this repository: processes, kernel modules, the command
and event catalogues, the session lifecycle, web authentication and where the tests live. The historical
research syntheses that preceded the build are in [`docs/research/`](research/00_README_AND_MANIFEST.md);
the reviews and the consolidation log that shaped the current tree are in [`review/`](../review/README.md).

The command and event tables below are checked against the code by `tests/test_docs_and_versions.py`
(`BUILTIN_COMMANDS`, `EVENT_PAYLOADS`, `CARD_FIELDS`): if a command or event is added, this file must change.

## 1. Process model

```
harness ui                       harness run / interactive
┌───────────────────────────┐    ┌──────────────────────────┐
│ HarnessWebServer (aiohttp)│    │ CliApp (rich)            │
│  :8000 API + static UI    │    │  same Commands, in-proc  │
│  :8001 sandbox origin     │    └───────────┬──────────────┘
└─────────────┬─────────────┘                │
              │ Commands.dispatch / EventBus │
┌─────────────▼──────────────────────────────▼─────────────┐
│ Kernel (harness/kernel, ≈5.7k lines)                      │
│  bootstrap → registry(all plugins) → gate → runner → bus   │
└───────────────────────────────────────────────────────────┘
```

* One daemon per workspace. `harness ui --port 8000 --sandbox-port 8001 --workspace .` writes
  `<workspace>/.harness/daemon.json` (port, bearer token, mode 0600) and serves the browser client from
  `harness/frontends/web/static/`. The CLI frontends embed the same kernel in-process.
* Everything the frontends do goes through **`Commands`** (`harness/kernel/commands.py`) and everything the
  kernel says comes back on the **`EventBus`** (`harness/kernel/bus.py`) as typed events (§4). There is no
  second API.
* Untrusted HTML/SVG artifacts are served from the second origin (`:8001`) so a prompt-injected artifact
  cannot read the API origin.

## 2. Kernel modules (`harness/kernel/`)

| module | role |
|---|---|
| `bootstrap.py` | `bootstrap_kernel(workspace, config_dir, resume_session=True)` wires everything below and returns a `KernelRuntime` (`commands`, `bus`, `registry`, `session_manager`, …). |
| `registry.py` | `PluginRegistry`: discovers `harness_plugin.toml` manifests (built-in `harness/plugins/**`, `$HARNESS_CONFIG_DIR/plugins`, `<workspace>/.harness/plugins`), loads entry points, holds providers / tools / policies / executors / stores / context sources / strategies / hooks; enforces the `Provider` contract at registration. |
| `settings.py`, `secrets.py` | `SettingsStore` (`$HARNESS_CONFIG_DIR/settings.json`, default `~/.harness/`, layered with `<workspace>/.harness/settings.json`) and `Secrets` (`secrets.json`, 0600; env fallback). |
| `jail.py` | `WorkspaceJail`: every path a tool touches resolves inside the workspace. |
| `gate.py` | `PolicyGate.execute(call)`: the nine steps every tool call passes — resolve + validate args · `PRE_TOOL_USE` hooks · policy chain (`workspace_jail` 10 → `risk_classifier` 50 → `tirith` 60 → `permission_modes` 900) · approval when a policy says ASK · capability-gated `ToolContext` · handler under timeout · `POST_TOOL_USE` hooks · secret sanitisation + truncation. |
| `approvals.py`, `inbox.py` | `ApprovalBroker` (`approval.required` / `resolve_approval`) and `BackgroundInbox` (background task wake-ups, no polling). |
| `runner.py` | `TurnRunner`: resolves the provider, assembles context, runs the strategy, streams `model.*` events, enforces the circuit breaker and spend cap, records usage. |
| `context.py`, `compaction.py` | `ContextAssembler` (blocks with `static` / `session` / `turn` stability for prompt caching) and `Compactor`. |
| `session.py` | `SessionManager`: owns sessions (§5). |
| `economics.py`, `breaker.py` | `EconomicsSnapshot` from the cost ledger store; provider circuit breaker. |
| `commands.py` | `Commands`: the frontend API (§3). |
| `bus.py` | `EventBus`: fan-out to subscribers, delta coalescing for SSE. |
| `sanitizer.py`, `validators.py` | Secret redaction; `validate_session_id`. |

Plugins (35, nine families under `harness/plugins/`): `providers` (anthropic, gemini, mock, ollama,
openai_compat → openai / openrouter / deepseek), `tools` (fs, shell, search, memory, skills, artifacts,
screenshot_macos), `policy` (workspace_jail, risk_classifier, tirith, permission_modes), `executors` (local,
worktree), `stores` (jsonl_transcript, cost_ledger, fts5_search, fs_artifacts), `context` (identity,
instructions_files, memories, repo_map, skills_catalog, tool_guidance), `strategies` (single_loop,
architect_editor), `hooks` (checkpoints), `bridges` (mcp_client, mcp_server, acp, shell_hooks). The SDK
contracts they implement are in [`architecture/CONTRACTS.md`](architecture/CONTRACTS.md); the catalogue in
[`docs/catalog/PLUGINS.md`](catalog/PLUGINS.md) (generated — `harness catalog gen`, gated by `harness catalog --check` and by `tests/kernel/test_catalog.py`); authoring in [`PORTING_RECIPES.md`](PORTING_RECIPES.md).

## 3. Command catalogue

`POST /commands/<name>` with a JSON body = `Commands.dispatch(name, body)`. Plugins can add commands with
`Commands.register_command`. Built-ins (`BUILTIN_COMMANDS`):

| command | arguments | result / effect |
|---|---|---|
| `submit_turn` | `prompt`, `session_id?`, `model?`, `provider?`, `strategy?`, `options?` | Runs one turn (one active turn per session, else 409). Without `session_id` the active session is used or one is created (`session.started`). Returns `TurnResult`. |
| `cancel_turn` | `session_id?` | Cancels the running turn (`turn.cancelled`). |
| `resolve_approval` | `approval_id`, `approved`, `reason?` | Answers an `approval.required` (`approval.resolved`). |
| `form.submit` | `form_id`, `data` | Submits a `form` card (`form_submit` is an underscore alias). |
| `sessions.list` | — | `SessionRow[]`, newest activity first. |
| `sessions.create` | — | Creates and activates an empty session. |
| `sessions.get` | `session_id` | `{session_id, …, records: TranscriptRecord[]}` — the full history. |
| `sessions.switch` | `session_id` | Activates an existing session (`session.switched`). |
| `settings.get` | `path`, `default?` | Dotted-path read from the merged settings. |
| `settings.set` | `path`, `value` | Writes a setting (or a `secrets.*` secret) and publishes `settings.changed`. |
| `settings.schema` | — | JSON schema with `x-ui` groups that drives the settings panel. |
| `models.list` | — | `ModelRow[]` — every model every provider knows, with `status` ready / needs_key / unreachable. |
| `files.read` | `path` | Reads a workspace file through the jail. |
| `files.write` | `path`, `content` | Writes a workspace file through the jail. |
| `plugins.list` | — | Manifest summary of every discovered plugin. |
| `plugins.info` | `name` | One plugin's manifest and state. |
| `plugins.enable` | `name` | Removes the plugin from `kernel.plugins.disabled`. |
| `plugins.disable` | `name` | Adds the plugin to `kernel.plugins.disabled` (takes effect on next load). |
| `approvals.list` | — | Pending approvals. |
| `economics` | `session_id?` | `EconomicsSnapshot` (`turn_usd`, `session_usd`, `lifetime_usd`, `cap_usd`, `unknown_pricing`, `usage`). |
| `commands.list` | — | Every dispatchable command with its argument names, `alias_of` and `exposed` (WS-05). |
| `ui.contributions` | — | What the client mounts: declared panels (with their owner and backing command), `[ui].record_renderers`, plugin client modules, `settings_groups`, `record_kinds`. |

Errors map to HTTP: a `HarnessError` with a `status_code` keeps it (`TurnInProgress` → 409); other
`HarnessError` / `ValueError` → 400; `KeyError` / `FileNotFoundError` → 404; anything else → 500 with the
message. Responses are `{"result", "status": "ok"}` or `{"error", "status": "error"}`.

## 4. Event catalogue

Every event is `Event{type, ts, session_id, data}`; `data` is one of the payload classes in
`harness/sdk/events.py` (`EVENT_PAYLOADS`), serialised by `to_dict()` and validated by
`validate_event()` — in tests by the autouse `event_contract_guard`, in the browser by the handlers that
read only these keys (`tests/frontends/test_static_contract.py`). The browser receives them on
`GET /events` as named SSE frames (`event: <type>`), plus `heartbeat`. `docs/events.schema.json` is the
generated JSON schema (`harness events schema`).

*Session*

| event | payload class | keys |
|---|---|---|
| `session.started` | `SessionStarted` | session_id, workspace, strategy, provider, model |
| `session.switched` | `SessionSwitched` | session_id, workspace |

*Plugins & settings*

| event | payload class | keys |
|---|---|---|
| `plugin.loaded` | `PluginLoaded` | name, version, provides, capabilities, trust |
| `plugin.error` | `PluginError` | name, error |
| `settings.changed` | `SettingsChanged` | paths |
| `hook.fired` | `HookFired` | hook, event, command, outcome, duration_ms |

*Turn*

| event | payload class | keys |
|---|---|---|
| `turn.started` | `TurnStarted` | prompt_preview, strategy, provider, model, options |
| `turn.completed` | `TurnCompleted` | stop_reason, usage, duration_ms, tool_calls |
| `turn.cancelled` | `TurnCancelled` | reason |
| `turn.error` | `TurnError` | message, kind, retryable |
| `context.assembled` | `ContextAssembled` | blocks, total_tokens, history_messages |

*Model*

| event | payload class | keys |
|---|---|---|
| `model.started` | `ModelStarted` | provider, model, request_tokens_estimate |
| `model.text.delta` | `ModelTextDelta` | text |
| `model.thinking.delta` | `ModelThinkingDelta` | text |
| `model.tool_call` | `ModelToolCall` | call_id, name, arguments |
| `model.notice` | `ModelNotice` | message |
| `model.completed` | `ModelCompleted` | stop_reason, usage |

*Tools & approvals*

| event | payload class | keys |
|---|---|---|
| `tool.call` | `ToolCallStarted` | call_id, name, arguments, risk, plugin |
| `tool.decision` | `ToolDecision` | call_id, action, plugin, policy, risk, reason |
| `approval.required` | `ApprovalRequired` | approval_id, call_id, kind, summary, details, timeout_s, options |
| `approval.resolved` | `ApprovalResolved` | approval_id, approved, by, reason |
| `tool.output.delta` | `ToolOutputDelta` | call_id, stream, text |
| `tool.result` | `ToolCallResult` | call_id, name, is_error, duration_ms, preview, metadata |
| `tool.result.pruned` | `ToolResultPruned` | call_id, name, original_chars, shown_chars, spill_path (live-only, WS-03 size guard) |
| `tool.repeat_detected` | `ToolRepeatDetected` | call_id, name, count, blocked (live-only, WS-03 spin brake) |

*Background & compaction*

| event | payload class | keys |
|---|---|---|
| `task.started` | `TaskStarted` | task_id, label |
| `task.completed` | `TaskCompleted` | task_id, label, exit_code, tail |
| `compaction.started` | `CompactionStarted` | estimated_tokens |
| `compaction.completed` | `CompactionCompleted` | summary_tokens, duration_ms |

*Economics & UI*

| event | payload class | keys |
|---|---|---|
| `cost.updated` | `CostUpdated` | turn_usd, session_usd, lifetime_usd, cap_usd, unknown_pricing, usage |
| `ui.card` | `UiCard` | card |

`ui.card.card` is `{kind, id, title, data}` with `data` keys per kind (`CARD_FIELDS`):

| kind | data keys |
|---|---|
| `markdown` | body |
| `code` | body, language, path |
| `diff` | path, stats, unified |
| `table` | columns, rows |
| `notice` | body, level |
| `progress` | label, value |
| `tree` | nodes |
| `approval` | approval_id, details, options, summary |
| `artifact` | media_type, name, request_feedback, url |
| `form` | form_id, schema, submit_label |

## 5. Session lifecycle (kernel-owned)

* Sessions live in `<workspace>/.harness/sessions/<session_id>/` (`session.json` metadata,
  `transcript.jsonl` compact, `transcript_full.jsonl` with tool payloads). The transcript store holds one session; listing is
  `SessionManager.list_sessions()` → `SessionRow[]` (`title` = first prompt, `turns`, `updated_at`, `active`,
  `busy`), newest activity first.
* **The log is the source of truth** (WS-04). `TranscriptLog` (`harness/kernel/transcript.py`) owns the
  write path — one instance per session directory, shared by the turn runner, `Session.log` and
  `sessions.get`, so there is no in-memory mirror of the conversation to drift from the file. Records are
  typed and versioned (`kind` ∈ `RECORD_KINDS`, `v: 2`); v1 lines are upgraded *on read* and never
  rewritten, because an appended line is the audit trail of what was recorded when.
* The record anatomy, the invariant list, the client-side mapping and the upgrade policy are spelled out in
  [`architecture/SESSION_LOG.md`](architecture/SESSION_LOG.md). The generated kind table — which kinds are
  model-visible, which durable event carries them, which scenario transcripts hold one — is
  [`catalog/RECORD_KINDS.md`](catalog/RECORD_KINDS.md), refreshed by `harness catalog gen` and checked by
  `harness catalog --check`.
* Policy facts are records too (WS-04b). The gate writes a `tool.decision` record for every deny / ask /
  allow and a `ui.card` record for every card a tool produced — before publishing the matching event, and
  whether or not anyone is subscribed, because the log is not the bus's copy of itself. The approval broker
  writes `approval.requested` and `approval.resolved` (resolved via `resolve_recorded`, which async callers
  use; the sync `resolve` is what a CLI keypress calls and cannot await). None of these kinds is
  model-visible, so a session that logs twice as much history sends the same prompt: re-baselining the 24
  scenarios changed `transcript.json` and `events.json` and left `requests.json`, `http.json` and the DOM
  `timeline.txt` byte-identical.
* Durability is declared, not remembered: `EventPayload.record_kind` names the record kind an event
  mirrors and `EventPayload.transient` marks the streamed set; `harness catalog gen` renders both (plus
  `carries_record`) into `docs/events.schema.json` and the "In the log" column of `docs/catalog/EVENTS.md`.
  A durable event carries the stored record on `Event.record`, so a client can render that fact without a
  second fetch. Two events name a kind but carry no pointer — `tool.result` (a strategy batches a step's
  results into one record, the shape a provider requires) and `compaction.completed` (the summary record is
  written by the loop) — and pair with the log by `call_id` / step boundary instead.
* An unanswered `tool_use` (a crash between recording a call and its result) is closed **in the log** at
  the start of the next turn: `TranscriptLog.repair_dangling_tool_calls()` appends a synthetic error
  result labelled `metadata.synthetic`. `derive_messages` still repairs its view in memory, because it
  must stay pure, but the transcript now says what happened.
* `derive_messages` (`harness/kernel/context.py`) is the only producer of a model request's message
  list: `user.message`, `assistant.message`, `tool.result`, `compaction.summary` and a
  `system.notice` marked `model_visible` reach the model; decisions, approvals, cards and turn markers
  are in the log for the user and not in the request. A `tool_use` with no recorded result (a crash
  between the two) is repaired with an explicit synthetic error result, so a crashed turn cannot wedge
  the session. Appending is fail-closed: if the store cannot write, the turn stops with
  `turn.error(kind="transcript_append_failed")` instead of continuing on a history nobody will read.
* On start, `bootstrap_kernel(resume_session=True)` resumes the newest session that has turns
  (`session.switched`); `harness run` starts fresh unless `--continue`, `harness interactive` resumes unless
  `--new`. The web client **never creates a session at boot**: it connects SSE, lists sessions, renders the
  active one if any, and the first `submit_turn` without a `session_id` makes the kernel create one and
  announce it (`session.started`). Restarting the daemon three times therefore leaves one session, not three.
* One active turn per session (`TurnInProgress` → 409); `cancel_turn` ends it with `turn.cancelled`.

## 6. Web client and authentication

* Static files only (`index.html`, `app.js`, `events.js`, `api.js`, `cards.js`, `settings.js`,
  `styles.css`, `vendor/tw.css`); CSP `script-src 'self'`; no inline scripts or handlers, no inline styles.
  Styling is `styles.css` (components, state in `data-*` attributes) plus `vendor/tw.css` (utilities compiled
  from the templates by `npm run build:css`; CI checks it is fresh and `tests/frontends/ui_audit.py --strict`
  checks ids/classes/stylesheets are coherent).
* Auth: the bearer token from `daemon.json` (`Authorization: Bearer …` or the `harness_token` cookie set by
  `/`), and for `EventSource` a 60-second single-use ticket from `POST /auth/ticket`. Mutating requests must
  be `application/json` (else 415) with a same-origin `Origin` (else 403); the `Host` header must be an
  allowed host (else 421, DNS-rebinding guard). `/health` reports `version` (= `harness.__version__`),
  `workspace`, `active_session`, `sandbox_origin`, `spend`.
* The client renders turns from `model.text.delta` / `model.thinking.delta`, tool cards from `tool.call` →
  `tool.output.delta` → `tool.result`, approvals from `approval.required`, cards from `ui.card`
  (artifacts also into the Artifacts tab), economics from `cost.updated` / `economics`, and the model picker
  from `models.list` grouped by `status`.
* Which **panels** exist is not client knowledge: `ui.contributions` returns the panels declared in a manifest's
  `[ui]` block — the shipped client's three are in `harness/frontends/web/ui.toml` — each backed by a command in
  `commands.list`. A feature that is disabled contributes nothing, so its tab goes with it; a panel whose command
  is not dispatchable is reported in `unbacked_panels` and not served. Timeline rendering dispatches on
  `record.kind` through `static/registry.js`: one module per kind under `static/renderers/`, and
  `NO_RENDERER` for the kinds the timeline deliberately does not paint — each with its reason, each checked
  against the record contract in `tests/frontends/test_ui_contract.py` (generated table:
  `docs/catalog/UI_CONTRACT.md`).
* Which **pane** is visible is the same registry's answer, not a chain of branches: `PanelRegistry.activate(id)`
  shows the panel's host and runs its module (`static/panels/<id>.js`), and a declared panel with no module gets
  the generic renderer its `[ui]` block names. So `index.html` needs no edit to gain a tab, and the client
  cannot mount a panel the kernel never declared (`test_the_panel_modules_are_the_declared_panels`).
  The **settings tabs** are derived the same way: a property's `x-ui.group` chooses its tab, an unnamed one takes
  the tab of its plugin's family directory, and `x-ui.owner` names the section it renders under — the client
  holds no tab list, not even for the tab it opens on (P13a). Controls
  that merely call a command use `data-command` + `CommandBinder`; controls needing a runtime id or local side
  effects keep their handler in `app.js`, and the catalogue lists the bindings either way.

## 7. Where the tests live

| path | what | how to run |
|---|---|---|
| `tests/kernel/` | kernel units + roundtrips (bus, gate invariants, runner, sessions, contracts, import boundaries) | `pytest tests/kernel -q` |
| `tests/plugins/` | one file per plugin family incl. provider readiness; also `harness plugin test --all` | `pytest tests/plugins -q` |
| `tests/frontends/` | web server (auth, SSE, commands), static contract tests (handlers/cards/pickers read only contract keys), UI coherence (`ui_audit.py` + jsdom cascade), CLI | `pytest tests/frontends -q` |
| `tests/e2e/` | S2–S6 acceptance scenarios on a bootstrapped kernel | `pytest tests/e2e -q` |
| `tests/scenarios/` | record/replay **flow-of-information** scenarios, one case per `scenario.yaml`: real kernel → real daemon → the shipped client in jsdom, provider replayed from `provider.jsonl`, goldens for transcript / events / requests / http / timeline, and a record mode that writes a fixture from a live provider (`HARNESS_REPLAY=record\|replay\|update`) | `SCENARIO_DOM_WAIT_MS=6000 pytest tests/scenarios -q` (needs `npm ci`); how-to in [`docs/testing/SCENARIOS.md`](testing/SCENARIOS.md) |
| `tests/test_docs_and_versions.py` | this document and the CHANGELOG stay true to the code | `pytest tests/test_docs_and_versions.py -q` |

Plain `pytest -q` (hermetic: `export HOME=$(mktemp -d); export HARNESS_CONFIG_DIR=$HOME/.harness`) runs all
of them in one invocation; CI runs exactly that plus `ruff`, the plugin doctor, the schema diff and the UI
gates (`.github/workflows/ci.yml`).
