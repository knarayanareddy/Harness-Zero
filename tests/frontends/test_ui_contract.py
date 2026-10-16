"""WS-05 — the shipped client owns no UI that a feature did not declare.

Four rules, checked statically (no browser) against ``harness/frontends/web/static/`` and a booted kernel:

* a **panel** exists because it is declared in a manifest ``[ui]`` block *and* backed by a dispatchable command;
* a **record kind** the session log keeps is either painted by a renderer or explicitly excused;
* a **command** the client binds is one the kernel dispatches;
* the **settings surface** comes from ``settings.schema`` alone, so a plugin can add and order a tab without a
  kernel edit, and the client holds no second copy of it.

The point is not file layout — it is that the client cannot claim more than the kernel offers, and a feature
cannot add a tab the kernel does not know the owner of. ``test_static_contract.py`` keeps its own invariants
(ids, CSP, no ``alert``); this is the same style of check pointed at ownership.
"""
from __future__ import annotations

import re
from pathlib import Path

import pytest

from harness.kernel.bootstrap import bootstrap_kernel

REPO = Path(__file__).resolve().parents[2]
STATIC = REPO / "harness" / "frontends" / "web" / "static"

# The list of kinds the timeline deliberately does not paint lives in the client, so this file reads it rather
# than keeping a second copy — a list kept twice is a list that lies. `NO_RENDERER` in `static/registry.js` maps
# each such kind to the reason it needs no painter; a kind that is neither painted nor excused is a coverage
# hole, and `RendererRegistry.render` makes it visible in the page instead of silently dropping the record.
_EXCUSE_RE = re.compile(r"^  '([a-z_][a-z0-9_.]*)':\s*\n?\s*'([^']+)'", re.M)

_PLACEHOLDER_RE = re.compile(r"data-notice|noop\(|coming soon|\bTODO\b", re.I)
_TAB_RE = re.compile("""data-tab=["']([^"'`]+)["']""")
_DATA_COMMAND_RE = re.compile("""data-command=["']([^"'`]+)["']""")
_API_COMMAND_RE = re.compile("""command\\(\\s*["']([a-zA-Z_][\\w.]*)["']""")
_RENDER_CASE_RE = re.compile("""case ["']([a-z_][a-z0-9_.]*)["']:""")
_PANEL_REGISTER_RE = re.compile(r'''this\.panels\.register\(\s*['"]([\w-]+)['"]''')
_GENERIC_RENDERER_RE = re.compile(r"^  (\w+): \(", re.M)
_STUDIO_PANE_RE = re.compile(r'<section class="studio-pane">.*?</section>', re.S)


def _sources() -> dict[str, str]:
    return {
        str(p.relative_to(STATIC)): p.read_text(encoding="utf-8")
        for p in sorted(STATIC.rglob("*"))
        if p.is_file() and p.suffix in {".js", ".html"} and "vendor" not in p.parts
    }


def _markup_text() -> str:
    return "\n".join(_sources().values())


def _literal_values(matches) -> list[str]:
    """Literal attribute values only: `data-tab="${tabName}"` inside a JS selector is not a declared tab."""
    return sorted({m for m in matches if m and not any(c in m for c in "${} \t")})


def _excuses() -> dict[str, str]:
    """`NO_RENDERER` from ``static/registry.js``: kind -> the reason it has no painter."""
    src = _sources()["registry.js"]
    start = src.index("export const NO_RENDERER = {")
    body = src[start : src.index("\n};", start)]
    return {kind: " ".join(reason.split()) for kind, reason in _EXCUSE_RE.findall(body)}


def _durable_kinds() -> list[str]:
    """Record kinds a session log keeps *and* a reloaded page must show — derived, never hand-listed."""
    from harness.sdk.events import EVENT_PAYLOADS
    from harness.sdk.types import RECORD_KINDS

    carried = {pl.record_kind for pl in EVENT_PAYLOADS.values() if pl.record_kind}
    return [k for k in RECORD_KINDS if k in carried]


