"""WS-05c — the address a plugin's client module is served at, and what must never be readable through it.

Two halves. `client_module_url` is the string the kernel hands the browser; `client_module_file` is the whole
security decision behind that address. They are tested together because the interesting failure is their
*disagreement*: a URL the route does not serve leaves a plugin's panel silently generic (which is exactly what
happened for a whole phase — the URL was `"/" + path`, landing in the client's own `/static/` tree where no such
file existed), and a route more permissive than the URL would let anyone walk a plugin directory file by file.

The trust half (R58) belongs here too: "only a trusted plugin may ship browser code" was unreachable while
`discover_directory` let `PluginManifest.load` assume `trust="builtin"` for every directory on disk.
"""
from __future__ import annotations

import logging
from pathlib import Path

import pytest

from harness.kernel.bus import EventBus
from harness.kernel.plugin_paths import (
    CLIENT_MODULE_ROOT,
    client_module_file,
    client_module_url,
    plugin_dir,
)
from harness.kernel.registry import PluginRegistry, TrustTier
from harness.kernel.secrets import Secrets
from harness.kernel.settings import SettingsStore
from harness.sdk.manifest import PluginManifest

REPO = Path(__file__).resolve().parents[2]
WEB_SERVER_SOURCE = REPO / "harness" / "frontends" / "web" / "server.py"

MODULE_BODY = "export const panels = { vault: { async render() {} } };\n"


def _manifest(*, module: str | None) -> str:
    ui = 'panels = [{ id = "vault", title = "Vault", command = "obsidian.search", renderer = "table" }]\n'
    if module:
        ui += f'client_module = "{module}"\n'
    return (
        "[plugin]\n"
        'name = "obsidian"\n'
        'version = "1.0.0"\n'
        'api_version = "1"\n'
        'summary = "Vault search as a tool and a panel"\n'
        'provides = ["tool"]\n'
        'entry = "plugin:ObsidianPlugin"\n'
        "\n"
        "[ui]\n" + ui
    )


def _plugin_root(root: Path, *, module: str = "static/plugins/obsidian.js", extra: dict[str, str] | None = None) -> Path:
    """A plugin directory as discovery finds it: `<root>/<family>/<name>/harness_plugin.toml`."""
    plugin = root / "tools" / "obsidian"
    (plugin / "static" / "plugins").mkdir(parents=True, exist_ok=True)
    (plugin / "harness_plugin.toml").write_text(_manifest(module=module), encoding="utf-8")
    (plugin / "plugin.py").write_text("class ObsidianPlugin:\n    pass\n", encoding="utf-8")
    (plugin / "static" / "plugins" / "obsidian.js").write_text(MODULE_BODY, encoding="utf-8")
    for name, body in (extra or {}).items():
        path = plugin / name
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(body, encoding="utf-8")
    return root


def _registry(root: Path, tier: TrustTier = TrustTier.LOCAL) -> PluginRegistry:
    reg = PluginRegistry(
        workspace=root.parent / "ws",
        settings=SettingsStore(root.parent / "settings.json"),
        secrets=Secrets(root.parent / "secrets"),
        bus=EventBus(),
    )
    reg.discover_directory(root, tier)
    return reg


# ---------------------------------------------------------------- the address the browser is told


def test_the_url_is_namespaced_by_plugin_so_two_features_may_use_the_same_filename():
    assert client_module_url("obsidian", "static/plugins/obsidian.js") == (
        f"{CLIENT_MODULE_ROOT}/obsidian/plugins/obsidian.js"
    )
    assert client_module_url("vaulted", "static/plugins/obsidian.js") == (
        f"{CLIENT_MODULE_ROOT}/vaulted/plugins/obsidian.js"
    )
    assert client_module_url("obsidian", "static/panel.js") == f"{CLIENT_MODULE_ROOT}/obsidian/panel.js"


def test_the_route_is_built_from_the_same_constant_as_the_url():
    """A prefix kept in two files is a 404 in the browser, and no test comparing literals would notice a rename."""
    source = WEB_SERVER_SOURCE.read_text(encoding="utf-8")
    assert "CLIENT_MODULE_ROOT" in source, "the route must be built from the kernel's constant, not its own copy"
    assert 'add_get(\n            f"{CLIENT_MODULE_ROOT}' in source or "f\"{CLIENT_MODULE_ROOT}/" in source
    # ...and registered before `/static/`, whose prefix would otherwise swallow it (aiohttp matches in order).
    assert source.index("CLIENT_MODULE_ROOT}/") < source.index('add_static("/static/"'), (
        "the plugin route must be registered before the client's `/static/` mount"
    )


