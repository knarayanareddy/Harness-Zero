# Principles (P1–P6)

Six invariants observed in `dsh` (`docs/architecture.md`, `packages/README.md`, `scripts/verify-*.ts`) restated as Harness-Zero rules. Each has an **enforcement** — a test or CI gate — because a principle without a gate is how the previous three releases happened. Workstreams cite these by id.

---

## P1 — Model-visible means logged

**`dsh`:** "Every model request must be reconstructable from the log. New model-visible inputs require session events." `deriveMessages()` projects history from the append-only `SessionEvent` log; resume, fork, replay, export, telemetry and the UI all fold the same log.

**Harness-Zero rule:** `ModelRequest.messages` is produced only by `derive_messages(records)` (`harness/kernel/context.py`, today `records_to_messages`) from records that are already appended to the session's transcript store. Context sources add *system* blocks; anything that becomes a user/assistant/tool message is a record first. Live-only events (`model.text.delta`, `tool.output.delta`, `cost.updated`, …) are marked `durable: false` in `docs/events.schema.json`.

**Enforcement:** `tests/kernel/test_log_is_source.py` (WS-04) — captures `ModelRequest`s with a scripted provider, re-derives messages from the on-disk transcript of the same session in a fresh process, asserts equality; a second test asserts every `durable: true` event type in the schema has a corresponding record type and renderer (WS-05).

---

## P2 — A surface exists only because a plugin registered it

**`dsh`:** each feature ships its own `ui-settings-*` tab; conversation nodes are registered per session-event type; `verify-client-*` gates check the browser half.

**Harness-Zero rule:** the web client renders settings **only** from `settings.schema` (which is assembled from plugin manifests' `settings_schema` + `KERNEL_SCHEMA`), renders timeline items **only** through a renderer registered for an event type, and binds interactive elements **only** to a registered command. There is no static HTML for features. If the registry does not know it, the page does not show it.

**Enforcement:** `tests/frontends/test_ui_contract.py` (WS-05): for every `data-command` / `data-event` / `data-setting` attribute in the served page or client modules there is a registered command / schema event / schema setting, and vice-versa for every `durable` event there is a renderer. `harness catalog --check ui` (WS-02) runs the same check in CI. Review rule: `data-notice`, `data-onclick="noop"`, hard-coded user data ⇒ reject.

---

## P3 — Fail closed

**`dsh`:** `ctx.approval` is one-shot; "absent or unanswerable: deny". Sandbox: "enforcement is a reported fact" (`full` / `partial`), unsupported platform ⇒ `SANDBOX_UNAVAILABLE` rather than silently running unconfined. Checkpoint failure ⇒ the model request does not run.

**Harness-Zero rule:** an `ask` decision with no reachable approver is a denial (already true in the CLI; must be true in the daemon when no client is connected). A sandbox mode that cannot be enforced on this host is reported as such in the tool result and the UI; `permission_mode=sandboxed` without an available backend refuses mutating tools instead of degrading. A transcript append failure aborts the step.

**Enforcement:** `tests/kernel/test_gate_invariants.py` gains cases for "no approver ⇒ deny" via the daemon path (WS-06), "sandbox unavailable ⇒ refuse + reported" (WS-06), "append failure ⇒ step error" (WS-04).

---

## P4 — Catalogs are generated, prose is verified

**`dsh`:** `gen-tool-catalog`, `gen-config-catalog`, `event-producer-consumer` map, module graph, persistence catalog — all generated, all freshness-gated (`doc-sync`); `verify-doc-refs`, `verify-md-links`, per-package README contract.

**Harness-Zero rule:** `docs/PLUGIN_CATALOG.md`, `docs/TOOL_CATALOG.md`, `docs/SETTINGS_CATALOG.md`, `docs/events.schema.json`, `docs/UI_CONTRACT.md` are outputs of `harness catalog` and never edited by hand (header says so). `CHANGELOG.md` feature lines link to a catalog diff. README prose that names a tool/plugin/setting/route is checked against the catalogs by `harness catalog --check refs`.

**Enforcement:** CI step `harness catalog --check` (WS-02) fails on any drift between the tree and the generated files, and on any backticked identifier in `README.md` / `docs/*.md` that is not in a catalog.

---

## P5 — The real app is tested against recorded reality

**`dsh`:** `llm-replay` boots the real agent keylessly from recorded session JSONL; `session-snapshot` compares normalised results with committed goldens across headless, SDK, ACP and Web; the web lane runs built-client interaction snapshots in CI.

**Harness-Zero rule:** every user-visible capability has a **scenario** under `tests/scenarios/<name>/` consisting of a recorded provider stream, an input script, and goldens for (a) the transcript, (b) the SSE event sequence, (c) the rendered timeline (headless DOM). Scenarios replay in CI without keys. Recording against a real provider is a local, explicit action.

**Enforcement:** `pytest tests/scenarios` in CI (WS-01); a new tool/command/UI feature without a scenario is rejected in review (checked by `harness catalog --check scenarios`: every tool and command name appears in at least one scenario manifest).

---

## P6 — Settings have one owner; capabilities are seams

**`dsh`:** plugin settings live in the plugin's namespace and are read through a view; capabilities (fs, subprocess, sandbox, web, skill, subagent, …) are a *Service Definition* with swappable providers and consumers that never import a concrete provider.

**Harness-Zero rule:** a settings key is `plugins.<plugin-name>.<key>` and declared in that plugin's `settings_schema`; only `kernel.*` is owned by the kernel. Legacy global keys are read for one minor version, then removed. New capabilities (sandbox backend, spill store, job registry, web provider) are declared as SDK protocols in `harness/sdk/contracts.py` with a registry slot, never as imports of a concrete plugin.

**Enforcement:** `tests/kernel/test_import_boundaries.py` already forbids kernel→plugin imports; extend it to forbid plugin→plugin imports except through `harness.sdk` (WS-02). `harness catalog --check settings` fails on a key read anywhere in the tree that is not declared in a schema.