def _painted_kinds() -> set[str]:
    """Kinds the client has a renderer for: a `case` in the record switch, or a module under `renderers/`.

    Both shapes count, so the set does not move when WS-05b lifts the switch out of `app.js` into
    `registry.js` + one module per kind — which is the refactor this suite is meant to make safe.
    """
    painted = set(_RENDER_CASE_RE.findall(_markup_text()))
    renderer_dir = STATIC / "renderers"
    if renderer_dir.is_dir():
        painted |= {p.stem for p in renderer_dir.glob("*.js") if not p.stem.startswith("_")}
    return painted


@pytest.fixture
async def kernel(tmp_path: Path):
    rt = await bootstrap_kernel(workspace=tmp_path / "ws", config_dir=tmp_path / "conf")
    try:
        yield rt
    finally:
        await rt.registry.unload_all()


# ---------------------------------------------------------------- no placeholders, no invented bindings


def test_no_placeholder_bindings_in_the_client():
    """A control that does nothing is a defect, not a roadmap item."""
    offenders = {name: _PLACEHOLDER_RE.findall(text) for name, text in _sources().items()}
    offenders = {k: v for k, v in offenders.items() if v}
    assert not offenders, f"placeholder UI present: {offenders}"


async def test_every_command_the_client_binds_is_dispatchable(kernel):
    rows = await kernel.commands.commands_list()
    dispatchable = {row["name"] for row in rows if row["exposed"]}
    called = set(_API_COMMAND_RE.findall(_markup_text()))
    bound = set(_DATA_COMMAND_RE.findall(_markup_text()))
    unknown = sorted((called | bound) - dispatchable)
    assert not unknown, f"the client binds commands the kernel does not expose: {unknown}"


# ---------------------------------------------------------------- panels: declared, backed, present


async def test_panels_are_declared_backed_and_reachable(kernel):
    """A declared panel must be paintable by the client, and the client may not paint an undeclared one.

    This rule used to require a `data-tab` for every declared panel in the shell markup, which contradicted the
    feature it governed: `PanelRegistry.mount()` creates the tab and the host for anything the kernel declares,
    and that is how a plugin — and, since P13b, the Plugins pane itself — gets a real pane without a pull
    request against `index.html`. Reachability is the fact worth pinning, and it has two honest arms: either the
    client registers a module for the panel id, or the panel names a generic renderer that exists.
    """
    contributions = await kernel.commands.ui_contributions()
    panels = {panel["id"]: panel for panel in contributions["panels"]}
    in_markup = set(_literal_values(_TAB_RE.findall(_markup_text())))
    assert not contributions["unbacked_panels"], (
        "a panel whose command is not dispatchable is a tab that shows an error: "
        + ", ".join(f"{p['id']} → {p['command']}" for p in contributions["unbacked_panels"])
    )
    assert not in_markup - set(panels), (
        f"`data-tab` with no `[ui]` declaration: {sorted(in_markup - set(panels))} — delete the pane or declare "
        "it in `harness/frontends/web/ui.toml` (or the plugin's manifest)"
    )
    unreachable = sorted(
        pid
        for pid, panel in panels.items()
        if pid not in _registered_panel_modules() and panel["renderer"] not in _generic_renderers()
    )
    assert not unreachable, (
        f"declared panels nothing can paint: {unreachable} — needs a module under `static/panels/` registered "
        "in `app.js`, or one of the generic renderers"
    )
    orphan = sorted(_registered_panel_modules() - set(panels))
    assert not orphan, f"panel modules with no `[ui]` declaration: {orphan} — the shell may not claim UI no feature owns"


def _registered_panel_modules() -> set[str]:
    """The panel ids `app.js` hands a module for (`this.panels.register('<id>', …)`)."""
    return set(_PANEL_REGISTER_RE.findall(_sources()["app.js"]))


