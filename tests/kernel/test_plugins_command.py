"""P13b — the row `plugins.list` answers, which three frontends read and the Plugins pane paints.

The pane is only as honest as this command: a card that says "4 settings on Hooks" is a claim about the settings
surface, and a Disable button that looks live is a lie. So each assertion is an agreement between two paths that
could drift — the row against the aggregated `settings.schema` (not against a helper), `disabled` against the one
setting the toggles write, and `panels`/`client_module` against the manifest the loader parsed.

`plugins.list` had no test at all before this file, which is how a command can carry ten frontends and still
change shape without anyone noticing.
"""
from __future__ import annotations

from pathlib import Path
from typing import Any

import pytest

from harness.kernel.bootstrap import bootstrap_kernel
from harness.kernel.registry import DiscoveredPlugin, TrustTier
from harness.sdk.manifest import PluginManifest

ROW_KEYS = {
    "name",
    "version",
    "summary",
    "provides",
    "capabilities",
    "tier",
    "loaded",
    "disabled",
    "family",
    "has_settings",
    "settings_count",
    "settings_group",
    "panels",
    "record_renderers",
    "client_module",
}


@pytest.fixture
async def kernel(tmp_path: Path):
    rt = await bootstrap_kernel(workspace=tmp_path / "ws", config_dir=tmp_path / "conf")
    try:
        yield rt
    finally:
        await rt.registry.unload_all()


# ---------------------------------------------------------------- the row itself


async def test_rows_carry_everything_the_pane_reads_and_the_cli_keeps(kernel):
    """Exact key set: `harness plugin list` and the TUI read `provides`/`capabilities`; the pane reads the rest."""
    rows = await kernel.commands.plugins_list()
    assert rows, "the built-in tree must discover plugins"
    for row in rows:
        assert set(row) == ROW_KEYS, f"{row['name']}: {set(row) ^ ROW_KEYS}"
        assert isinstance(row["loaded"], bool) and isinstance(row["disabled"], bool)
        assert isinstance(row["panels"], list) and isinstance(row["record_renderers"], list)


async def test_a_plugin_with_no_ui_declaration_says_so(kernel):
    """`panels: []` and `client_module: None` are the common case, and the pane must not invent a badge."""
    rows = {row["name"]: row for row in await kernel.commands.plugins_list()}
    plain = next(row for row in rows.values() if not row["settings_count"])
    assert plain["panels"] == [] and plain["record_renderers"] == []
    assert plain["has_settings"] is False and plain["settings_group"] is None


async def test_the_ui_a_manifest_declares_shows_up_in_its_row(kernel):
    """What `[ui]` promises is what `plugins.list` reports — the pane does not re-read any manifest."""
    registry = kernel.registry
    name = "demo-feature"
    manifest = PluginManifest.from_dict(
        {
            "name": name,
            "version": "0.1.0",
            "api_version": "1",
            "provides": ["tool"],
            "ui": {
                "panels": [{"id": "vault", "title": "Vault", "command": "obsidian.search", "renderer": "table"}],
                "record_renderers": ["ui.card"],
                "client_module": "static/plugins/obsidian.js",
            },
        }
    )
    # `registry.discovered` hands out a defensive copy, so a fixture writes the store it copies from.
    registry._discovered[name] = DiscoveredPlugin(manifest=manifest, tier=TrustTier.LOCAL, origin="test")
    try:
        row = {r["name"]: r for r in await kernel.commands.plugins_list()}[name]
    finally:
        registry._discovered.pop(name, None)
    assert row["panels"] == ["vault"]
    assert row["record_renderers"] == ["ui.card"]
    assert row["client_module"] == "static/plugins/obsidian.js"


# ---------------------------------------------------------------- agreement with the settings surface


async def test_the_pane_and_the_modal_agree_about_a_plugins_settings(kernel):
    """`settings_count` / `settings_group` are re-derived from `settings.schema`, the response the modal renders.

    If these two paths drift, "Configure" opens a tab that does not hold that plugin's controls — the kind of
    bug no unit test of either path alone can see.
    """
    schema: dict[str, Any] = await kernel.commands.settings_schema()
    props = schema["properties"]
    for row in await kernel.commands.plugins_list():
        prefix = f"plugins.{row['name']}."
        owned = {key: prop for key, prop in props.items() if key.startswith(prefix)}
        assert row["settings_count"] == len(owned), row["name"]
        assert row["has_settings"] is bool(owned), row["name"]
        if not owned:
            assert row["settings_group"] is None, row["name"]
            continue
        groups = {prop.get("x-ui", {}).get("group") for prop in owned.values()}
        assert row["settings_group"] in groups, f"{row['name']}: {row['settings_group']!r} not in {groups}"
        assert row["settings_group"] in schema["groups"], (
            f"{row['name']} points at a tab the client would never show"
        )


async def test_every_settings_bearing_plugin_is_reachable_from_the_pane(kernel):
    """The pane is the way into a plugin's controls, so no plugin may own settings with no jump target."""
    rows = await kernel.commands.plugins_list()
    stranded = [row["name"] for row in rows if row["settings_count"] and not row["settings_group"]]
    assert not stranded, f"plugins with settings but no tab to open: {stranded}"
    assert any(row["settings_count"] >= 4 for row in rows), (
        "the aggregate of plugin schemas must keep a real settings surface — an empty pane is a stub"
    )


async def test_the_family_reported_is_the_tree_the_plugin_was_found_under(kernel):
    """`family` and the settings tab come from one index, so the pane's grouping matches the modal's default."""
    from harness.kernel.settings_schema import settings_owner_index

    owners = settings_owner_index(kernel.registry)
    for row in await kernel.commands.plugins_list():
        assert row["family"] == owners.get(f"plugins.{row['name']}", {}).get("family"), row["name"]


# ---------------------------------------------------------------- enable / disable are next-start facts


async def test_toggling_writes_the_disabled_list_without_unloading(kernel, tmp_path):
    """The honest half of the pane: `disabled` moves, `loaded` does not, because nothing hot-unloads a plugin."""
    rows = {row["name"]: row for row in await kernel.commands.plugins_list()}
    name = next(row["name"] for row in rows.values() if row["loaded"])

    assert await kernel.commands.plugins_disable(name) is True
    after = {row["name"]: row for row in await kernel.commands.plugins_list()}
    assert after[name]["disabled"] is True, "the user's intent must be visible in the same response"
    assert after[name]["loaded"] is True, "the pane may not claim a plugin stopped mid-session"
    assert kernel.settings.get("kernel.plugins.disabled") == [name]

    assert await kernel.commands.plugins_disable(name) is False, "disabling twice is not a change"
    assert await kernel.commands.plugins_enable(name) is True
    restored = {row["name"]: row for row in await kernel.commands.plugins_list()}
    assert restored[name]["disabled"] is False
    assert await kernel.commands.plugins_enable(name) is False, "enabling twice is not a change"


async def test_every_discovered_plugin_gets_a_row(kernel):
    """The pane must not hide a plugin: one row per entry in `registry.discovered`, no more and no less."""
    rows = await kernel.commands.plugins_list()
    assert {row["name"] for row in rows} == set(kernel.registry.discovered)
    assert len(rows) == len(kernel.registry.discovered), "rows are keyed by name, so duplicates would vanish"
