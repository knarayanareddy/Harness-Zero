# Changelog

All notable changes to the **Harness-Zero** project are documented in this file.

The format is based on [Keep a Changelog](https://keepachangelog.com/en/1.1.0/),
and this project adheres to [Semantic Versioning](https://semver.org/spec/v2.0.0.html).

---

## [Unreleased]

### Added — WS-10a durable background jobs
- Added the session-scoped `tools-jobs` registry, durable `job.started/output/exited` records, bounded output ring and spill file, lost-process recovery, and a model-visible next-turn completion notice. Detached `job_start` jobs no longer hold up the turn; legacy reactive `run_command` tasks retain their wait behavior and `manage_task` alias.
- Added `job_start`, `job_output`, `job_list`, and `job_kill`, plus the plugin-owned Jobs panel. Panel stop uses the existing approval broker and destructive-action policy.
- Hardened index/spill access against symlink escapes, FIFOs and other special files, and multiply-linked regular files; file descriptors are opened nonblocking/no-follow where supported and validated with `fstat`.
- Added the deterministic kernel and record/replay integration coverage. Validation: `pytest -q` → **716 passed, 4 skipped**; `ruff check .`, strict UI audit, plugin doctor, catalog drift, and `git diff --check` pass. Catalog README/scenario-coverage warnings remain advisory.
- Generated catalog entries: [plugins](docs/catalog/PLUGINS.md), [tools](docs/catalog/TOOLS.md), [settings](docs/catalog/SETTINGS.md), and [UI contract](docs/catalog/UI_CONTRACT.md).

### Added — WS-09 MCP streamable HTTP and resilience
- Added the streamable-HTTP transport alongside stdio, per-call timeouts, cancellation, supervised reconnect/backoff, and truthful connection status.
- Generated catalog changes: [plugins](docs/catalog/PLUGINS.md), [tools](docs/catalog/TOOLS.md), [settings](docs/catalog/SETTINGS.md), and [events](docs/catalog/EVENTS.md).

### Added — WS-08 user questions
- Added `ask_user` with a model-visible question/answer lifecycle on the existing interaction channel, including CLI and web response handling and headless behavior.
- Generated catalog changes: [tools](docs/catalog/TOOLS.md), [events](docs/catalog/EVENTS.md), and [UI contract](docs/catalog/UI_CONTRACT.md).

### Added — WS-07 workspace changes and deliverables
- Added per-turn workspace-change summaries and the `present` tool for user-facing files/artifacts, with durable events and feature-owned renderers/panels.
- Generated catalog changes: [plugins](docs/catalog/PLUGINS.md), [tools](docs/catalog/TOOLS.md), [events](docs/catalog/EVENTS.md), and [UI contract](docs/catalog/UI_CONTRACT.md).

### Added — P15 / WS-06 permission presets and OS process confinement
- **Named presets resolve without materialising their expansion.** `locked`, `careful`, `standard`, and `open` use
  explicit stored values → preset defaults → call-site/schema defaults. Explicit overrides remain pinned; `bypass`
  is never a preset. The `standard` no-click EXEC path is allowed only after a sandbox backend successfully probes.
- **Executor processes gain a fail-closed OS boundary.** `SandboxService` probes the selected backend and wraps the
  chosen executor after policy/approval. Linux bubblewrap and macOS Seatbelt adapters are registered through the
  SDK seam. Required-but-unavailable denies before spawn; preferred-but-unavailable asks before unconfined
  execution; `off` is an explicit unconfined choice. An approved unconfined fallback and every confined result
  retain truthful sandbox metadata, including enforcement and network state.
- **One posture across kernel, CLI and Studio.** `security.status`, `/health`, `harness security show`, tool
  decision/result metadata and the Studio badge expose effective preset and sandbox state. The schema-driven
  Security & Sandboxing settings group includes preset, backend, mode, network and extra-readable path controls.
- Added preset/sandbox unit tests, the daemon-without-approver regression, four preset/unavailable/full-status
  scenarios, sandbox backend READMEs, generated catalogs, and CI installation of bubblewrap on Linux.
- Validation: `pytest -q -rs` → **655 passed, 4 skipped**; scenarios → **30 passed, 1 skipped**; plugin
  conformance → **220 passed**; `ruff check .`, the strict UI audit, and catalog drift/settings/UI checks passed.
  The four local skips require real bubblewrap; `bwrap` is absent here, so this run does not claim real Linux
  confinement. Seatbelt profile rules have mocked unit coverage only; no real macOS integration run was performed.

### Added — P14 / WS-05c plugin client modules are served, and installed plugins load at all
- **`[ui].client_module` now means something on disk.** The address is derived in one module both ends import —
  `harness/sdk/plugin_static.py` (`CLIENT_MODULE_ROOT`, `CLIENT_MODULE_SUFFIXES`, `plugin_dir`, `client_module_url`,
  `client_module_file`, `client_module_trusted`), with `harness/kernel/plugin_paths.py` as the kernel-side façade —
  and `harness/frontends/web/server.py` registers
  `add_get("/static/plugins/{name}/{tail:.+}", _handle_plugin_static)` *ahead* of the client's own `/static/` tree.
  `static/plugins/obsidian.js` in the plugin `obsidian` is therefore fetched from
  `/static/plugins/obsidian/plugins/obsidian.js`: namespaced so two features may ship the same filename, and
  computed by the same function that answers the request, so an advertised URL cannot outlive its handler. It had
  to live in the SDK because `test_frontends_allowed_imports` lets a frontend import `harness.sdk`,
  `harness.kernel.commands` and `harness.kernel.bus` — widening that allow-list was the wrong fix.
- **Refusals are 404s, enforced twice.** An undeclared sibling file, a path leaving the plugin's `static/`, a
  suffix outside `.js .mjs .css`, a plugin the running daemon did not load, and a plugin discovered from a
  *workspace* all answer the same way, so the route cannot enumerate what a machine has installed. The same rule
  is applied at discovery: `PluginManifest.load(path, trust=tier.value)` clears an untrusted declaration with a
  `[warn]` instead of raising — `bootstrap.discover_all` is unguarded on purpose, so a raising gate would turn one
  workspace plugin's typo into a kernel that will not boot — and `_handle_plugin_static` re-checks per request,
  because `plugins.disable` records intent for the next start while the directory stays on disk.
- **The address is the plugin's name, three times over (R59).** The `<plugin>` segment is the registry key
  (`manifest.name`), not the folder the plugin happens to live in — the scaffold's directory comes from the command
  line, so the two differ routinely. Pinning that is what caught a subtler bug in the resolver itself: comparing
  `client_module_url(name, module)` against `f"{CLIENT_MODULE_ROOT}/{name}/{tail}"` mentions `name` on both sides,
  so it holds for *any* name and the "only the declared address is readable" guard was a tautology.
  `client_module_file` now refuses unless the segment is the plugin's own name, which is what the docstring had
  claimed all along — and which nothing could have caught without a test that tried to pin it.
- **A plugin you installed could never load (R60).** `harness sdk scaffold` writes `entry = "plugin:MyPlugin"`,
  nothing put the plugin directory on `sys.path`, `import_module` raised, and `load_all` filed it as
  `plugin.error` — so scaffolds, `<config>/plugins/…` and `<workspace>/.harness/plugins/…` trees were discovered,
  listed, and never instantiated: their settings, panels and modules could not appear in any UI. Reproduced first
  (`ModuleNotFoundError: No module named 'plugin'`), fixed by `PluginRegistry._import_entry`, which loads the
  manifest's own file under a unique `sys.modules` name. No `sys.path` mutation.
- **A declared panel could not be refreshed (R61).** `mountGeneric` passed `session_id` to every module-less panel
  and `Commands.dispatch` forwards arguments as keyword arguments, so any command that does not name `session_id`
  raised `TypeError` and painted nothing — the three built-in panels worked only because all three accept it, which
  is the kind of accident a plugin would pay for. `ui.contributions` now publishes each panel's `accepts` (the
  parameter names, from rows `commands.list` already builds) and the client sends the id only to a panel that
  declares it takes one; the catalogue carries `accepts`, so `--check` sees drift without booting a kernel.
  Pinned by `test_every_declared_panel_can_be_refreshed_with_the_args_the_client_sends`, and by the panel key set
  becoming `{id, title, command, renderer, plugin, accepts}`.
- **An installed plugin is not a capability family.** `settings_owner_index` reads the family from the directory a
  plugin was found under, which is `hooks` for `harness/plugins/hooks/<name>` and `plugins` — i.e. nothing — for
  `<config>/plugins/<name>`; the install root is now recognised and no family is reported, so the Plugins pane
  files it under "Other" instead of inventing a family heading and its settings take the default tab.
- **The scenario lane can now install a plugin.** `Scenario.config_files` maps a path under the config dir to
  either inline text or `@_fixtures/…`, `seed_tree()` writes it (and `seed_workspace` delegates to it), and both
  lanes seed *before* `bootstrap_kernel` — the only way a scenario can test an installed plugin rather than a
  built-in one. The DOM probe gained: click every declared tab and report what each pane painted
  (`panel_texts`), the module URLs the app was handed (`client_module_urls`), a per-URL fetch status from inside
  the page (`plugin_module_status`), and an optional click of a plugin card's switch (`plugin_disable`, with the
  before/after badge); `PLUGINS_PROBE` runs on the restart pass too, so "off on the next start" is checkable.
  New expectations: `dom_tabs_include`, `dom_tabs_exclude`, `dom_panel_text`, `dom_plugin_modules_served`,
  `dom_disable_plugin`, `dom_restart_tabs_include`, `dom_restart_tabs_exclude`.
