# WS-05 — Feature-owned UI: settings, timeline renderers and panels exist only because a plugin registered them

| | |
|---|---|
| Status | specified |
| Depends on | §8 critical path merged **and** §8 step 2 (delete facade/dead tabs) done; WS-02 (catalog + `ui` check); WS-04 (records are what gets rendered) |
| Effort | 2–3 days |
| Principles | P2 (primary), P4, P6 |
| dsh reference (@86a40220) | `client/features/ui-settings-*/` (one settings tab per feature, mounted by the feature's client plugin), `client/core/conversation/` (renderer registered per `SessionEvent` type), `client/core/plugin-host/` (client plugins declare `contributes: {settingsTabs, conversationNodes, panels, commands}`), `scripts/verify-client-settings-tabs.ts`, `scripts/verify-client-event-renderers.ts` |
| Harness-Zero touchpoints | `harness/kernel/commands.py:176-250` (settings schema hand-coded; after WS-02 it aggregates registered schemas), `harness/frontends/web/static/settings.js:58-130` (fetches `settings.schema` **and** keeps a hard-coded fallback schema — a second source), `static/app.js:78-183` (23 event handlers wired inline, no registry; 11 schema event types unhandled), `static/index.html` (10 side panels `data-tab=artifact|diffs|economics|editor|files|sandbox|search|sessions|skills|tree`, 12 `data-notice` placeholders), `static/cards.js:29` (`CardRenderer` — closest thing to a renderer registry; cards are already data-driven), `tests/frontends/test_static_contract.py` (ID/inline-handler/CSP checks — base for the UI contract test), `harness/sdk/manifest.py:36-48` (manifest has `settings_schema`, no `ui` field), `harness/kernel/catalog.py` (WS-02) |

## 1. Outcome

- **Settings**: the client builds tabs and widgets exclusively from `settings.schema` (aggregated from `KERNEL_SCHEMA` + plugin `settings.schema.json`, WS-02). Disabling a plugin removes its tab. The fallback schema in `settings.js` is deleted. Group order and labels come from the schema (`x-ui.group`, `x-ui.order`).
- **Timeline**: `renderRecord(kind, record)` dispatches through a renderer registry keyed by record kind (WS-04). A kind without a renderer renders a generic "unknown record" node **and fails the contract test** — so a new durable event cannot ship without its renderer.
- **Panels** (side tabs): a panel exists only if declared by a plugin manifest `ui.panels[]` and backed by a registered command that provides its data. The 10 current `data-tab` panels are reduced to those with a backing command today (`sessions`, `economics`, `files` via `files.read`, `diffs` after WS-07, `search` if `/search` is wired) — the others are deleted, not hidden.
- **Interactive elements** bind through `data-command="<name>"` (+ `data-args` JSON) and are dispatched by one `commandBinder`; the contract test proves every bound command exists in `commands.list` (new command) and every command with `x-ui.exposed: true` has at least one binding or a scenario.

## 2. Current state (verified)

- Two settings schemas (server dict + client fallback) that already disagree in property sets.
- Timeline rendering is a chain of `this.events.on('<type>', …)` closures; history rendering is a different function (WS-04 §3.5 removes that).
- Side panels are static HTML; 12 controls are `data-notice` stubs (v0.2.1 review §3 table).
- No `commands.list` command; the client hard-codes command names in `api.js`.
- `test_static_contract.py` already parses HTML/JS with regexes in CI — extend it rather than introducing a new mechanism.

## 3. Design

### 3.1 Manifest `ui` block (optional)

```toml
[ui]
panels = [
  { id = "economics", title = "Spend", command = "economics", renderer = "table" },   # renderer ∈ builtin set: table | tree | markdown | list
]
record_renderers = ["job.started", "job.completed"]    # record kinds this plugin's client module renders (file: static/plugins/<name>.js)
client_module = "static/plugins/<name>.js"             # optional, served under /static/plugins/<name>.js, CSP 'self'
```

`PluginManifest.ui: UiContribution | None` parsed and validated (`sdk/manifest.py`); registry exposes `ui_contributions()`; new kernel command `ui.contributions` returns `{panels, record_renderers, client_modules, settings_groups}`; `commands.list` returns `{name, args_schema, exposed}` for every entry in the dispatch table.

The built-in kernel surfaces (sessions, settings, approvals, files) are declared the same way in a `harness/frontends/web/ui.toml` so there is exactly one mechanism.

### 3.2 Client

```
static/
├── app.js            # bootstrap only: connect SSE, load ui.contributions + settings.schema + commands.list, mount
├── registry.js       # RendererRegistry (record kind → fn), PanelRegistry, CommandBinder (data-command → api.command)
├── renderers/        # one file per built-in record kind: user_message.js, assistant_message.js, tool_call.js, tool_result.js, approval.js, card.js, turn.js, compaction.js, system_notice.js
├── panels/           # sessions.js, economics.js, files.js (+ diffs.js in WS-07)
├── settings.js       # schema-driven only; fallback deleted
├── plugins/          # plugin client modules (served from plugin dirs through the catalog's allow-list)
└── index.html        # shell: timeline, composer, panel host, settings host — no feature markup
```

Rules enforced by the contract test (§3.4): no `data-notice`; no element with an `id` referenced by JS that is not in `index.html` or produced by a renderer; every `data-command` in HTML/JS ∈ `commands.list`; every `data-setting` ∈ schema; every durable record kind ∈ `RendererRegistry`; every `ui.panels[].command` ∈ `commands.list`.

### 3.3 Settings

`settings.schema` (post-WS-02) → groups from `x-ui.group` (order: `x-ui.order` then alphabetical), widgets by `x-ui.widget` ∈ `text | number | boolean | select | secret | json | path`. Secret widgets never receive values (server returns `{"set": true}` only). A per-plugin `enabled` toggle is rendered from the plugin list, not from a setting (`plugins.enable/disable` commands exist).

### 3.4 Contract test (`tests/frontends/test_ui_contract.py`, extends `test_static_contract.py`)

```python
def test_every_bound_command_exists(ui_contract, commands):      # ui_contract from harness.kernel.catalog (parsed HTML/JS), commands from Commands dispatch table
    assert set(ui_contract.commands) <= set(commands)
def test_every_durable_record_kind_has_renderer(schema, renderers):  # renderers = names exported by static/renderers/*.js + plugin record_renderers
    assert set(k for k in RECORD_KINDS if durable(k)) <= set(renderers)
def test_no_placeholder_bindings(static_sources):
    assert not re.search(r"data-notice|noop\(|coming soon|TODO", static_sources, re.I)
def test_panels_are_declared(ui_contract, contributions):
    assert set(ui_contract.panel_ids) == set(p.id for p in contributions.panels)
def test_settings_js_has_no_inline_schema():
    assert "'x-ui'" not in Path("static/settings.js").read_text()
```

Plus a headless-DOM test (jsdom, WS-01 infra): load with a `ui.contributions` that includes a fake plugin panel + renderer → panel tab appears and the renderer is invoked for its record kind; load with the plugin disabled → neither exists.

## 4. Files

### ADD

```
harness/sdk/manifest.py            # UiContribution dataclass + parsing (MODIFY)
harness/frontends/web/ui.toml      # built-in panels/renderers declared like a plugin
harness/frontends/web/static/registry.js
harness/frontends/web/static/renderers/*.js
harness/frontends/web/static/panels/*.js
tests/frontends/test_ui_contract.py
tests/frontends/test_ui_headless.mjs (run via pytest wrapper)
tests/scenarios/{plugin_panel_appears_when_enabled,settings_tab_from_plugin_schema}/…
docs/catalog/UI_CONTRACT.md        # generated (WS-02)
```

### MODIFY

```
harness/kernel/commands.py         # + commands.list, ui.contributions; settings.schema groups carry order
harness/kernel/registry.py         # collect ui contributions; serve allow-listed client modules
harness/frontends/web/server.py    # route /static/plugins/<name>.js from registry allow-list (CSP unchanged: 'self')
harness/frontends/web/static/{app.js,settings.js,index.html,api.js,cards.js}
tests/frontends/test_static_contract.py   # keep; new test imports its helpers
```

## 5. Tests (fail-first)

| test | fails on main because |
|---|---|
| `test_ui_contract.py::test_no_placeholder_bindings` | 12 `data-notice` |
| `::test_every_bound_command_exists` | no `commands.list`; several buttons bound to nothing |
| `::test_every_durable_record_kind_has_renderer` | 11 unhandled types / no registry |
| `::test_settings_js_has_no_inline_schema` | fallback schema present |
| `::test_panels_are_declared` | 10 static panels |
| headless: plugin disabled ⇒ no tab | no mechanism |

## 6. Acceptance

```bash
pytest tests/frontends -q && pytest tests/scenarios -q -k "plugin_panel or settings_tab"
harness catalog --check ui                       # now `fail` mode
# manual: harness ui → Settings shows one tab per group derived from schema; `harness plugin disable hooks-checkpoints` → restart → its settings tab is gone
# teeth: add <button data-command="nope"> → contract test fails; add a record kind to RECORD_KINDS without renderer → fails
```

## 7. Non-goals / cautions

- No framework migration (stay vanilla ES modules; CSP `script-src 'self'` must keep passing `test_static_contract.py`).
- Plugin client modules are trusted code served from disk: only **built-in** and **config-dir** plugins may ship `client_module`; workspace plugins may not (same clamp as capabilities).
- Do not "register" the 10 existing panels to make the test pass; delete the ones without a backing command (review §8 step 2 is the prerequisite for this reason).
- Keep the CLI frontend out of scope; it renders records through `rich` in its own module and gets the same registry idea only if someone asks.

## 8. Agent brief

> Implement WS-05 from `review/dsh-integration/WS-05_feature_owned_ui.md` after WS-02 and WS-04 are merged. Start with `tests/frontends/test_ui_contract.py` (all five tests red). Add `commands.list` and `ui.contributions` kernel commands and the manifest `ui` block (+ `harness/frontends/web/ui.toml` for built-ins). Restructure the client into `registry.js` + `renderers/` + `panels/`; delete the fallback schema and every `data-notice`/unbacked panel. Add the jsdom test for "panel appears only when the plugin is enabled" using the WS-01 headless harness. Regenerate `docs/catalog/UI_CONTRACT.md`, flip `harness catalog --check ui` to fail mode. Open PR `WS-05: feature-owned UI`.
