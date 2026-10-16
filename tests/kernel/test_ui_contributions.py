"""The `[ui]` manifest block, the registry that collects it, and the two commands a client boots from (WS-05).

These are the kernel half of "a feature owns its UI": a declaration is parsed and refused or accepted *here*,
so the client never has to guess whether a panel is real, and `commands.list` exists so a button can be checked
against the dispatch table instead of against a list kept in a JS file.
"""
from __future__ import annotations

import tomllib
from pathlib import Path

import pytest

from harness.kernel.bootstrap import bootstrap_kernel
from harness.kernel.bus import EventBus
from harness.kernel.commands import BUILTIN_COMMANDS
from harness.kernel.registry import PluginRegistry
from harness.kernel.secrets import Secrets
from harness.kernel.settings import SettingsStore
from harness.sdk.errors import PluginLoadError
from harness.sdk.manifest import PluginManifest, UiContribution
from harness.sdk.types import RECORD_KINDS

UI_TOML = Path(__file__).resolve().parents[2] / "harness" / "frontends" / "web" / "ui.toml"


def _manifest(**ui) -> dict:
    data = {"name": "demo-feature", "version": "0.1.0", "api_version": "1", "provides": ["tool"]}
    if ui:
        data["ui"] = ui
    return data


# ---------------------------------------------------------------- the declaration is validated


def test_a_ui_block_becomes_a_typed_contribution():
    manifest = PluginManifest.from_dict(
        _manifest(
            panels=[{"id": "vault", "title": "Vault", "command": "obsidian.search", "renderer": "table"}],
            record_renderers=["ui.card"],
            client_module="static/plugins/obsidian.js",
        )
    )
    assert manifest.ui is not None
    assert manifest.ui.panels[0].renderer == "table"
    assert manifest.ui.record_renderers == ["ui.card"]
    assert manifest.to_dict()["ui"]["client_module"] == "static/plugins/obsidian.js"


def test_a_feature_without_ui_needs_no_declaration():
    manifest = PluginManifest.from_dict(_manifest())
    assert manifest.ui is None
    assert manifest.to_dict()["ui"] is None, "the catalogue must be able to tell 'none' from 'empty'"


@pytest.mark.parametrize(
    "ui, message",
    [
        ({"panels": [], "widgits": []}, "unknown keys"),
        ({"panels": [{"id": "vault", "command": "obsidian.search"}]}, "missing title"),
        ({"panels": [{"id": "Vault", "title": "t", "command": "c"}]}, "must match"),
        ({"panels": [{"id": "alpha", "title": "t", "command": "c", "renderer": "canvas"}]}, "canvas"),
        ({"panels": [{"id": "alpha", "title": "t", "command": "   "}]}, "empty `command`"),
        ({"panels": [{"id": "alpha", "title": "t", "command": "c"}, {"id": "alpha", "title": "t", "command": "c"}]}, "twice"),
        ({"record_renderers": "ui.card"}, "must be a list"),
        ({"record_renderers": ["Not A Kind"]}, "invalid record kind"),
        ({"client_module": "/etc/passwd"}, "under static/"),
        ({"client_module": "static/../secret.js"}, "under static/"),
    ],
)
def test_a_declaration_the_client_cannot_honour_is_refused_at_parse(ui, message):
    with pytest.raises(PluginLoadError) as excinfo:
        UiContribution.from_dict(ui, plugin="demo")
    assert message in str(excinfo.value), str(excinfo.value)


def test_a_panel_id_clashes_are_caught_when_merged():
    """Two features claiming one id is one bug the client cannot resolve, so the registry refuses it."""
    registry = PluginRegistry(workspace=Path.cwd(), bus=EventBus(), settings=SettingsStore(Path("/tmp/s.json")), secrets=Secrets(Path("/tmp/x")), data_dir=Path("/tmp"))
    registry.register_ui_contribution("a", UiContribution(panels=[_panel("dup")]))
    registry.register_ui_contribution("b", UiContribution(panels=[_panel("dup")]))
    with pytest.raises(PluginLoadError, match="same panel id"):
        registry.ui_contributions()


def _panel(pid: str):
    from harness.sdk.manifest import UiPanel

    return UiPanel(id=pid, title="T", command="sessions.list")


# ---------------------------------------------------------------- the shipped client declares itself the same way


def test_the_web_frontend_declares_its_panels_in_a_file_not_in_kernel_code():
    """`harness/frontends/web/ui.toml` is read as data, so the kernel neither imports nor hard-codes the shell."""
    assert UI_TOML.is_file(), "the built-in UI declaration is missing"
    ui = UiContribution.from_dict(tomllib.loads(UI_TOML.read_text(encoding="utf-8"))["ui"], plugin="web")
    declared = {panel.id: panel for panel in ui.panels}
    assert set(declared) == {"artifact", "sessions", "economics"}, declared
    for panel in ui.panels:
        # Every panel is backed by a command a frontend can dispatch today: a tab with no command behind it is
        # how `data-notice` placeholders were born.
        assert panel.command in BUILTIN_COMMANDS, f"{panel.id} → {panel.command} is not dispatchable"
        assert panel.title.strip(), f"{panel.id} has no label"
    assert ui.record_renderers == [], "the client's own kinds are the code in renderRecord, not a second list"
    # The name of the game: no Python file in the package knows the panel set. Before this, the tabs were a
    # hard-coded branch in `app.js` and nothing anywhere said which command was supposed to fill them.
    offenders = [
        str(py.relative_to(UI_TOML.parents[3]))
        for py in (UI_TOML.parents[2] / "harness").rglob("*.py")
        if all(f'"{panel.id}"' in py.read_text(encoding="utf-8") for panel in ui.panels)
    ]
    assert not offenders, f"a kernel-side list of the client's panels reappeared: {offenders}"


