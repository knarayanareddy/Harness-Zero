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
| `tool.decision` | `ToolDecision` | call_id, action, policy, risk, reason |
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

## 7. Where the tests live

| path | what | how to run |
|---|---|---|
| `tests/kernel/` | kernel units + roundtrips (bus, gate invariants, runner, sessions, contracts, import boundaries) | `pytest tests/kernel -q` |
| `tests/plugins/` | one file per plugin family incl. provider readiness; also `harness plugin test --all` | `pytest tests/plugins -q` |
| `tests/frontends/` | web server (auth, SSE, commands), static contract tests (handlers/cards/pickers read only contract keys), UI coherence (`ui_audit.py` + jsdom cascade), CLI | `pytest tests/frontends -q` |
| `tests/e2e/` | S2–S6 acceptance scenarios on a bootstrapped kernel | `pytest tests/e2e -q` |
| `tests/scenarios/` | 21 record/replay **flow-of-information** scenarios: real kernel → real daemon → the shipped client in jsdom, provider replayed from `provider.jsonl`, goldens for transcript / events / requests / http / timeline | `SCENARIO_DOM_WAIT_MS=6000 pytest tests/scenarios -q` (needs `npm ci`) |
| `tests/test_docs_and_versions.py` | this document and the CHANGELOG stay true to the code | `pytest tests/test_docs_and_versions.py -q` |

Plain `pytest -q` (hermetic: `export HOME=$(mktemp -d); export HARNESS_CONFIG_DIR=$HOME/.harness`) runs all
of them in one invocation; CI runs exactly that plus `ruff`, the plugin doctor, the schema diff and the UI
gates (`.github/workflows/ci.yml`).