- **Two scenarios, and what the lane refuses to claim.** `plugin_panel_appears_when_enabled` boots a web kernel
  with a fixture plugin at `<config>/plugins/probe-vault` and asserts the tab mounts from the declaration alone,
  both the plugin's own panel and the Plugins pane paint it, its module URL answers 200 to the browser, the pane's
  Disable switch repaints `Disable → Enable` / `off-next`, and the panel is *gone* on the next start while the
  Plugins tab remains. `settings_tab_from_plugin_schema` asserts the same fixture yields a `Probe Vault` settings
  tab (its own `x-ui.group`) and that the pane reports the plugin there. The lane cannot execute the module —
  Node's ESM loader refuses `http:` and a root-relative import from a `file://` base resolves to `file:///static/…`
  — so the scenario asserts the two ends around it (advertised URL, bytes served, panel mounted) rather than
  pretending to import it, and a real browser check stays the preview's job.
- Tests: `tests/frontends/test_plugin_static.py` (10: route order, declared-file-only, traversal via `%2e%2e`,
  uninstalled plugin, untrusted tier, the advertised URL is the URL that serves the bytes),
  `tests/kernel/test_plugin_static_paths.py` (15: URL/file mapping both ends, trust vocabulary, discovery dropping
  an untrusted module while keeping the panel), `test_ui_contributions.py` (21), `test_settings_groups.py` (9),
  plus the two scenarios and their `make_recordings.py` entries (the corpus stays byte-reproducible, so a synthetic
  recording nobody can regenerate is not allowed to exist). `harness catalog --check` all `[ok]`;
  `python tests/frontends/ui_audit.py --strict` 0 findings; `ruff check harness tests` clean.
  Full suite: `pytest -q` → **629 passed** (6:59), 31 tests more than P13b.

### Added — P13b / WS-05 follow-up: the Plugins & Integrations pane
- **The Studio now lists what it actually runs.** `harness/frontends/web/ui.toml` declares a fourth panel —
  `{ id = "plugins", command = "plugins.list", renderer = "list" }` — and `harness/frontends/web/static/panels/plugins.js`
  paints one card per **discovered** plugin (39 today): tier, what it `provides`, its capability, how many settings
  it brings and **which settings tab they are on**, and whether it declares panels, record renderers or a client
  module. Cards are grouped under the family the kernel found them in — a list the client does not keep — and
  nothing in the pane names a plugin. The pane is mounted by `PanelRegistry.mount()` from the declaration alone:
  `index.html` was not edited, which is the WS-05 promise demonstrated rather than described.
- **`plugins.list` grew up** (`harness/kernel/commands.py`): the row now carries `disabled`, `family`,
  `has_settings`, `settings_count`, `settings_group`, `panels`, `record_renderers`, `client_module` alongside the
  existing keys, so `harness plugin list`, the TUI and the browser read one answer. `loaded` stays the live truth
  (`registry.is_loaded`) and `disabled` the user's intent for the next start — the two are deliberately not
  collapsed into an "enabled" boolean, because nothing hot-unloads a plugin mid-session and a switch that implies
  otherwise is a lie. `Settings` and `Configure` therefore agree with the modal by construction: both are derived
  from `settings_owner_index` + `x-ui.group`.
- **Two switches, zero handlers.** Enable/Disable are `data-command="plugins.disable"` / `…enable"` attributes
  handled by `CommandBinder` (WS-05b), and `harness catalog --check --section ui` now gates them:
  `ui.data_command_bindings` is non-empty for the first time (`plugins.enable`, `plugins.disable`, both
  `registered: true`). The badge distinguishes `loaded` / `off next start` / `off` / `not loaded` so a pending change
  is visible without pretending it already happened, and the restart note says what to do — `harness daemon` has no
  `restart` subcommand, so "applies on next start" is the truth the pane tells rather than a caveat it hides.
  `not loaded` is deliberately not spelled "on next start": a plugin whose entry point or `setup()` failed looks
  identical in the data (see R57).
- **The panel contract was sharpened, not weakened.** `test_panels_are_declared_backed_and_mounted` required a
  `data-tab` in the client sources for every declared panel — a rule that contradicted runtime mounting (it failed
  the day a panel was *only* declared). It is now
  `test_panels_are_declared_backed_and_reachable`: declared ⇒ backed by a dispatchable command ⇒ paintable (a
  module registered in `app.js`, or one of the generic renderers), plus the mirror rule that no module may exist
  for an undeclared panel, plus `test_the_client_implements_exactly_the_renderers_a_manifest_may_name`, which pins
  `registry.js:GENERIC_RENDERERS` against `harness/sdk/manifest.py:VALID_UI_RENDERERS`.
- **Two defects found by running the DOM lane, both fixed.** The binder reported a *successful* command as failed
  because its `dispatchEvent` sat inside the command's `try` (a repaint that detaches the control threw, and the
  user saw a red "plugins.disable failed" notice for a write that landed); and `headless_timeline.mjs` let Node's
  own `Event`/`CustomEvent`/`FormData` globals shadow jsdom's, so `dispatchEvent` rejected the page's events and the
  whole announce path was untestable. See `review/CONSOLIDATION_LOG.md` (R55, R56).

### Changed — P13a / WS-05 settings surface: tabs by capability family, sections by owner
- **A plugin's settings get a tab that means something.** `harness/kernel/settings_schema.py` now defaults a
  property's `x-ui.group` from the plugin's **capability family** (`FAMILY_TABS`: hooks → `Hooks`, bridges →
  `Bridges & Integrations`, policy → `Policy & Safety`, context → `Context`, stores → `Storage & Records`,
  tools → `Tools`; providers and strategies reuse the kernel's existing `Models & Providers` /
  `Execution & Strategies`) instead of dumping everything that did not name a group into
  `Plugins & Integrations` — which held **22 of 44** panel properties. An explicit `x-ui.group` still wins
  (a plugin may say where it belongs), and the four schemas that had written the catch-all by hand
  (`bridge-mcp-client`, `bridge-mcp-server`, `bridge-shell-hooks`, `context-repo-map`) now omit it, so the tab
  list is derived end to end. `Plugins & Integrations` survives as `PLUGIN_TAB_DEFAULT` for a family nobody
  knows (a workspace plugin, a new directory) — tolerated, and kept from becoming a dumping ground by
  `tests/kernel/test_settings_groups.py`.