async def test_unloading_drops_contributions_so_disabling_removes_a_panel(tmp_path: Path):
    rt = await bootstrap_kernel(workspace=tmp_path / "ws", config_dir=tmp_path / "conf")
    try:
        before = {p["id"] for p in (await rt.commands.ui_contributions())["panels"]}
        assert before, "a booted kernel serves the built-in panels"
        await rt.registry.unload_all()
        after = {p["id"] for p in (await rt.commands.ui_contributions())["panels"]}
        assert after == before, "unloading plugins leaves the client's own declaration in place"
        # A plugin's contribution would be gone with it; that is the mechanism, not a special case in the UI.
        rt.registry.register_ui_contribution("demo-feature", UiContribution(panels=[_panel("vault")]))
        assert "vault" in {p["id"] for p in (await rt.commands.ui_contributions())["panels"]}
        await rt.registry.unload_all()
        assert "vault" not in {p["id"] for p in (await rt.commands.ui_contributions())["panels"]}
    finally:
        await rt.registry.unload_all()


# ---------------------------------------------------------------- commands.list


async def test_commands_list_is_the_dispatch_table(tmp_path: Path):
    rt = await bootstrap_kernel(workspace=tmp_path / "ws", config_dir=tmp_path / "conf")
    try:
        rows = {row["name"]: row for row in await rt.commands.commands_list()}
        assert set(rows) >= set(BUILTIN_COMMANDS), "the table must list everything it dispatches"
        assert rows["submit_turn"]["args"][:2] == ["session_id", "prompt"]
        assert rows["commands.list"]["exposed"] and rows["ui.contributions"]["exposed"], "the new commands are dispatchable"
        assert rows["form_submit"] == {
            "name": "form_submit",
            "args": rows["form_submit"]["args"],
            "alias_of": "form.submit",
            "exposed": False,
        }, "an alias is reported as one and is not offered to a UI"
        for name, row in rows.items():
            if row["alias_of"] is None:
                assert hasattr(rt.commands, name.replace(".", "_").replace("-", "_")), f"{name} is listed without a handler"
    finally:
        await rt.registry.unload_all()


async def test_a_command_a_plugin_registers_shows_up(tmp_path: Path):
    rt = await bootstrap_kernel(workspace=tmp_path / "ws", config_dir=tmp_path / "conf")
    try:
        async def _noop(**kwargs):  # a plugin command, exactly as `register_command` receives one
            return {"ok": True}

        rt.commands._custom_commands["demo.ping"] = _noop
        rows = {row["name"]: row for row in await rt.commands.commands_list()}
        assert "demo.ping" in rows and rows["demo.ping"]["exposed"]
        assert await rt.commands.dispatch("demo.ping") == {"ok": True}
    finally:
        await rt.registry.unload_all()


# ---------------------------------------------------------------- ui.contributions


async def test_the_boot_response_names_the_vocabulary_the_client_needs(tmp_path: Path):
    rt = await bootstrap_kernel(workspace=tmp_path / "ws", config_dir=tmp_path / "conf")
    try:
        ui = await rt.commands.ui_contributions()
        assert ui["record_kinds"] == sorted(RECORD_KINDS), "the client cannot refuse an unknown kind it was never told"
        assert ui["settings_groups"], "the settings tabs come from the aggregated schema"
        assert all(panel["plugin"] for panel in ui["panels"]), "every panel says who declared it"
    finally:
        await rt.registry.unload_all()


async def test_a_panel_with_no_command_is_reported_not_served(tmp_path: Path):
    """A tab whose command does not exist is worse than no tab: it shows an error where a feature promised a view."""
    rt = await bootstrap_kernel(workspace=tmp_path / "ws", config_dir=tmp_path / "conf")
    try:
        from harness.sdk.manifest import UiPanel

        rt.registry.register_ui_contribution(
            "ghost-feature", UiContribution(panels=[UiPanel(id="ghost", title="Ghost", command="nope.nope")])
        )
        ui = await rt.commands.ui_contributions()
        assert "ghost" not in {p["id"] for p in ui["panels"]}
        assert [p["id"] for p in ui["unbacked_panels"]] == ["ghost"]
        assert ui["unbacked_panels"][0]["plugin"] == "ghost-feature", "the report says who to tell"
    finally:
        await rt.registry.unload_all()


async def test_a_plugin_can_paint_a_kind_the_client_does_not_know(tmp_path: Path):
    rt = await bootstrap_kernel(workspace=tmp_path / "ws", config_dir=tmp_path / "conf")
    try:
        rt.registry.register_ui_contribution(
            "obsidian",
            UiContribution(
                record_renderers=["ui.card"],
                client_module="static/plugins/obsidian.js",
            ),
        )
        ui = await rt.commands.ui_contributions()
        assert {"kind": "ui.card", "plugin": "obsidian"} in ui["record_renderers"]
        assert ui["client_modules"] == [
            {"plugin": "obsidian", "path": "static/plugins/obsidian.js", "url": "/static/plugins/obsidian.js"}
        ]
    finally:
        await rt.registry.unload_all()
