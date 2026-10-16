"""WS-05 follow-up (P13): the settings surface is grouped by what a plugin *is*, and named by who owns it.

The complaint this answers, from the v0.2.1 review's product half: "settings are bare bone". It was true in a
specific, mechanical way — `with_ui_defaults` dumped every property that did not name its own `x-ui.group` into
one catch-all tab (`Plugins & Integrations`), which held 22 of 44 settings, and the client then rendered them as a
flat list of anonymous toggles. Nothing about the *owner* of a setting reached the page, so 39 loaded plugins
looked like one plugin.

The rule now, in both directions:

* a plugin's properties land on the tab for its **capability family** (hooks on `Hooks`, bridges on
  `Bridges & Integrations`, providers on the existing `Models & Providers`) — an explicit `x-ui.group` still wins,
  because a plugin is allowed to say where it belongs;
* each property carries **who owns it** (`x-ui.owner`, `owner_title`, `owner_summary`), which is what the client
  renders as a section heading — so a tab groups like behaviour, and a heading names the integration.

The tab names are derived, never listed: the client must not learn the word `Hooks`, and a family with no
properties gets no tab (that invariant is `test_ui_contract.py`'s).
"""

from __future__ import annotations

import inspect
import json
import re
from pathlib import Path

import pytest

from harness.kernel.bootstrap import bootstrap_kernel
from harness.kernel.settings_schema import (
    FAMILY_TABS,
    PLUGIN_TAB_DEFAULT,
    settings_owner_index,
    tab_for,
    with_ui_defaults,
)

REPO = Path(__file__).resolve().parents[2]
SETTINGS_JS = REPO / "harness" / "frontends" / "web" / "static" / "settings.js"


@pytest.fixture
async def kernel(tmp_path: Path):
    rt = await bootstrap_kernel(workspace=tmp_path / "ws", config_dir=tmp_path / "conf")
    try:
        yield rt
    finally:
        await rt.registry.unload_all()


# ---------------------------------------------------------------- the derivation


def test_a_plugin_chooses_its_tab_by_family():
    """Family → tab, with the catch-all only for a plugin whose family nobody knows."""
    assert tab_for("hook-read-before-edit", "hooks") == "Hooks"
    assert tab_for("hook-tool-result-spill", "hooks") == "Hooks"
    assert tab_for("bridge-mcp-client", "bridges") == "Bridges & Integrations"
    assert tab_for("policy-tirith", "policy") == "Policy & Safety"
    assert tab_for("context-repo-map", "context") == "Context"
    assert tab_for("store-jsonl-transcript", "stores") == "Storage & Records"
    # Providers and strategies belong to the tabs the kernel already had: a plugin does not invent a near-duplicate.
    assert tab_for("provider-anthropic", "providers") == "Models & Providers"
    assert tab_for("strategy-single-loop", "strategies") == "Execution & Strategies"
    assert tab_for("executor-worktree", "executors") == "Execution & Strategies"
    # Unknown family (a workspace plugin, a new directory) is tolerated, not dropped.
    assert tab_for("mystery-thing", None) == PLUGIN_TAB_DEFAULT
    assert tab_for("mystery-thing", "exotic") == PLUGIN_TAB_DEFAULT


def test_an_explicit_group_beats_the_family_tab():
    """`x-ui.group` is a plugin's own answer and stays authoritative (WS-05a's rule, unchanged)."""
    prop = {"type": "string", "x-ui": {"group": "Obsidian"}}
    assert with_ui_defaults(prop, "hook-vault-sync", family="hooks")["x-ui"]["group"] == "Obsidian"
    assert with_ui_defaults({"type": "boolean"}, "hook-vault-sync", family="hooks")["x-ui"]["group"] == "Hooks"


def test_owner_metadata_names_the_plugin_human_readably():
    from harness.kernel.settings_schema import plugin_display_name

    assert plugin_display_name("hook-read-before-edit", "hooks") == "Read Before Edit"
    assert plugin_display_name("bridge-mcp-client", "bridges") == "MCP Client"
    assert plugin_display_name("policy-tirith", "policy") == "Tirith"
    assert plugin_display_name("context-repo-map", "context") == "Repo Map"
    # No id-shaped leftovers: a heading is not a package name.
    for name in ("hook-read-before-edit", "fs-tools", "local-executor"):
        heading = plugin_display_name(name, None)
        assert "-" not in heading and heading == heading.strip() and heading, name