# ---------------------------------------------------------------- what that address resolves to


def test_the_declared_module_resolves_to_the_bytes_the_browser_will_import(tmp_path: Path):
    root = _plugin_root(tmp_path / "plugins")
    dp = _registry(root).discovered["obsidian"]
    found = client_module_file(dp, "obsidian", "plugins/obsidian.js")
    assert found is not None
    assert found.read_text(encoding="utf-8") == MODULE_BODY
    assert found.is_relative_to(plugin_dir(dp) / "static")


def test_a_declared_stylesheet_is_served_too_because_the_manifest_lets_it_be_declared(tmp_path: Path):
    root = _plugin_root(tmp_path / "plugins", module="static/themes/vault.css", extra={"static/themes/vault.css": ".vault{color:red}\n"})
    dp = _registry(root).discovered["obsidian"]
    assert client_module_file(dp, "obsidian", "themes/vault.css") is not None


@pytest.mark.parametrize(
    "tail",
    [
        "plugins/other.js",  # a real file in the same directory, never declared
        "plugins/../plugins/other.js",  # the same file, spelled cleverly
        "../../../etc/passwd",
        "themes/vault.css",  # the declared name of a *different* fixture's module
        "",
    ],
)
def test_anything_not_declared_is_nothing(tmp_path: Path, tail: str):
    root = _plugin_root(tmp_path / "plugins", extra={"static/plugins/other.js": "// no\n"})
    dp = _registry(root).discovered["obsidian"]
    assert client_module_file(dp, "obsidian", tail) is None, tail


def test_a_symlink_cannot_carry_a_request_out_of_the_plugin_directory(tmp_path: Path):
    outside = tmp_path / "elsewhere.js"
    outside.write_text("export const leaked = 1;\n", encoding="utf-8")
    # The module is *declared* at the path the symlink sits on, so this exercises containment alone.
    root = _plugin_root(tmp_path / "plugins")
    link = root / "tools" / "obsidian" / "static" / "plugins" / "obsidian.js"
    link.unlink()
    link.symlink_to(outside)
    dp = _registry(root).discovered["obsidian"]
    assert client_module_file(dp, "obsidian", "plugins/obsidian.js") is None


def test_a_plugin_that_declared_no_module_serves_nothing_even_when_it_has_files(tmp_path: Path):
    root = tmp_path / "plugins"
    plugin = root / "tools" / "plain"
    (plugin / "static").mkdir(parents=True)
    (plugin / "static" / "x.js").write_text("// never declared\n", encoding="utf-8")
    (plugin / "harness_plugin.toml").write_text(
        '[plugin]\nname = "plain"\nversion = "1.0.0"\napi_version = "1"\nprovides = ["tool"]\n', encoding="utf-8"
    )
    dp = _registry(root).discovered["plain"]
    assert (dp.manifest.ui is None) or (dp.manifest.ui.client_module is None)
    assert client_module_file(dp, "plain", "x.js") is None


# ---------------------------------------------------------------- trust: who may ship browser code


def test_a_workspace_plugin_loses_its_module_but_keeps_its_panel(tmp_path: Path, caplog):
    """An untrusted tier may not run code in the browser — and one bad declaration must not break the kernel.

    A malformed manifest is re-raised by `discover_directory` (a plugin that cannot be read is a bug to fix). The
    browser-trust rule is different in kind: dropping the module leaves `[ui].panels` intact, rendered by the
    generic renderer the declaration names, so refusing to boot over it would hand any workspace plugin the
    power to take the studio down.
    """
    root = _plugin_root(tmp_path / "plugins")
    with caplog.at_level(logging.WARNING, logger="harness.sdk.manifest"):
        dp = _registry(root, tier=TrustTier.WORKSPACE).discovered["obsidian"]
    assert dp.manifest.ui.client_module is None, "the module survived an untrusted tier"
    assert [panel.id for panel in dp.manifest.ui.panels] == ["vault"], "the panel was taken down with it"
    assert "ignored" in caplog.text and "obsidian" in caplog.text, caplog.text
    assert client_module_file(dp, "obsidian", "plugins/obsidian.js") is None