def _generic_renderers() -> set[str]:
    """The renderer names `static/registry.js` implements for a panel that ships no module."""
    src = _sources()["registry.js"]
    start = src.index("export const GENERIC_RENDERERS = {")
    body = src[start : src.index("\n};", start)]
    return set(_GENERIC_RENDERER_RE.findall(body))


def test_the_client_implements_exactly_the_renderers_a_manifest_may_name():
    """`VALID_UI_RENDERERS` is the load-time allow-list; `GENERIC_RENDERERS` is the runtime table.

    A plugin panel that passes validation and then falls through to `GENERIC_RENDERERS.list` would render a
    list where its author declared a table — silently, on the user's screen. Checking the two sets against
    each other means the allow-list can only grow by writing the renderer, never by editing one list.
    """
    from harness.sdk.manifest import VALID_UI_RENDERERS

    assert _generic_renderers() == set(VALID_UI_RENDERERS), (
        f"client renderers {sorted(_generic_renderers())} != manifest-allowed {sorted(VALID_UI_RENDERERS)}"
    )


def test_the_studio_pane_holds_hosts_not_feature_markup():
    """`index.html` supplies the tab strip and empty view hosts; every row and card is rendered by JS.

    That is what lets a panel be *added* by a declaration: if the shell carried an `economics-card` here, a
    new feature could not get one without editing the shell, and the declaration would be decoration.
    """
    html = (STATIC / "index.html").read_text(encoding="utf-8")
    pane = _STUDIO_PANE_RE.search(html)
    assert pane, "the studio pane host is missing from index.html"
    rendered = re.findall(r'class="[^"]*(?:-card|-row|-item)\b', pane.group(0))
    assert not rendered, f"feature markup in the shell: {rendered}"


# ---------------------------------------------------------------- the record vocabulary is covered


def test_every_durable_record_kind_is_painted_or_excused():
    """The kind set is closed by the client's own two lists, checked against the kernel's contract."""
    painted = _painted_kinds()
    excuses = _excuses()
    assert excuses, "NO_RENDERER could not be parsed out of static/registry.js — the test lost its subject"
    missing = sorted(k for k in set(_durable_kinds()) - painted if k not in excuses)
    assert not missing, (
        f"durable kinds with no renderer and no excuse: {missing} — a new kind needs a module under "
        "static/renderers/ plus a line in `RECORD_RENDERERS`, or an entry in `NO_RENDERER` saying why the "
        "timeline does not show it"
    )
    for kind, reason in excuses.items():
        assert len(reason.split()) >= 5, f"{kind}: an excuse has to be a sentence, not a shrug ({reason!r})"
    # An excuse is not a hiding place: it must name a kind the kernel writes, and it must be needed.
    assert not set(excuses) & painted, f"both painted and excused: {sorted(set(excuses) & painted)}"
    from harness.sdk.types import RECORD_KINDS

    stale = sorted(k for k in excuses if k not in RECORD_KINDS)
    assert not stale, f"excuses for kinds the kernel does not write: {stale}"


def test_the_renderer_map_is_the_modules_on_disk():
    """`RECORD_RENDERERS` in app.js, its imports, and `static/renderers/*.js` must agree.

    A renderer module that nothing imports, or an import with no file behind it, is the same defect seen from
    two ends: the file and the wiring disagree about what the client can paint.
    """
    app = _sources()["app.js"]
    start = app.index("const RECORD_RENDERERS = {")
    block = app[start : app.index("\n};", start)]
    wired = set(re.findall(r"'([a-z_][a-z0-9_.]*)':\s*render", block))
    imports = set(re.findall(r"from '\./renderers/([a-z_][a-z0-9_.]*)\.js'", app))
    on_disk = {p.stem for p in (STATIC / "renderers").glob("*.js") if not p.stem.startswith("_")}
    assert wired == on_disk, f"map {sorted(wired)} != modules {sorted(on_disk)}"
    assert imports == on_disk, f"imports {sorted(imports)} != modules {sorted(on_disk)}"