async def test_the_aggregated_schema_groups_by_family_and_carries_owners(kernel):
    schema = await kernel.commands.settings_schema()
    props = schema["properties"]
    groups = set(schema["groups"])

    # Fail-first: before this change `Hooks` did not exist as a tab and the hooks' 17 properties sat in the
    # catch-all with nothing saying who they belonged to.
    assert "Hooks" in groups, f"hook settings have no tab of their own; groups={sorted(groups)}"
    hook_props = {k: v for k, v in props.items() if v["x-ui"].get("owner", "").startswith("hook-")}
    assert hook_props, "the hook plugins' settings vanished from the surface"
    assert {v["x-ui"]["group"] for v in hook_props.values()} == {"Hooks"}

    # The catch-all must stay the *exception*: at most one property from a family we know about.
    dumped = {k for k, v in props.items() if v["x-ui"].get("group") == PLUGIN_TAB_DEFAULT}
    owners_dumped = {props[k]["x-ui"].get("owner") for k in dumped} - {None}
    assert len(owners_dumped) <= 1, f"still a dumping ground: {sorted(owners_dumped)}"

    # Every plugin-owned property says who owns it; kernel properties do not pretend to.
    for key, prop in props.items():
        ui = prop["x-ui"]
        if key.startswith("kernel.") or ui.get("group") == "Secrets":
            assert "owner" not in ui, f"{key}: a kernel setting claims a plugin owner"
            continue
        assert ui.get("owner"), f"{key} has no owner"
        assert ui["owner"] in kernel.registry.discovered, f"{key}: owner {ui['owner']!r} is not a loaded plugin"
        title = ui.get("owner_title")
        assert title and "-" not in title, f"{key}: owner_title={title!r}"


async def test_no_tab_is_invented_for_a_plugin_with_nothing_to_configure(kernel):
    """39 plugins load; only the ones with settings may create a tab."""
    schema = await kernel.commands.settings_schema()
    declared = {p["x-ui"].get("group") for p in schema["properties"].values()} - {None, ""}
    assert set(schema["groups"]) <= declared | {"Secrets"}, "a tab exists with no control in it"
    assert "Tools" not in schema["groups"], "no tool plugin declares a setting, so a `Tools` tab would be empty"


# ---------------------------------------------------------------- the client half


def test_an_installed_plugin_reports_no_capability_family(tmp_path):
    """A plugin *installed* into an install root has no family, so it is grouped as "Other".

    The family is read from the directory a plugin was found under, which is a capability name in the built-in tree
    (`harness/plugins/hooks/<name>`) and a *location* for an installed one (`<config>/plugins/<name>`,
    `<workspace>/.harness/plugins/<name>` — what `harness plugin` scaffolds). Reporting `plugins` there would make
    the Plugins pane invent a family heading called "plugins" and imply a capability nobody chose, so the parent is
    recognised as an install root and no family is reported. `tab_for` answers `None` with the default tab, so the
    plugin's settings are still exactly where a user expects an integration to be.
    """
    from types import SimpleNamespace

    def found(directory):
        # `plugin_dir()` reads the manifest's own location, which is where the family comes from.
        manifest = SimpleNamespace(summary="a summary", dir_path=str(directory))
        return SimpleNamespace(manifest=manifest)

    index = settings_owner_index(
        SimpleNamespace(discovered={
            "probe-vault": found(tmp_path / "config" / "plugins" / "probe-vault"),
            "audit_log": found(tmp_path / "harness" / "plugins" / "hooks" / "audit_log"),
        })
    )

    assert index["plugins.probe-vault"]["family"] is None
    assert tab_for("plugins.probe-vault", None) == PLUGIN_TAB_DEFAULT
    # a built-in's family is untouched by the install-root rule
    assert index["plugins.audit_log"]["family"] == "hooks"
    assert tab_for("plugins.audit_log", "hooks") == FAMILY_TABS["hooks"]