def test_a_locally_installed_plugin_may_ship_browser_code(tmp_path: Path):
    """`<config>/plugins` (tier `local`) is where a user installs a plugin they trust.

    Before R58 was fixed this could not happen: `CLIENT_MODULE_TRUST` named `"config"`, which is no tier, so a
    user-installed plugin was told it was untrusted for declaring a file the kernel had already loaded.
    """
    root = _plugin_root(tmp_path / "plugins")
    dp = _registry(root, tier=TrustTier.LOCAL).discovered["obsidian"]
    assert dp.manifest.ui.client_module == "static/plugins/obsidian.js"
    assert client_module_file(dp, "obsidian", "plugins/obsidian.js") is not None


def test_a_manifest_built_without_a_tier_is_not_assumed_untrusted():
    """`from_dict` has no trust argument, so enforcement lives where the tier is known (discovery, and the route)."""
    manifest = PluginManifest.from_dict(
        {
            "name": "obsidian",
            "version": "1.0.0",
            "api_version": "1",
            "provides": ["tool"],
            "ui": {"panels": [], "client_module": "static/plugins/obsidian.js"},
        }
    )
    assert manifest.ui.client_module == "static/plugins/obsidian.js"


def test_the_address_is_keyed_on_the_registry_name_not_the_folder_it_sits_in(tmp_path: Path):
    """A plugin's folder may be called anything, and the two halves must not read it separately (R59).

    `discover_directory` keys a plugin by `manifest.name` — the scaffold's directory name comes from the command
    line argument instead, so the two disagree routinely. Build the URL from the folder in one half and from the
    registry key in the other, and the file exists, the route is registered, the manifest is valid, and the
    browser still gets a 404 for it with nothing in the page to say why. Both derivations go through
    `client_module_url(name, …)`, so the assertion worth having is that the *key* is what the address carries and
    that a request assembled from the folder is refused like any stranger's file.
    """
    root = tmp_path / "plugins"
    plugin = root / "probe-plugin-dir"
    (plugin / "static" / "plugins").mkdir(parents=True)
    (plugin / "harness_plugin.toml").write_text(_manifest(module="static/plugins/obsidian.js"), encoding="utf-8")
    (plugin / "static" / "plugins" / "obsidian.js").write_text(MODULE_BODY, encoding="utf-8")

    reg = _registry(root, tier=TrustTier.LOCAL)
    assert list(reg.discovered) == ["obsidian"], "discovery keys by manifest name, not by folder"
    dp = reg.discovered["obsidian"]

    name = next(iter(reg.discovered))
    url = client_module_url(name, dp.manifest.ui.client_module)
    assert url == f"{CLIENT_MODULE_ROOT}/obsidian/plugins/obsidian.js"
    tail = url.removeprefix(f"{CLIENT_MODULE_ROOT}/{name}/")
    assert client_module_file(dp, name, tail) == (plugin / "static" / "plugins" / "obsidian.js").resolve()
    # the folder name is not an address: refusing it is what keeps one plugin from reading another's files
    assert client_module_file(dp, "probe-plugin-dir", "plugins/obsidian.js") is None


def test_every_module_the_shipped_tree_advertises_can_actually_be_served(tmp_path: Path):
    """The gate for WS-06+: a built-in that declares `[ui].client_module` must have that file on disk.

    Zero of the 39 built-ins declare one today, which is exactly how `ui.contributions` spent a whole phase
    advertising URLs nothing served — there was no case for the check to bite on. This asserts over the real tree
    rather than a fixture so the first plugin to ship a module is covered the moment it lands.
    """
    reg = PluginRegistry(
        workspace=tmp_path / "ws",
        settings=SettingsStore(tmp_path / "settings.json"),
        secrets=Secrets(tmp_path / "secrets"),
        bus=EventBus(),
    )
    reg.discover_directory(REPO / "harness" / "plugins", TrustTier.BUILTIN)
    declared = {
        name: dp
        for name, dp in reg.discovered.items()
        if getattr(getattr(dp.manifest, "ui", None), "client_module", None)
    }
    for name, dp in declared.items():
        url = client_module_url(name, dp.manifest.ui.client_module)
        tail = url.removeprefix(f"{CLIENT_MODULE_ROOT}/{name}/")
        assert client_module_file(dp, name, tail) is not None, (
            f"{name} advertises {url}, which resolves to nothing"
        )