def test_the_panel_modules_are_the_declared_panels():
    """One name, three places: the declared panel id, `static/panels/<id>.js`, the registration in app.js.

    A panel gets its tab because a manifest declared it (WS-05); the client module is wired by id. So a file
    named for a panel nobody declared is dead code that *looks* like a feature, and a declaration with no module
    is fine only because the generic renderer covers it — which is what `panels[].renderer` records.
    """
    app = _sources()["app.js"]
    registered = set(re.findall(r"this\.panels\.register\('([a-z][a-z0-9_-]+)'", app))
    modules = {p.stem for p in (STATIC / "panels").glob("*.js") if not p.stem.startswith("_")}
    import tomllib

    with (REPO / "harness" / "frontends" / "web" / "ui.toml").open("rb") as fh:
        declared = {panel["id"] for panel in tomllib.load(fh)["ui"]["panels"]}
    assert declared, "harness/frontends/web/ui.toml declares no panels — the built-in client has no tab to show"
    assert modules == registered == declared, (
        f"modules {sorted(modules)} != registered {sorted(registered)} != declared {sorted(declared)}"
    )
    # and each registered id must be imported from the file that bears its name
    assert all(f"'./panels/{pid}.js'" in app for pid in registered), "a registered panel has no module import"




async def test_the_generated_contract_agrees_with_this_test():
    """A renderer list kept twice is a list that lies, so the catalogue and this file parse the same source.

    `docs/catalog/UI_CONTRACT.md` is what a reviewer reads; if it and the test disagreed, the doc would be
    decoration. This asserts the generated fact against the same set the test enforces.
    """
    from harness.kernel.catalog import build_catalog

    cat = await build_catalog(repo_root=REPO)
    assert set(cat["ui"]["kinds_without_renderer"]) == set(_durable_kinds()) - _painted_kinds()
    assert cat["ui"]["undocumented_tabs"] == []
    assert [panel["id"] for panel in cat["ui"]["panels"]] == sorted(
        panel["id"] for panel in cat["ui"]["panels"]
    ), "the generated table is stable regardless of registration order"


# ---------------------------------------------------------------- settings: one source, schema-ordered


def test_settings_js_holds_no_schema_of_its_own():
    """The client interprets the schema; it must not *contain* one.

    Reading `x-ui.group`/`x-ui.widget` off the response is the design (WS-02), so a ban on the string `x-ui`
    would only punish documentation. What is banned is schema *data*: a group list or a `properties` object
    with anything in it. The one literal allowed is the empty pair the panel falls back to when the daemon
    does not answer — an empty form plus an error message beats a stale form, which is what the v0.2.1
    fallback schema used to be.
    """
    text = (STATIC / "settings.js").read_text(encoding="utf-8")
    assert "settings.schema" in text, "the panel must read the aggregated schema"
    for literal in re.findall(r"properties\s*:\s*\{([^{}]*)\}", text):
        assert not literal.strip(), f"a hand-written `properties` block is a second source: {literal[:60]!r}"
    for literal in re.findall(r"groups\s*:\s*\[([^\]]*)\]", text):
        assert not literal.strip(), f"a hand-written group list is a second source: {literal[:60]!r}"


def test_a_plugin_settings_group_is_a_tab_the_kernel_never_mentioned():
    """Ordering and membership come from the schemas, so a feature can add a tab without a kernel edit (R47)."""
    from harness.kernel.settings_schema import SETTINGS_GROUPS, order_groups

    props = {
        "kernel.provider": {"x-ui": {"group": "Models & Providers"}},
        "plugins.obsidian.vault": {"x-ui": {"group": "Obsidian"}},
        "plugins.early.enabled": {"x-ui": {"group": "First", "order": -1}},
    }
    assert "Obsidian" not in SETTINGS_GROUPS, "the point: the group is one the kernel has never heard of"
    assert order_groups({"Models & Providers", "Obsidian", "First"}, props) == [
        "First",
        "Models & Providers",
        "Obsidian",
    ]
    # `order_groups` orders; it is the caller that decides membership from the properties it aggregated
    # (asserted against a live schema below). A group handed in with no properties survives, because that is
    # how `secrets` arrives — an empty group with no keys is not a bug the orderer should hide.
    assert order_groups({"Secrets"}, {}) == ["Secrets"]
    assert order_groups({"Obsidian", "Models & Providers"}, props)[0] == "Models & Providers", (
        "no `x-ui.order` means the built-in sequence first, then the newcomer"
    )