def test_settings_js_renders_the_owner_heading_and_hardcodes_no_tab_names():
    """The panel groups by `x-ui.owner`; it does not know what a hook is.

    Two directions here, both cheap to lose and expensive to discover in a browser: the client ignoring the owner
    fields (back to a flat wall), and the client learning tab names by heart (a plugin's tab then needs a client
    edit — the dependency WS-05 removed).
    """
    js = SETTINGS_JS.read_text(encoding="utf-8")
    assert "owner_title" in js, "the section heading has to come from the schema's owner, not from the key"
    assert "owner_summary" in js, "a heading without a sentence saying what the plugin does is decoration"
    # Comment lines may *explain* a tab name; code may not *choose* one. Same discipline as
    # `harness/kernel/catalog.py`'s `_code_only`: prose that reads like wiring gets miscounted as wiring, and a
    # test that grades prose ends up being argued with instead of obeyed.
    code = "\n".join(
        line for line in js.splitlines() if not line.strip().startswith(("//", "*", "/*", "*/"))
    )
    for hardcoded in ("Plugins & Integrations", "Models & Providers", "Hooks", "Secrets", "Context", "Tools"):
        assert hardcoded not in code, f"settings.js names a tab in its logic ({hardcoded!r})"
    # The precise form of the bug, found while writing this: the constructor used to *seed* the open tab with
    # `'Models & Providers'`, so a daemon with no provider plugin opened the modal on an empty pane.
    assert not re.search(r"activeGroup\s*=\s*['\"]", code), "the open tab must come from `groups`, never a literal"
    assert "this.schema.groups.includes(this.activeGroup)" in code, "a remembered group has to be validated"


def test_settings_doc_has_a_section_for_every_tab():
    """A generated document that filters through its own list of tab names drops what it has not heard of.

    The R47 bug class, this time in the catalogue: when hooks and bridges moved onto family tabs, their rows
    vanished from `docs/catalog/SETTINGS.md` because the renderer walked a literal tuple of group names. The
    freshness gate could not see it — it compares the file against a fresh build of the *same* renderer — so the
    rule has to be stated directly: every tab the client is offered and every UI-advertised row must appear.

    This reads the committed pair (`catalog.json` + `SETTINGS.md`) rather than booting a kernel, because
    `test_catalog.py::test_committed_documents_match_a_fresh_boot` is what guarantees they came from one build.
    """
    cat = json.loads((REPO / "docs/catalog/catalog.json").read_text(encoding="utf-8"))
    text = (REPO / "docs/catalog/SETTINGS.md").read_text(encoding="utf-8")
    sections = set(re.findall(r"^## (.+)$", text, re.M))
    groups = set((cat.get("ui") or {}).get("settings_groups") or [])
    rows_by_group = {
        row["group"]
        for row in cat["settings"]
        if row["in_ui"] and not row["alias"] and row["group"]
    }
    assert "Hooks" in rows_by_group, "the hooks' settings are not on a `Hooks` tab — this test lost its subject"
    assert rows_by_group <= sections, f"advertised rows with no section: {sorted(rows_by_group - sections)}"
    assert groups - {"Secrets"} <= sections, f"tabs the client sees but the document hides: {sorted(groups - sections)}"
    for row in cat["settings"]:
        if row["in_ui"] and not row["alias"]:
            assert f"`{row['key']}`" in text, f"{row['key']} is in the schema but absent from SETTINGS.md"


def test_with_ui_defaults_keeps_its_signature_compatible():
    """The catalogue calls this with (prop, namespace) only; the new argument must stay optional."""
    assert "family" in inspect.signature(with_ui_defaults).parameters
    assert with_ui_defaults({"type": "boolean"}, "kernel")["x-ui"]["group"] == "Execution & Strategies"
    assert "owner" not in with_ui_defaults({"type": "boolean"}, "kernel")["x-ui"], "the kernel owns no plugin"
    # Plugin schemas are registered under a `plugins.<name>` namespace; the owner is the bare id.
    prop = with_ui_defaults({"type": "boolean"}, "plugins.hook-x", family="hooks")
    assert prop["x-ui"]["group"] == "Hooks"
    assert prop["x-ui"]["owner"] == "hook-x"
    assert with_ui_defaults({"type": "boolean"}, "hook-x")["x-ui"]["group"] == PLUGIN_TAB_DEFAULT
