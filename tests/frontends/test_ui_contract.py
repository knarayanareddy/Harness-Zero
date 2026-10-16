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

#: Durable record kinds the client has no renderer for, and the reason each one needs none. Add a kind to the
#: log without painting it and this test fails — a record nobody renders is invisible after a reload, which is
#: the bug class WS-04c closed. Keep an excuse that is no longer needed and it fails the other way, so the
#: list cannot quietly become a hiding place.
UNPAINTED_KINDS = {
    "turn.completed": "the turn marker `appendTurnBlock`/`finalizeStreamingBlock` already place it; a reloaded "
    "page shows the assistant record itself, so painting the marker too would duplicate the turn",
}

_PLACEHOLDER_RE = re.compile(r"data-notice|noop\(|coming soon|\bTODO\b", re.I)
_TAB_RE = re.compile("""data-tab=["']([^"'`]+)["']""")
_DATA_COMMAND_RE = re.compile("""data-command=["']([^"'`]+)["']""")
_API_COMMAND_RE = re.compile("""command\\(\\s*["']([a-zA-Z_][\\w.]*)["']""")
_RENDER_CASE_RE = re.compile("""case ["']([a-z_][a-z0-9_.]*)["']:""")
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
        painted |= {p.stem for p in renderer_dir.glob("*.js")}
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


async def test_panels_are_declared_backed_and_mounted(kernel):
    contributions = await kernel.commands.ui_contributions()
    declared = {panel["id"] for panel in contributions["panels"]}
    in_markup = set(_literal_values(_TAB_RE.findall(_markup_text())))
    assert not contributions["unbacked_panels"], (
        "a panel whose command is not dispatchable is a tab that shows an error: "
        + ", ".join(f"{p['id']} → {p['command']}" for p in contributions["unbacked_panels"])
    )
    assert not in_markup - declared, (
        f"`data-tab` with no `[ui]` declaration: {sorted(in_markup - declared)} — delete the pane or declare "
        "it in `harness/frontends/web/ui.toml` (or the plugin's manifest)"
    )
    assert not declared - in_markup, f"declared panels the client never renders: {sorted(declared - in_markup)}"


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
    painted = _painted_kinds()
    missing = sorted(k for k in _durable_kinds() if k not in painted)
    assert missing == sorted(UNPAINTED_KINDS), (
        f"durable kinds with no renderer: {missing}, excused: {sorted(UNPAINTED_KINDS)} — a new kind needs a "
        "case in the record switch, a module under static/renderers/, or an entry in UNPAINTED_KINDS with a "
        "reason that says why the timeline does not show it"
    )
    for kind, reason in UNPAINTED_KINDS.items():
        assert len(reason.split()) >= 5, f"{kind}: an excuse has to be a sentence, not a shrug"


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
        assert set(panel) == {"id", "title", "command", "renderer", "plugin"}
        assert panel["renderer"] in {"table", "tree", "markdown", "list"}
    assert ui["record_kinds"], "a client that is told nothing cannot refuse an unknown kind"
    rows = await kernel.commands.commands_list()
    assert {"name", "args", "alias_of", "exposed"} == set(rows[0])
    aliases = {row["name"]: row["alias_of"] for row in rows if row["alias_of"]}
    assert aliases == {"form_submit": "form.submit"}, "an underscore alias must be reported as one"
    assert all(not row["exposed"] for row in rows if row["alias_of"]), "an alias is not something to put on a button"