async def test_settings_schema_groups_and_properties_agree(kernel):
    schema = await kernel.commands.settings_schema()
    declared = {p.get("x-ui", {}).get("group") for p in schema["properties"].values()}
    declared = {g for g in declared if g}
    assert declared <= set(schema["groups"]), f"properties in groups with no tab: {sorted(declared - set(schema['groups']))}"
    for group in schema["groups"]:
        assert group in declared or group == "Secrets", f"tab with no control in it: {group!r}"


# ---------------------------------------------------------------- the boot-time contract's shape


async def test_every_declared_panel_can_be_refreshed_with_the_args_the_client_sends(kernel):
    """For each panel, dispatch its command with exactly what `PanelRegistry` would send.

    Three shapes of failure live here and none of them are visible statically: a panel naming a command that
    does not exist (caught elsewhere), a command that rejects `session_id` (R61 — `dispatch` forwards args as
    keyword arguments, so one extra name is a TypeError and an empty pane), and a command that *requires* an
    argument nobody sends. The client's rule is `(panel.accepts).includes('session_id')`, so this test and that
    line have to agree — which is the point of checking the pair rather than each alone.
    """
    ui = await kernel.commands.ui_contributions()
    for panel in ui["panels"]:
        args = {"session_id": "does-not-exist"} if "session_id" in panel["accepts"] else {}
        try:
            await kernel.commands.dispatch(panel["command"], args)
        except TypeError as err:  # a wrong signature is the bug; a missing session is not
            pytest.fail(f"panel {panel['id']!r} → {panel['command']}({sorted(args)}) is not callable: {err}")
        except Exception:
            pass  # unknown session id, no provider configured, empty log — all fine for a wiring check
    generic = [panel for panel in ui["panels"] if panel["id"] not in _registered_panel_modules()]
    assert all("session_id" in panel["accepts"] or panel["accepts"] == [] for panel in generic), (
        "a module-less panel is refreshed by the generic renderer; its command must take session_id or nothing"
    )


async def test_ui_contributions_response_is_all_the_client_needs_at_boot(kernel):
    """The response's keys are the client's contract, so they are asserted as a set (WS-02's discipline)."""
    ui = await kernel.commands.ui_contributions()
    assert set(ui) == {
        "panels",
        "record_renderers",
        "client_modules",
        "unbacked_panels",
        "settings_groups",
        "record_kinds",
    }
    for panel in ui["panels"]:
        # `accepts` is here because the client must know what the panel's command takes: the generic renderer
        # refreshes a pane with the active session, and `dispatch` forwards args as keyword arguments.
        assert set(panel) == {"id", "title", "command", "renderer", "plugin", "accepts"}
        assert panel["renderer"] in {"table", "tree", "markdown", "list"}
        assert isinstance(panel["accepts"], list) and all(isinstance(a, str) for a in panel["accepts"])
    assert ui["record_kinds"], "a client that is told nothing cannot refuse an unknown kind"
    rows = await kernel.commands.commands_list()
    assert {"name", "args", "alias_of", "exposed"} == set(rows[0])
    aliases = {row["name"]: row["alias_of"] for row in rows if row["alias_of"]}
    assert aliases == {"form_submit": "form.submit"}, "an underscore alias must be reported as one"
    assert all(not row["exposed"] for row in rows if row["alias_of"]), "an alias is not something to put on a button"
