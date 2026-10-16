"""Where a discovered plugin lives on disk, and the one address its client module may be served at.

`DiscoveredPlugin` carries the manifest and its path; the plugin *directory* is what README,
`settings.schema.json`, the conformance test and `[ui].client_module` are resolved against. That fact is needed on
both sides of the line `tests/kernel/test_import_boundaries.py` draws — the kernel's loader and catalogue, and a
frontend that has to serve the file the browser will import — so it lives in the SDK, the layer both may read.
`harness/kernel/plugin_paths.py` re-exports it for kernel callers.

The URL is here for the same reason. `PluginRegistry.ui_contributions()` builds the string the browser imports and
`HarnessWebServer` mounts the route that answers it; two copies of one URL is a 404 in the browser and a green
build, which is exactly how `[ui].client_module` shipped for a whole phase (R58): the URL was ``"/" + path``, which
pointed into the client's own `/static/` tree, and a module that fails to import only makes a panel generic.
"""
from __future__ import annotations

from pathlib import Path
from typing import Any

#: Root a plugin's own client modules are served under. Namespaced by plugin id, so two features may both write
#: `static/panel.js` and the address still says whose file the browser is fetching.
CLIENT_MODULE_ROOT = "/static/plugins"

#: What may be served out of a plugin's `static/` directory. A panel module is JS; a stylesheet is the only other
#: thing a `[ui]` block could legitimately want (and a plugin cannot declare one today).
CLIENT_MODULE_SUFFIXES = frozenset({".js", ".mjs", ".css"})

#: `UiContribution.from_dict` requires the declaration to start with this; it is the manifest's contract with the
#: loader, not part of the address, so the URL drops it and the route resolves the tail inside `static/`.
_STATIC_PREFIX = "static/"


def plugin_dir(discovered: Any) -> Path:
    """The directory holding ``harness_plugin.toml`` for a discovered plugin."""
    manifest = getattr(discovered, "manifest", None)
    dir_path = getattr(manifest, "dir_path", None)
    if dir_path:
        return Path(dir_path)
    manifest_path = getattr(discovered, "manifest_path", None)
    if manifest_path:
        return Path(manifest_path).parent
    origin = getattr(discovered, "origin", "")
    return Path(origin).parent if origin else Path.cwd()


def client_module_url(plugin: str, declared_path: str) -> str:
    """`("obsidian", "static/plugins/obsidian.js")` -> `/static/plugins/obsidian/plugins/obsidian.js`."""
    rel = str(declared_path).strip().lstrip("/")
    if rel.startswith(_STATIC_PREFIX):
        rel = rel[len(_STATIC_PREFIX):]
    return f"{CLIENT_MODULE_ROOT}/{plugin}/{rel}"


def client_module_file(discovered: Any, name: str, tail: str) -> Path | None:
    """Resolve a requested plugin-static path, or refuse it.

    Returns the file only when all four hold: the plugin declared ``[ui].client_module`` and *this* is the address
    that declaration maps to (so a plugin directory cannot be browsed for whatever else it contains); the resolved
    path stays inside that plugin's own ``static/`` directory (symlinks included, because ``resolve()`` is what
    makes the comparison honest); the suffix is a module or stylesheet; and the file exists. A frontend answers 404
    on ``None`` — keeping the rule here, rather than inside a handler, means the security decision is testable
    without a socket and every frontend gets the same answer for free.

    ``name`` is the registry's key for the plugin, which is also what ``ui_contributions`` puts in the URL; taking
    it as an argument instead of reading ``manifest.name`` keeps the two derivations identical even for a manifest
    whose name and directory disagree.
    """
    if not tail or "\0" in tail:
        return None
    module = getattr(getattr(getattr(discovered, "manifest", None), "ui", None), "client_module", None)
    if not module:
        return None
    # The name in the address has to be the plugin's own name, not merely the key a caller happened to look this
    # plugin up by. Discovery keys by `manifest.name`, so on the live path the two are the same string and this
    # refuses nothing; it is here because `name` appears on both sides of the address comparison below, which
    # makes that comparison a tautology for *any* name — a caller that resolved a plugin from its folder name
    # would otherwise be handed the file. `tests/kernel/test_plugin_static_paths.py` found that by trying.
    if str(name) != str(getattr(getattr(discovered, "manifest", None), "name", "") or ""):
        return None
    # `tail` is what follows `<root>/<plugin>/` in the request, so the address the browser used has to be rebuilt
    # from all three parts before it can be compared with what the manifest declared.
    if client_module_url(name, module) != f"{CLIENT_MODULE_ROOT}/{name}/{str(tail).lstrip('/')}":
        return None  # declared, but not at this address: an undeclared sibling file stays unreadable
    static_root = (plugin_dir(discovered) / "static").resolve()
    # The declared path is relative to the plugin directory, `tail` to its `static/` — so containment is checked
    # against the declared file, never against a path the request chose.
    candidate = (static_root / str(tail).lstrip("/")).resolve()
    if static_root not in candidate.parents:
        return None  # escaped the plugin's `static/` — through `..`, a symlink, or an absolute path
    if candidate.suffix not in CLIENT_MODULE_SUFFIXES:
        return None
    return candidate if candidate.is_file() else None


def client_module_trusted(discovered: Any) -> bool:
    """Whether this plugin's trust level may run a `[ui].client_module` in the browser.

    `PluginManifest.load` already drops the declaration for a tier that is not allowed, so a route that checked
    nothing would usually be safe by accident. It checks anyway because a module is the one contribution that
    executes with the page's own privileges, and because a registry assembled without going through `load` — a
    test, a second frontend, a plugin loaded from an entry point — has had neither rule applied.
    """
    from harness.sdk.manifest import CLIENT_MODULE_TRUST  # local: `manifest` is the definition, this is the policy

    tier = getattr(discovered, "tier", None)
    return getattr(tier, "value", tier) in CLIENT_MODULE_TRUST