- **Every property now says who owns it.** `x-ui.owner` (the plugin id), `owner_title` (a heading: `hook-read-before-edit`
  → `Read Before Edit`, `bridge-mcp-client` → `MCP Client`) and `owner_summary` (the manifest's summary) are set
  by `with_ui_defaults` from an index shared with the catalogue (`settings_owner_index`), and `settings.js`
  renders a **section per owner inside a tab** — heading plus one-line summary above that plugin's controls. So a
  tab groups like *behaviour* and a heading names the integration, instead of 17 anonymous toggles under one label.
- **Two bugs the derivation exposed.** `SettingsPanel`'s constructor seeded the open tab with the literal
  `'Models & Providers'`, so a daemon with no provider plugin loaded opened the modal on "No settings configured
  in this group" while 44 settings existed — the open tab now comes from `settings.schema`'s `groups`, a
  remembered group is validated against it, and `setGroup` ignores a request for a tab that does not exist
  (**R53**). And `docs/catalog/SETTINGS.md` rendered its sections by walking a literal tuple of group names, so
  the rows that moved to new tabs silently disappeared from the document (**R54** — the same class as R47, one
  layer over: a generator with its own copy of a fact the kernel owns, which a freshness gate cannot see because
  it compares the file to itself). It now derives sections from the rows plus `ui.settings_groups`, and
  `test_settings_doc_has_a_section_for_every_tab` fails on either direction (shown by restoring the literal list:
  it names `Bridges & Integrations`, `Context`, `Hooks`).
- **New tests**: `tests/kernel/test_settings_groups.py` (8) — family → tab derivation, an explicit group winning,
  `plugin_display_name` producing headings rather than package names, owner metadata on every plugin property and
  on no kernel property, no tab invented for a plugin with nothing to configure, `settings.js` naming no tab in
  its logic (judged on code, not prose, per `_code_only`), the signature staying catalogue-compatible, and the
  document-coverage rule above. `docs/catalog/UI_CONTRACT.md` / `SETTINGS.md` regenerated: 8 tabs, hooks' 17 rows
  under `Hooks`.

### Added — P12 / WS-05b the client reads the registry, not a switch
- **Record painting is a registry, one module per kind.** `harness/frontends/web/static/registry.js` owns
  `RendererRegistry` (register / has / gaps / render), the eleven painters now in `static/renderers/*.js` —
  `user.message`, `assistant.message`, `tool.result`, `tool.decision`, `ui.card`, `approval.requested`,
  `approval.resolved`, `compaction.summary`, `turn.error`, `turn.cancelled`, `system.notice` — and
  `NO_RENDERER`, where a kind the timeline deliberately does not paint is excused *with a reason*.
  `renderRecord` is now a dispatch, so a plugin's kind is painted by the module that shipped with it instead of
  by a branch added to the app; a kind with neither renderer nor excuse prints
  `Unrendered record: <kind>` rather than disappearing. The switch it replaces was 88 lines of `case` bodies;
  the move is behaviour-preserving, which `reload_equals_live_web_dom` and `dom_equals_kernel_transcript` are
  the ones to prove it (both pass unchanged).
- **Panels are mounted from declarations.** `PanelRegistry` takes the `panels` from `ui.contributions`, keeps
  the host of each one (`static/panels/{artifact,sessions,economics}.js` for the built-ins, `panel-<id>`
  otherwise), creates the tab and host a *plugin* panel lacks, and gives a panel with no module of its own the
  generic renderer named in its declaration (`table | list | markdown | tree`). `switchStudioTab` asks the
  registry which tabs exist; the sessions pane's rows and the Spend pane's markup live in their own modules and
  the app keeps only what is cross-cutting (`noteSessionRows` adopts the active id, then the header updates).
- **`[data-command]` is dispatched by `CommandBinder`** — one delegated click listener, `data-args` as JSON,
  `@active` resolved to the session id — for controls with no runtime id and no local side effect; the shipped
  controls keep their handlers, and `harness catalog --check --section ui` fails a binding to a command the
  kernel does not expose. A declared panel's refresh button is the first user of the generic path.
- **The tests read the same source the client does** (see `review/CONSOLIDATION_LOG.md` §P12b for the four
  findings this phase produced, including `ui_audit.py`/`test_static_contract.py` quietly losing coverage the
  moment bodies moved out of `app.js`, and the catalogue counting a `data-command` example written in a doc
  comment). New pins: `NO_RENDERER` is parsed out of `registry.js` rather than kept twice, `RECORD_RENDERERS`,
  its imports and the files on disk must be one set, `static/panels/<id>.js` must be a *declared* panel, the
  client may only call methods it defines, and a crashed reload lane now fails as itself instead of as an empty
  timeline. 581 passed; four mutations were shown to fail the new tests before being reverted.

### Added — P12 / WS-05a feature-owned UI, kernel side
- **A plugin can declare UI** — `[ui]` in `harness_plugin.toml`: `panels` (`{id, title, command, renderer}`),
  `record_renderers` (the record kinds *this plugin's client module* paints) and an optional `client_module`.
  Parsed and validated in `harness/sdk/manifest.py` (`UiContribution`, `UiPanel`): unknown key, malformed or
  duplicate panel id, empty `command`, a `renderer` outside `table | tree | markdown | list`, a `client_module`
  outside `static/`, or a client module from a plugin whose trust isn't `builtin`/`config` all fail the load
  with the reason in the message.
- **The shipped client declares itself the same way**: `harness/frontends/web/ui.toml` lists the three studio
  panels (Artifacts ← `sessions.get`, Sessions ← `sessions.list`, Spend ← `economics`) under the source name
  `web`. `PluginRegistry.ui_contributions()` merges the loaded manifests and *clears on unload*, so disabling a
  feature removes its panel and its renderers without a single `if enabled` in the client.
- **Two boot-time commands**: `commands.list` (every dispatchable command with its argument names, `alias_of`,
  and `exposed` — false for the `form_submit` alias, so an alias is never offered to a button) and
  `ui.contributions` (panels + renderers + client modules + `settings_groups` + `record_kinds`). A panel whose
  command is not dispatchable is not served at all; it comes back in `unbacked_panels`, naming its owner.
- **A plugin's settings group is a tab** (`harness/kernel/settings_schema.order_groups`): membership comes from
  the aggregated properties and order from `x-ui.order` (lowest wins, then the built-in sequence, then
  alphabetical), so a feature can add — or place — a tab without a kernel edit. Previously
  `groups = [g for g in SETTINGS_GROUPS if g in present]` filtered the surface through a hand-kept list, which
  silently dropped any group the kernel had not heard of (**R47**).
- **`docs/catalog/UI_CONTRACT.md` grew the contract** (generated): the panel table with owner and backing
  command, undocumented `data-tab`s, `data-command` bindings against the dispatch table, the renderer modules,
  durable kinds with no renderer, and the settings tabs. `harness catalog --check --section ui` now **fails** on
  an unowned tab, an unbacked panel or a dangling binding instead of only reporting unregistered `api.js` calls.
- **New tests**: `tests/frontends/test_ui_contract.py` (10) — no placeholders, client bindings dispatchable,
  panels declared-and-mounted-and-backed, the studio pane holds hosts rather than feature markup, every durable
  record kind painted or excused (the excuse list must have a reason and goes red when a renderer appears),
  `settings.js` carries no schema data of its own, and this test file and the generated catalogue must agree.
  `tests/kernel/test_ui_contributions.py` (20) — the manifest validation matrix, the built-in file's own
  invariants (including that no Python file lists the client's panels), unload semantics, and both new commands.

### Added — P11b / WS-01 the record path, and the replay machinery in the SDK
- **`HARNESS_REPLAY=record` is wired into the scenario suite** (`tests/scenarios/_lib/record.py`): the mode
  takes the provider named by `HARNESS_RECORD_PROVIDER` out of the registry, registers a
  `RecordingProvider` tee under the fixture's own key (`replay`) in its place, and writes
  `provider.jsonl` plus a `provider.requests.json` sidecar (`{"provider", "model", "requests"}`) as the run
  proceeds. `HARNESS_RECORD_MODEL` chooses the model. The tee keeps the fixture key *and* the wrapped provider
  steps aside deliberately: key it as anything else and `kernel.provider` never reaches it, leave the wrapped
  one registered and a client request resolves past the tee — the run records nothing and still looks green
  (`harness/frontends/web/static/app.js` sends the provider of the selected model, and the built-in `mock`
  answers with plausible text).
- **A recording replays as the model it was made from**: `_lib.replay_provider.scenario_model` reads the id out
  of the sidecar, and the headless browser lane scopes the model chip by `data-provider` (`MODEL_PROVIDER`)
  instead of by id, reporting the selection (`model_selected` / `model_selected_provider`). Model ids are not
  unique across providers — `mock-v1` is listed by the built-in mock and by a fixture recorded from it — so an
  id-only selection silently stops testing the fixture. Synthetic fixtures ship no sidecar, so every existing
  golden still replays as `replay` / `replay-v1`: the 25 committed goldens are byte-identical.
- **`harness/sdk/testing/replay.py`** now holds the providers and the format — `ReplayProvider`,
  `RecordingProvider`, `encode_event` / `decode_event` / `load_streams` / `write_streams` / `request_summary`,
  exported from `harness.sdk.testing` — so a plugin can record and replay without importing the suite;
  `_lib/replay_provider.py` and `_lib/codec.py` are thin views of it. `ScriptedProvider` gained
  `strict_exhaustion` (default `False`, so small unit tests stay polite) and a `remaining` property.
- **New tests**: `tests/scenarios/test_record_replay.py` (4) records against the built-in `mock` provider and
  asserts record and replay agree in the kernel lane *and* through the shipped client, including the goldens;
  `tests/kernel/test_replay_provider.py` (10) pins the strictness split, the credential refusal, and the codec
  as a fixed point of a committed `provider.jsonl`; `tests/scenarios/test_normalize.py` (6) pins the
  stabiliser, including that an id in prose and the same id under `call_id` normalise to one token.
- **Recording cannot clobber the corpus**: `HARNESS_REPLAY=record` refuses to overwrite an existing
  `provider.jsonl` unless `HARNESS_RECORD_OVERWRITE=1` (`-k <name>` selects a scenario, not an intention to
  re-record the fixture every golden was derived from), and it says so before the run rather than after.
- **`make_recordings.py` is now the corpus's source**: it covers all 25 synthetic fixtures (`daemon_restart_web_dom`
  had only ever been written by hand), fails when a `recorded_from: synthetic` scenario has no entry, and
  `tests/scenarios/test_make_recordings.py` (4) proves regeneration is byte-identical and that the generator
  never touches a real recording.
- **pytest markers** `scenario` and `web` (`-m web` selects the 11 browser-lane cases); a scenario whose first
  run created goldens no longer has to be guessed at — `tests/scenarios/_lib/scenario.py:kind_of` reads the
  `kind` for collection without validating the file, so a half-written scenario fails its own test.
- **`docs/testing/SCENARIOS.md`**: how to run, add, record and update a scenario, with the symptom table for
  the failure modes above. The in-suite `tests/scenarios/README.md` gains the four scenarios that arrived with
  WS-02…WS-04 and the recording contract.

### Added — P11 / WS-04c the client renders the log
- **One renderer path in the shipped client.** `harness/frontends/web/static/app.js`: `reloadTranscript()`
  no longer re-derives a conversation from roles. It feeds every record `sessions.get` returns to
  `renderRecord(rec)`, a switch on `rec.kind` that calls *the same painters the live event handlers call*
  (`appendUserTurn`, `appendTurnBlock`/`streamAssistantText`/`finalizeStreamingBlock`, `appendToolCall`,
  `updateToolResult`, `updateToolDecision`, `appendCard`, `showApproval`/`resolveApprovalUI`,
  `appendNotice`). A refreshed page now shows the approval the user answered, the decision the policy made
  and the cards a tool emitted — all of which used to be dropped, because `user`/`assistant`/`system`
  cannot tell a decision from a notice. A reloaded tool card is appended as `running` and finished by its
  own records, so a call whose result never arrived stays visibly open rather than claiming success.
  `compaction.summary` paints as a markdown card; turn markers and `checkpoint.created`/`job.*` are
  history the timeline does not need.
- **`tool.decision` gained `plugin`, on the event and in the record alike.** The `fs-tools · write`
  subtitle on a tool card is part of *what was decided*, and until now only `tool.call` carried it — so
  the log could not reproduce the card. `ToolDecision`, the four gate publish sites and the record metadata
  match; `docs/events.schema.json`, `docs/catalog/EVENTS.md` and `docs/ARCHITECTURE.md` §4 regenerated, and
  `tests/frontends/test_static_contract.py` keeps the client reading only declared keys.
- **The derived search index projects v2 records.** `harness/plugins/stores/fts5_search/index.py`:
  `record_row()` reads `role`/`kind` (falling back to the v1 `source`/`type`), and both the live hook
  (`index()`, called by the runner per appended record) and the rebuild walk go through it. Before this,
  every row a running session indexed said `UNKNOWN`/`UNKNOWN` — visible as the `Source` column of a
  `search_memory` result.
- **`harness sessions reindex`** (new `harness sessions` group): `FTS5SearchIndex.rebuild_from_sessions`
  deletes and re-projects the table plus the `conversation_summaries` counts from the log, unconditionally —
  the old `maybe_reindex_from_sessions` only ran on an empty table, so a stale index could not be repaired.
  `--dry-run` sizes the job. A rebuild is a repair, not a rewrite: the log stays untouched.
- **Scenario `reload_equals_live_web_dom`** (the 25th) tests WS-04's claim from the browser's side:
  `reload_equals_live` compared against a *second boot* (the web lane had no live-side comparison at all —
  `sessions.get` on the same daemon would have made it vacuous), `dom_reload_contains_live_lines` (every
  line the live session painted is on screen after a refresh with no further click; live-only chrome
  exempt), and `reload_timeline_contains` pinning the approval, its resolution, the tool output and the
  skill card.
- **`docs/architecture/SESSION_LOG.md`**: the seven invariants with the test that enforces each, record
  anatomy and the empty-bag rule, the on-disk order for a step (and the two deliberately surprising orders),
  the three durability classes, the v1→v2 upgrade table, the client mapping with its two honest caveats, and
  what is deliberately not here. Linked from `README.md` §5 and `docs/ARCHITECTURE.md` §5.
- **`docs/catalog/RECORD_KINDS.md`** (generated, new `CATALOG_DOCS` entry): for every kind in
  `RECORD_KINDS` — model-visible (`MESSAGE_KINDS`), which durable event carries it
  (`EventPayload.record_kind`), and how many scenario transcripts hold one. A producer *scan* was tried
  first and dropped: turn markers are written through a generic marker path where the kind is a variable,
  so "not yet written" would have been a lie about `turn.error`. Measured coverage cannot lie: the reserved
  `job.*`/`goal.*` kinds and `tool.call` show `0 scenario(s)`.
- Goldens were re-baselined twice while landing this. The whole diff is `+ "plugin": "<owner>"` in 18
  `transcript.json` + 18 `events.json`, and 6 rows where a memory-search hit gained a real `Source` label
  (`UNKNOWN` → `user`) — one `timeline.txt` of 25 changed, for that label. `requests.json` and `http.json`
  are untouched: what the model reads and what the user sees are still the same, the log just explains
  itself now.
- Recorded deviation: the plan asked to keep `sessions.get`'s `history` key as a deprecated alias for one
  minor version. It returns `records` only — there is no in-tree consumer of the old name, and a second
  key to keep in step is the two-source pattern this workstream exists to remove.

### Added — P10 / WS-04b durable events, recorded policy facts, on-disk repair
- **Durability is now a declared property of an event.** `harness/sdk/events.py`:
  `EventPayload.record_kind` (the transcript kind the event mirrors), `EventPayload.transient` (the streamed
  set: `*.delta`, `cost.updated`, `context.assembled`, `model.started`, `compaction.started`, and the two
  WS-03 hygiene events whose docstrings already said they were live-only) and `carries_record` (whether the
  envelope embeds the record). `durable` is derived from `record_kind`, so a payload cannot advertise
  durability it does not implement. `docs/events.schema.json` gains the `events` table; `docs/catalog/EVENTS.md`
  gains the **In the log** column; both are generated and drift-checked.
- **`Event.record`.** A durable event carries the stored record it describes, emitted only when a record was
  actually written. Durable now: `turn.started` (the prompt), `turn.completed` / `turn.error` /
  `turn.cancelled` (the turn markers), `model.completed` (the assistant message), `tool.decision`,
  `approval.required` / `approval.resolved`, `ui.card`. `tool.result` and `compaction.completed` name a kind
  but pair by id — recorded in the schema as `carries_record: false` rather than quietly skipped.
- **Log-before-emit for turn events.** The prompt is recorded and only then is `turn.started` published (so a
  client is never told about a turn the log does not have); the turn's end appends its marker record first.
  `model.completed` is held by services until the assistant message it names exists, and flushed if a turn
  ends without one, so no event is lost and none points at nothing. Cancellation no longer writes a second
  "Turn cancelled by user." notification beside the marker.
- **The gate and the approval broker write records.** `tool.decision` per deny/ask/allow and `ui.card` per
  card, from the gate, with the session log passed into `PolicyGate.execute` — decisions and cards used to
  exist only as live events, which is why a reloaded session could not explain itself. The broker gained
  `log_for` (a session resolver, set by the bootstrap) and `resolve_recorded`, and `approval.resolved` now
  carries the `session_id` it previously omitted (R42: a client filtering events by session left the card
  pending until reload).
- **Unanswered tool calls are closed in the log.** `TranscriptLog.repair_dangling_tool_calls()` appends an
  explicit synthetic error result at the start of the next turn, labelled `metadata.synthetic` /
  `metadata.repair`, instead of every reader inventing its own repair in memory.
- `sessions.list`'s `message_count` counts messages (`MESSAGE_KINDS`), not records — turn markers and
  decisions live in the same file now — and a test pins that the counter's set and `MODEL_VISIBLE_KINDS`
  cannot drift.
- Fixed while landing this: `upgrade_record_v1` synthesised a `tool_results` bag for **any** record carrying
  `metadata.call_id`, which rewrote decision records into fake tool results on reload; the synthesis is now
  scoped to untyped v1 lines, and the store's metadata promotions are mirrored so a cached record and the
  line on disk are the same dict (also giving v1 tool-result lines their correct `tool.result` kind).
- Tests: `tests/kernel/test_durable_events.py` (13), including the spec's
  `test_every_durable_event_has_record_kind` and "a recorded fact never reaches the prompt".
  `tests/scenarios` goldens re-baselined: 18 `transcript.json` + 18 `events.json` changed, and `requests.json`,
  `http.json` and the DOM `timeline.txt` did not — the proof that adding history changed what is *recorded*,
  not what is *sent* or *painted*.

### Added — P9 / WS-04a the session log is the source of truth (typed records, derive_messages, fail-closed append)
- New `harness/kernel/transcript.py`: `TranscriptLog` owns the write path — one instance per session
  directory per process, shared by the runner, `Session.log` and `sessions.get`. Before this, the runner
  kept `transcript_records` in memory beside the JSONL file *and* built its own store instance over the
  same directory as the `Session`'s, so two objects each held a step counter and a cache of one truth.
  The log exposes `load`/`read`/`read_records`/`append`/`append_notice`/`last_compaction`, and its
  `records` list is the same object consumers (message building, the WS-03 read ledger) read from.
- Records are typed and versioned: `RECORD_KINDS` (20 kinds) + `infer_record_kind` +
  `TranscriptRecord.kind`/`v = 2` in `harness/sdk/types.py`. `upgrade_record_v1` upgrades v1 lines **on
  read** and never rewrites them — an appended line is the audit trail of what was recorded when. The
  upgrade is lossless (store annotations such as `call_id`, `tool_name`, `is_error`, `full_offset`,
  `truncated_fields` survive) and idempotent (`upgrade(v2) == v2`, because it only writes back a bag it
  actually fills), which is what makes the live cache and a reload byte-equal.
- **`derive_messages` is the only producer of a model request's message list.** Kind-driven rather than
  role-driven: `user.message`, `assistant.message`, `tool.result`, `compaction.summary` and a
  `system.notice` marked `model_visible` reach the model; decisions, approvals, cards and turn markers are
  in the log for the user and not in the request. Dangling `tool_use` repair (explicit synthetic error
  result), thinking-only turns stay in the transcript but off the wire, compaction slices the *derived
  view* only, media is dropped with an explicit `[media omitted: …]` note. `records_to_messages` is kept
  as an alias so nothing else can become a second producer.
- **Appending is fail-closed.** `TranscriptAppendError` from the store aborts the turn *before* the model
  is called — the user prompt is recorded inside the guarded region — with
  `turn.error(kind="transcript_append_failed", retryable=False)` and
  `TurnResult(stop_reason="transcript_append_failed")` (retrying would double-append what was already
  logged).
- `sessions.get` returns the session's `records` (upgraded) alongside `messages`, so a client can render
  and persist a session from the log alone.
- Catalog: `_scan_producers` now requires a publish marker (`Event(` / `publish` / `type=`) next to each
  occurrence of the name, instead of matching the name anywhere in the file — otherwise a file that merely
  *declares* a vocabulary (the new `RECORD_KINDS`) is listed as a producer of it.
- Tests: 33 new — `tests/kernel/test_derive_messages.py` (16, incl. a parametrised proof that no
  model-invisible kind is producible through `derive_messages`), `tests/kernel/test_record_upgrade_v1.py`
  (12, incl. real v1 files read through the store and `sessions.get` preserving `full_offset`),
  `tests/kernel/test_log_is_source.py` (4, incl. "a store that cannot append stops the turn" and
  "a record survives a reload with its kind"). Full `pytest -q` → 507 passed; the 24 scenario goldens
  were re-baselined and the only change in them is `"kind"`/`"v"` (plus one dropped empty `metadata`).
- Docs: `docs/ARCHITECTURE.md` §5 states the contract; the scenario count in `README.md` and
  `docs/ARCHITECTURE.md` was stale at 21 and is now 24.

### Added — P8 / WS-03 loop hygiene (tool-result size guards, cooperative timeout, read-before-edit)
- New kernel seam: `HookEvent.TOOL_RESULT_FINALIZE` fires in the gate after the handler returns and
  **before** secret sanitisation and the `kernel.limits.tool_result_bytes` truncation, so a hook can
  replace the result the model reads (`HookOutcome.result` with `action="mutate"`). Hooks at one event
  now run in `Hook.order` (default 500) order.
- Cooperative tool timeout: the gate signals `ToolContext.cancel_event` at the deadline, waits
  `kernel.limits.tool_grace_s` (new, default 5 s) for the handler to return partial work, then cancels
  the task — and terminates processes registered with `ToolContext.track_process()`. Whatever a tool
  streamed via `emit_output_delta` is prefixed onto the timeout result instead of being discarded, and
  the result stays `is_error` so an incomplete answer never reads as a success. `run_command` now
  registers its child and watches `cancel_event` while waiting (it used to leak a live subprocess past
  the turn).
- `HookContext.turn_state`: a per-turn dict shared by every hook, seeded with `records` (the live
  transcript list) so a guard can derive session history instead of keeping module-level state.
- `view_file` / `write_to_file` / `edit_file` return `metadata.path` + `metadata.read_sha256`.
- Four built-in hook plugins, each with a manifest, `settings.schema.json`, README and its own tests:
  `hook-tool-result-spill` (whole output to `.harness/sessions/<sid>/tool-output/<call_id>.txt`, capped
  spool), `hook-tool-result-pruner` (head + tail with an explicit omission notice instead of head-only
  truncation), `hook-repeat-call-reminder` (counts identical calls whose *result* also did not change;
  `block_after` can stop the loop), `hook-read-before-edit` (refuses a surgical edit of a file that was
  never read or changed since; transcript-derived, `mode: block|warn|off`).
- Two live-only events: `tool.result.pruned`, `tool.repeat_detected` (typed payloads in
  `harness/sdk/events.py`, registered in the client's `KNOWN_EVENT_TYPES`, documented in
  `docs/ARCHITECTURE.md` and the generated event catalogue).
- Four scenarios: `huge_tool_output_pruned_and_spilled`, `repeat_call_reminder`,
  `edit_without_read_blocked` (goldens recorded). Tests: 6 new kernel-seam files/cases for the finalize
  hook, 6 for the cooperative timeout, 5 end-to-end through a booted kernel, 25 for the hook plugins
  (kit conformance, declared settings, and the exact model-visible strings).
- Turning `plugins.hook-tool-result-pruner.enabled` off demonstrably returns head-only truncation
  (`test_turning_the_pruner_off_brings_back_head_only_truncation`), which is what makes the guard
  load-bearing rather than decorative.

### Changed — P7 / WS-02 generated catalogues and freshness gates
- New `harness catalog` group: `gen` rewrites `docs/catalog/{PLUGINS,TOOLS,SETTINGS,EVENTS,COMMANDS,UI_CONTRACT}.md`
  and `catalog.json` from a booted kernel; `--check [section]` gates them (`drift`, `settings`, `readmes`, `ui`,
  `scenarios`, `refs`); `readme <plugin>` prints the README Configuration table. `docs/PLUGIN_CATALOG.md` is deleted
  in favour of the generated plugin list.
- Settings ownership moved to the plugins: nine built-in plugins now ship `settings.schema.json` (with
  `x-namespace`, `x-alias-namespaces` for compatibility spellings and `x-secrets` for the secret keys they read), the
  registry registers them at load, and `Commands.settings_schema` is the aggregation of registered schemas.
  `kernel.request_timeout_s` and `kernel.plugins.capabilities` are declared; `kernel.security.jail_enabled` and
  `plugins.openai-compat.default_model` — settings the panel offered with no reader — are gone.
- Fixed: provider `base_url` / mock `script_file` settings could never resolve (the namespaced view prefixed the
  absolute key the plugin read), so `harness run --mock-script` and a hand-edited `settings.json` were ignored (R36);
  `kernel.max_tool_iterations` was shown in the panel but never applied — `TurnRunner` now seeds `max_iterations`
  from it (R38); `harness plugin info <name>` raised `AttributeError` (R35); the web client carried a second,
  invented copy of the settings schema that replaced the kernel's on any fetch error (R32).
- `bridge-mcp-client` dropped its third, unreachable `extensions.mcp_servers` fallback (R37).
- New tests: `tests/kernel/test_settings_declared.py` (every read declared; every panel setting read),
  `tests/kernel/test_catalog.py` (catalogue = tree, commands = dispatcher, events = SDK, committed docs = fresh boot),
  `tests/plugins/test_provider_settings.py`, and a plugin-to-plugin import guard in `tests/kernel/test_import_boundaries.py`.
- CI runs `harness catalog --check --section drift --section settings --section ui` in place of the single-file
  events-schema diff (the drift check covers that file too, and `pytest -q` runs it as well).

### Changed — P6 version and document hygiene
- Released sections `[0.2.1]`, `[0.2.0]`, `[0.1.0]` below are restored byte-for-byte to their git tags; the post-tag
  edits made in `158c836` are recorded in the "`main @ 158c836` remediation" block of this `[Unreleased]` section.
  `tests/test_docs_and_versions.py` pins the released sections so this cannot recur unnoticed.
- `/health` reports `harness.__version__` (was a literal `"2.0.0"`).
- Command errors that carry a status keep it: `TurnInProgress` is HTTP 409 (was flattened to 400).
- `harness.kernel.commands.BUILTIN_COMMANDS` is the catalogue of built-in commands; `dispatch` resolves through it.
- `TranscriptStore.list_sessions` removed from the SDK contract, scaffold and `jsonl_transcript` (a store holds one
  session; listing is `SessionManager.list_sessions`); the unused `sessions_index.jsonl` is no longer written.
- New `docs/ARCHITECTURE.md` — process model, kernel modules, command and event catalogues, session lifecycle, web
  authentication, test map — checked against the code by `tests/test_docs_and_versions.py`.
- The 22 research syntheses moved from `docs/` to `docs/research/`; README documents reading order and marks
  `docs/research/`, `review/` and `additions/` as history.
- Scenario goldens no longer pin `model.started.request_tokens_estimate` (it varies with the tmp path length).

### Changed — P5 one test estate (`review/CONSOLIDATION_LOG.md`)
- The flow-of-information suite moved from `additions/scenarios` to **`tests/scenarios`** and is part of plain `pytest -q` (kernel, plugins, frontends, e2e and the 21 record/replay scenarios in one invocation; CI runs it with the jsdom DOM lane, `SCENARIO_DOM_WAIT_MS=6000`).
- One `package.json` at the repository root (`jsdom 24.1.3`, `tailwindcss 3.4.17`; `npm ci`); the nested `tests/scenarios/_lib/node/package.json` is gone and the headless script resolves jsdom from the root install.
- `tests/frontends/ui_smoke.mjs`, `tests/frontends/test_ui_smoke.py` and `review/tools/ui_smoke.mjs` deleted; their remaining unique checks (settings modal opens with schema-driven group tabs, no browser console errors) are scenario expectations (`settings_groups_include`, `no_console_errors`) asserted by `plain_answer_web`.
- `tests/e2e/test_scenarios_s1_s6.py::test_scenario_s1_safe_daemon` deleted — it tested a throw-away aiohttp app; the real 401/415/421 checks live in `tests/frontends/test_web_server.py`.
- `additions/tools/ui_audit.py` → `tests/frontends/ui_audit.py` (still a CLI: `python tests/frontends/ui_audit.py --strict`).

### Changed — P4 provider truth lives in the provider (`review/CONSOLIDATION_LOG.md`)
- **SDK contract:** `Provider.is_configured() -> bool` and `async Provider.is_reachable() -> bool | None` are part of `harness.sdk.contracts.Provider`; `list_models()` returns the provider's catalogue with or without credentials (no network call when unconfigured, `ProviderError(kind="auth")` only when real credentials are rejected). `ProviderKit.test_readiness()` checks both; the scaffold and `docs/PORTING_RECIPES.md` show them. **Breaking for third-party providers:** the registry now refuses to register a provider that lacks any contract member (`PluginLoadError` naming them).
- **`models.list` rows are `harness.sdk.types.ModelRow`:** `{id, display_name, provider, context_tokens, input_usd_per_mtok, output_usd_per_mtok, configured, reachable, status}` with `status` ∈ `ready` / `needs_key` / `unreachable`. Probes run concurrently, a failing provider is logged at DEBUG and skipped, the command never raises. `Commands._is_provider_configured()` (secret-name heuristics) is gone.
- **Fixed: Ollama was offered as a working model while not running.** `OllamaProvider` probes `GET /api/tags` with a 300 ms timeout, caches the verdict (up or down) for 30 s, lists installed models when up and its catalogue — flagged `unreachable` — when down.
- **Fixed: hosted providers disappeared from the picker without a key** (and logged a WARNING per page load). Anthropic, Gemini, OpenAI, OpenRouter and DeepSeek now appear as `needs_key`; a self-hosted OpenAI-compatible endpoint (vLLM, LM Studio) is valid without a key.
- Web model picker groups by `status` (Ready / Needs API key / Not running); CLI `/models` shows a Status column.

### Changed — P3 one styling system, no facade (`review/CONSOLIDATION_LOG.md`)
- **Two stylesheets, one system.** `static/styles.css` is rewritten as components only (tokens, frame, conversation, banners, studio, cards, settings, inspector; 165 classes, all used) and the utilities the templates use (`flex`, `gap-2`, `text-xs`, `truncate`, `hidden`, …) come from `static/vendor/tw.css`, compiled from `index.html` + `*.js` by `npm run build:css` (`tailwind.config.cjs`, Tailwind 3.4.17, preflight off, committed, CI rebuilds and diffs it). No CDN, CSP unchanged.
- **Fixed: nothing marked `hidden` was hidden.** The page had no `.hidden` rule, so the approval banner, the proceed banner, the onboarding banner, the Stop button, the session list and the economics view were all visible on load; four undefined CSS custom properties left the settings dialog without a background. `tests/frontends/test_ui_coherence.py` now checks the cascade in jsdom.
- **Facade removed or made real:** `tool.output.delta` streams into the tool card's output (the hidden terminal drawer is gone); artifact cards render in the Artifacts tab (with a live proceed banner) as well as the timeline; the slash menu (`/plan /diff /compact …`, which no kernel command interpreted) is gone; title bar / status bar show the workspace (`/health.workspace`, new), permission mode (`kernel.security.permission_mode`, refreshed on `settings.changed`), active session title, selected model, daemon version and SSE state instead of `🪝 main`, `Autonomous`, `UTF-8`, `Sovereign Daemon Online`; the hard-coded welcome turn is replaced by the empty-timeline hint.
- Zero inline `style=` attributes in `index.html`, `app.js`, `cards.js` (progress cards use a native `<progress>`); state is carried by data attributes (`data-status`, `data-level`, `data-state`, `data-stability`, `data-error`).
- `additions/tools/ui_audit.py --strict` is now the UI gate (missing/unreferenced ids, undefined/unused classes, inline styles, stylesheet set and order, foreign scripts) and runs in CI.

### Changed — P2 kernel-owned session lifecycle (`review/CONSOLIDATION_LOG.md`)
- **The kernel resumes and creates sessions; frontends follow.** `bootstrap_kernel(resume_session=True)` makes the most recently updated session that has turns the active one (`SessionManager.resume_last()`, publishes `session.switched`); `submit_turn` without a `session_id` uses it or creates one and announces it with `session.started`. The web client no longer creates a session on every page load (previously each refresh left an empty session behind), connects SSE before fetching the transcript, adopts the kernel-chosen id from `session.started`, and shows an empty-timeline hint until the first prompt. `CliApp` no longer mints its own `session_<uuid>`: it uses the kernel's active session or adopts the one created for its first prompt.
- **`sessions.list` rows are `harness.sdk.types.SessionRow`** `{session_id, workspace, created_at, updated_at, title, turns, message_count, active, busy}`, newest activity first; `title` is the first user prompt (≤60 chars). The web picker shows title / turns / last activity; the CLI `/sessions` table does the same.
- `harness run` starts a new session per invocation unless `--continue/-c`; `harness interactive` resumes the last conversation unless `--new/-n`.
- Flow test: new scenario `daemon_restart_web_dom` (two prompts, daemon restart, page reload → same session, one row, full transcript on load); seven web goldens lose the phantom boot `session.started`.

### Changed — P1 typed event/card contract (`review/CONSOLIDATION_LOG.md`)
- **Every event payload is now a dataclass** in `harness/sdk/events.py` (`EVENT_PAYLOADS`, one `@event_payload` class per type, e.g. `ToolDecision`, `CostUpdated`, `UiCard`). `Event(data=<payload>)` serialises it and refuses a payload of the wrong type; `validate_event()` checks any envelope (required fields, leaf types, no undeclared keys, card validity for `ui.card`). All 45 kernel/plugin emit sites use the dataclasses. `harness events schema` now emits `$defs/payloads/<type>` sub-schemas with an `allOf`/`if`/`then` dispatch on `type`; `docs/events.schema.json` regenerated.
- **One spend shape — `EconomicsSnapshot`** `{turn_usd, session_usd, lifetime_usd, cap_usd, unknown_pricing, usage}` (`harness/sdk/types.py`, built by `harness/kernel/economics.py`). It is what the `economics` command returns, what `cost.updated` carries and what `GET /health` embeds as `spend`. The previous `{spend_cap_usd, totals}` command result is gone; the CLI `/economics` panel and the web Economics tab read the snapshot.
- **Card wire shape is the SDK's.** `cards.js` renders `Card{kind, id, title, data}` with the keys in `harness.sdk.cards.CARD_FIELDS` (markdown, code, diff, table, notice, progress, tree, approval, artifact, form) plus web-internal `tool`/`task`/`compaction`; the dead `plan` kind and every `a || b || c` alias chain are removed. `notice` gains the `success` level; `validate_card` rejects undeclared `data` keys. `ui.card` events from `bridge-shell-hooks` and `form.submit` now carry a real `Card`.
- `task.started` and `settings.changed` are emitted for the first time (background inbox; `settings.set`); the never-emitted `log` type is removed from the catalogue (28 → 29 types net).
- `CostLedger.record()` takes `session_id`; `totals(session_id=…)` filters by it, so session spend ≠ lifetime spend. The runner keeps `cost_usd=None` (instead of `0.0`) when a model has no known pricing, so `unknown_pricing` and the ledger agree.
- `reloadTranscript()` reads `TranscriptRecord` as written (`role`, `tool_calls[{id,name,arguments}]`, `tool_results[{tool_use_id,content,is_error}]`) and restores tool outputs after a page refresh.

### Changed — `main @ 158c836` remediation (pre-consolidation; these bullets were written into the released `[0.2.1]` section after the tag and are restored here)
- Web frontend unified on modular ES modules (`app.js`, `cards.js`, `api.js`, `events.js`, `settings.js`) with named `addEventListener` subscriptions for every event type in `events.schema.json`.
- Removed legacy inline scripts, duplicate `<head>`/`<body>` tags, fake confirmation `data-notice` attributes and `data-onclick="noop"` buttons; replaced 17 static mockup settings pages with the schema-driven `SettingsPanel` (`settings.schema`); first-run onboarding banner; CSP `script-src 'self'`.
- Removed 21 unrouted legacy REST endpoints (including `/api/sessions/{session_id}/turn` from 0.2.1) and ~650 lines of dead handlers from `server.py`; `/commands/{name}` + `/events` are the API. `sessions_switch` validates ids; command exceptions map to 400/404.
- Added `tests/frontends/test_static_contract.py`. (The jsdom smoke test and the credential-name-based model `<optgroup>` added at the same time were superseded in P5 and P4 above.)

### Fixed
- `tool.call.arguments` on the bus aliased the gate's mutable `current_args` dict, so SSE consumers saw hook/policy rewrites (absolute paths) depending on queue timing; payload serialisation now snapshots the data.
- `remember` no longer emits a notice card duplicating its own result text.

### Added
- `tests/conftest.py::event_contract_guard` — autouse fixture: every event published by non-test code during the suite must pass `validate_event`.
- `tests/frontends/test_static_contract.py`: `test_event_handlers_read_only_contract_fields` (every `data.<key>` an `app.js` handler reads ⊆ that event's payload schema, one level of delegation followed), `test_card_renderers_read_only_contract_fields` (`cards.js` ⊆ `CARD_FIELDS`), `test_transcript_reload_reads_transcript_record_fields`; the scenario suite validates every observed bus event / SSE frame against the contract.

### Fixed (earlier in this cycle)
- **Economics tab and header spend indicator always showed `$0.0000` / `0` tokens.** `renderEconomicsTab()` / `updateEconomics()` read `session_usd`, `cap_usd`, `token_usage.*` and `totals.total_cost_usd`, none of which the `economics` command emits (`{spend_cap_usd, totals: Usage.to_dict()}`). They now read the real keys and no longer fall back to a literal `$5.00` cap. Contract test: `tests/frontends/test_static_contract.py::test_economics_payload_keys_match_app_js`. (Review: `review/CODE_REVIEW_158c836.md`.)
- **Workspace memory did not round-trip.** `remember`, `search_memory`, the FTS5 store and the `memories` context source each opened a *different* `memory.db` (`.harness/tools/<tool>/`, `.harness/plugins/<plugin>/`, `.harness/`), so a remembered fact could never be searched or injected into a later session. All four now use `harness.sdk.paths.memory_db_path(workspace)` = `<workspace>/.harness/memory.db`; the FTS5 store also re-indexes from the real `.harness/sessions` directory. `search_memory` now searches remembered facts as well as transcript turns. Kernel-level regression test: `tests/kernel/test_memory_roundtrip.py`.
- **MCP tools could never execute inside the kernel.** `bridge-mcp-client` handlers took `(call: ToolCall, ctx)` while the gate invokes `handler(args: dict, ctx)`; every MCP call failed with `'dict' object has no attribute 'arguments'`. Handlers now follow the kernel contract. Regression test through the real gate: `tests/kernel/test_mcp_tool_through_kernel.py`.
- `bridge-mcp-client` now reads servers from its own settings namespace (`plugins.bridge-mcp-client.servers`) in addition to the legacy global keys.
- MCP risk classification recognises the spec-standard `readOnlyHint` / `destructiveHint` / `openWorldHint` annotations (previously only non-standard `readOnly` / `destructive` / `openWorld`), so read-only MCP tools no longer require approval in `ask` mode.

### Added
- `harness.sdk.paths` — canonical shared workspace paths (`harness_dir`, `memory_db_path`, `sessions_dir`).

---

## [0.2.1] - 2026-10-15

### Remediation Release: Full End-to-End Cohesion & Security Hardening

Release `0.2.1` addresses all architectural, security, and runtime gaps identified during the post-v0.2.0 audit (`review/CODE_REVIEW_v0.2.0.md` §9 Steps 1–6), establishing complete end-to-end cohesion between the microkernel, web frontend, agent loop, and plugin ecosystem.

#### Fixed & Remediated
- **Agent Loop Memory & Transcript Store Wiring (Step 1)**:
  - Corrected `TurnRecord` dataclass serialization mismatch and integrated session transcripts into `TurnRunner`.
  - Fixed model context feeding so tool results, error payloads, and conversational history persist and inform subsequent turns across sessions.
  - Added hermetic roundtrip verification tests (`tests/kernel/test_turn_roundtrip.py`).
- **Turn Endpoints & Tool Result Previews (Step 2)**:
  - Aligned `/api/sessions/{session_id}/turn` endpoint with dataclass-based request and response models.
  - Implemented streaming SSE and JSON fallback for real-time turn execution and tool result previews (`render_preview` contract).
  - Resolved provider resolution edge cases for scripted and external provider configurations (`tests/frontends/test_web_turn.py`).
- **Modular Frontend Unification & Strict CSP (Step 3)**:
  - Fully unified web frontend on modular ES modules (`app.js`, `bus.js`, `api.js`, `state.js`, `views/`).
  - Removed legacy inline scripts, inline styles, and `alert()` / `confirm()` popups in favor of native UI notifications and modals.
  - Enforced strict Content Security Policy (`default-src 'self'`) across static serving routes.
- **Security Hardening (Step 4)**:
  - Enforced POSIX file permissions `0600` on credentials, secrets, and settings files (`~/.harness/settings.json`, `~/.harness/secrets.json`).
  - Implemented single-use ticket and signed cookie authentication for SSE event streams, preventing unauthorized stream interception.
  - Added secret redaction filters preventing token exposure in settings GET endpoints and logs.
- **Dead v1 Tree Removal & Isolation Hardening (Step 5)**:
  - Removed dead legacy v1 packages (`harness/desktop`, `harness/web`, `harness/core`, etc.).
  - Hardened `local-executor` command execution with `DEVNULL` stdin to prevent zombie background processes from hanging on interactive prompts.
  - Made plugin data directory provisioning strictly lazy to prevent unwanted directory scaffolding.
  - Updated PyInstaller packaging spec with dynamic discovery of all 35 plugin packages.
- **Documentation Alignment (Step 6)**:
  - Reconciled `README.md`, `SECURITY.md`, and `docs/` with actual microkernel implementation and plugin inventory.
  - Formally documented `~/.harness/settings.json` architecture in `review/DECISIONS.md` (Decision D17).

---

## [0.2.0] - 2026-10-15

### Major Milestone: Modular Microkernel Architecture & Remediation

Release `0.2.0` represents a complete architectural overhaul and remediation of Harness-Zero, transitioning from an experimental monolith to a resilient, microkernel-and-plugin architecture with strict boundary enforcement, dual-origin web sandboxing, and developer SDK tooling.

---

### Added

#### Core Microkernel & SDK (`KRN-01` – `KRN-12`)
- **Microkernel Core (`harness.kernel`)**: Decoupled core runtime containing:
  - `TurnRunner` & `KernelServices`: Standardized agent turn loop supporting multi-turn memory, tool execution, and reactive background inbox wakeups.
  - `PolicyGate` & `WorkspaceJail`: Ordered security gates (Order 10: Workspace Jail, Order 50: Risk Classifier, Order 60: Tirith Security, Order 900: Permission Modes).
  - `ContextAssembler`: Multi-tier prefix-invariant context assembler with token estimation and compaction support.
  - `SettingsStore` & `Secrets`: Schema-validated settings store at `~/.harness/settings.json` (mode 0600) with in-memory CLI overrides (`set_override`) and secure credential management via `Secrets` (mode 0600).
  - `SessionManager`: Thread-safe session lifecycle and transcript storage tracking turns, model calls, and tool executions.
  - `EventBus`: Central pub/sub message bus with a 500-event ring buffer and `Last-Event-ID` replay.
- **Typed `harness.sdk`**:
  - `Protocol` interfaces: `Tool`, `Provider`, `ContextSource`, `Policy`, `Executor`, `Strategy`, `Bridge`, `Store`.
  - Testing kits in `harness.sdk.testing`: `ToolKit`, `ProviderKit`, `ContextKit`, `PolicyKit`, `ExecutorKit`, `StrategyKit`, `BridgeKit`, `StoreKit`, `HookKit`.
  - Conformance test runners validating streaming deltas, AST invariance, boundary checks, and execution idempotency.
- **Boundary Verification**: Automated tests and doctor audits enforcing that plugins only import `harness.sdk` and standard library, and the kernel imports no plugins.

#### Plugin Ecosystem (35 Built-in Plugins)
- Standardized under `harness/plugins/` across 8 component categories:
  - **Providers (5)**: `openai-compat` (universal OpenAI/DeepSeek/vLLM/OpenRouter), `provider-anthropic` (Messages API with prompt caching & thinking blocks), `provider-gemini` (Google Gemini with native tool calling), `provider-ollama` (local models with native tool calling), `provider-mock` (deterministic `ScriptedProvider`).
  - **Tools (7)**: `fs-tools` (`view_file`, `write_to_file`, `edit_file`), `search-tools` (`search_codebase` via ripgrep and python fallback), `shell-tools` (`run_command`, `manage_task`), `tool-artifacts` (`create_artifact`), `tool-memory` (`remember`, `search_memory`), `tool-skills` (`view_skill`), `tool-screenshot-macos` (`capture_screen`).
  - **Policies (4)**: `policy-workspace-jail` (path traversal guard), `policy-risk-classifier` (risk classes: READ, WRITE, EXEC, DESTRUCTIVE), `policy-tirith` (secret leak, SSRF, and destructive command auditing), `policy-permission-modes` (`plan`, `ask`, `allow`, `bypass`).
  - **Context Sources (6)**: `context-identity`, `context-instructions` (`AGENTS.md`, `CLAUDE.md`, etc.), `context-tool-guidance`, `context-skills-catalog`, `context-repo-map` (AST symbol outline), `context-memories`.
  - **Executors (2)**: `local-executor` (process group isolation, `DEVNULL` stdin, pager configuration, streaming stdout/stderr), `executor-worktree` (git worktree isolation for collision-free parallel edits).
  - **Stores (4)**: `store-jsonl-transcript` (append-only JSONL dual transcript store), `store-fts5-search` (SQLite FTS5 full-text index), `store-fs-artifacts` (isolated filesystem artifact storage), `store-cost-ledger` (per-model token economics and spend tracking).
  - **Strategies (2)**: `strategy-single-loop` (standard reactive autonomous loop), `strategy-architect-editor` (two-phase architect/editor coding with diff evaluation).
  - **Hooks & Bridges (5)**: `hook-checkpoints` (shadow git snapshots before mutating tools), `bridge-shell-hooks` (Claude Code-compatible shell hooks), `bridge-mcp-client` (stdio JSON-RPC MCP client), `bridge-mcp-server` (`harness mcp` stdio server), `bridge-acp` (`harness acp` Agent Client Protocol stdio server).
- **Plugin Manifests**: Each plugin configured via `harness_plugin.toml` with declared capabilities, settings schemas, and dependencies.

#### Web Studio & Dual-Origin Sandbox (`harness/frontends/web`)
- **Modular Frontend Architecture**: Built on vanilla ES modules (`app.js`, `api.js`, `events.js`, `cards.js`, `settings.js`, `styles.css`) with zero build step and zero external CDN dependencies.
- **Strict Content Security Policy (CSP)**: `default-src 'self'`, `script-src 'self'` (zero `'unsafe-inline'`, zero `'unsafe-eval'`), and frame isolation.
- **Dual-Origin SOP Sandbox**:
  - Control & API Daemon on `:8000` (`/commands/*`, `/events`, `/health`).
  - Artifact Sandbox on `:8001` (`/artifacts/*`) enforcing `default-src 'none'; script-src 'none'` to isolate agent-generated HTML/SVG from tokens and local storage.
- **Secure Authentication & CSRF Protection**:
  - Bearer token / `X-Harness-Token` authentication for API calls.
  - `HttpOnly` session cookie and short-lived single-use tickets (`POST /auth/ticket`) for EventSource SSE streaming (no tokens in query strings).
  - Host header rebinding validation (HTTP 421) and Origin / Sec-Fetch-Site checking (HTTP 403).
- **Interactive UI Cards & Streaming**:
  - Delegated event handling for interactive tool approval cards, forms, and proceed banners.
  - Streaming character markdown and reasoning thinking blocks.
  - Schema-driven settings panel reading kernel configurations with secrets masked.
  - Real-time token economics tracker and context assembler inspector.

#### Developer Tooling & CLI (`harness/cli.py`)
- **CLI Commands**:
  - `harness run`: Single autonomous turn execution with `--provider`, `--model`, and `--mock-script` (applied in-memory before plugin boot).
  - `harness ui`: Web studio server daemon launcher.
  - `harness mcp` & `harness acp`: Stdio protocol bridges for external hosts and editor integration.
  - `harness plugin new`: Scaffolding generator for any of the 8 component kinds.
  - `harness plugin doctor`: Comprehensive manifest, boundary, and capability audit across plugins (`--all`).
  - `harness plugin test`: Conformance test kit runner.
  - `harness events schema`: JSON Schema exporter for the unified event protocol.

---

### Changed
- **Config & Workspace Hermeticity**: Configuration cleanly honors `HARNESS_CONFIG_DIR`, defaulting to `$HOME/.harness` with 0600 file permissions and 0700 directory permissions.
- **Lazy Plugin Data Directories**: Kernel creates `.harness/plugins/<name>/` lazily on first access rather than generating 35 empty directories on startup.
- **Executor Hardening**: Local executor sets `GIT_PAGER=cat`, `GIT_TERMINAL_PROMPT=0`, `PAGER=cat`, and defaults non-interactive stdin to `DEVNULL` to prevent command hangs.
- **Turn Result Serialization**: Commands dispatch serialization cleanly handles nested dataclasses (`TurnResult`, `ExecResult`).

---

### Removed & Quarantined
- **Deleted Dead v1 Monolith**:
  - Deleted obsolete packages: `harness/core/`, `harness/memory/`, `harness/models/`, `harness/sandboxing/`, `harness/skills/`, `harness/artifacts/`, and `harness/desktop/`.
  - Deleted 10 legacy root-level test files that existed solely to cover obsolete v1 code.
  - Removed superseded monolithic `harness/experimental/architect_editor.py`.
- **Quarantined in `harness/experimental/`**:
  - `battle_arena.py`: Multi-agent git worktree battle evaluator with self-contained `WorktreeManager`.
  - `spawn_ledger.py`: Hierarchical subagent spawn tracking prototype.
  - `state_graph.py`: Cyclic state graph workflow engine.
  - `concurrency_gate.py`: Concurrent task slot gate prototype.
  - `browser_cdp.py`: Chrome DevTools Protocol engine prototype.

---

## [0.1.0] - 2026-10-14
- Initial experimental prototype release.
