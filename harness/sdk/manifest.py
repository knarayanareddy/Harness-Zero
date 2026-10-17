"""
Plugin Manifest Definition & Parser.
Validates harness_plugin.toml manifests against SDK contracts.
"""
from __future__ import annotations

import logging
import re
import tomllib
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from harness.sdk.errors import IncompatiblePlugin, PluginLoadError
from harness.sdk.types import SDK_API_VERSION, Capability

logger = logging.getLogger(__name__)

NAME_PATTERN = re.compile(r"^[a-z][a-z0-9-]{1,40}$")
PANEL_ID_PATTERN = re.compile(r"^[a-z][a-z0-9_-]{1,40}$")
RECORD_KIND_PATTERN = re.compile(r"^[a-z][a-z0-9_.-]{2,63}$")

#: The built-in panel renderers. A plugin asks for one by name rather than shipping markup, which is what
#: keeps `script-src 'self'` and a single CSP story while still letting a feature own a panel.
VALID_UI_RENDERERS = frozenset({"table", "tree", "markdown", "list"})

#: Trust levels allowed to ship a client module (executed by the browser with the kernel's own privileges).
#: The values are ``TrustTier`` values from ``harness/kernel/registry.py`` — the SDK may not import the kernel,
#: so they are spelled here and `tests/kernel/test_ui_contributions.py` pins that every one of them is a tier the
#: registry can actually assign. It previously named ``"config"``, which is no tier at all, so *only* built-in
#: plugins could ever ship a module: a plugin the user installed under ``<config>/plugins`` (tier ``local``) got
#: "not trusted enough to run code in the browser" for a file the kernel itself had loaded. ``workspace`` stays
#: excluded on purpose: those plugins are opt-in untrusted code, and the message below tells their author to
#: declare panels and renderers instead of shipping a module.
CLIENT_MODULE_TRUST = frozenset({"builtin", "local"})

VALID_PROVIDES = frozenset({
    "provider",
    "tool",
    "policy",
    "executor",
    "sandbox",
    "context",
    "store",
    "strategy",
    "hook",
    "bridge",
    "command",
})

VALID_CAPABILITIES = frozenset(c.value for c in Capability)


@dataclass(slots=True)
class UiPanel:
    """A side panel a frontend should offer, and the command that fills it.

    `command` is not decoration: a panel that no command backs is a tab that shows nothing, which is how the
    client accumulated `data-notice` placeholders in an earlier release. The contract test
    (`tests/frontends/test_ui_contract.py`) refuses a panel whose command is not dispatchable.
    """

    id: str
    title: str
    command: str
    renderer: str = "list"

    def to_dict(self) -> dict[str, Any]:
        return {"id": self.id, "title": self.title, "command": self.command, "renderer": self.renderer}


@dataclass(slots=True)
class UiContribution:
    """What a plugin contributes to the client: `[ui]` in its manifest.

    Absent means the feature has no UI, which is the common case and needs no declaration. Everything here is
    a *name* the client resolves — a panel id, a record kind, a module path — so no plugin ships behaviour
    the kernel has to trust beyond the one allow-listed module file.
    """

    panels: list[UiPanel] = field(default_factory=list)
    record_renderers: list[str] = field(default_factory=list)
    client_module: str | None = None

    def to_dict(self) -> dict[str, Any]:
        return {
            "panels": [p.to_dict() for p in self.panels],
            "record_renderers": list(self.record_renderers),
            "client_module": self.client_module,
        }

    @classmethod
    def from_dict(cls, data: dict[str, Any], *, plugin: str) -> UiContribution:
        """Validates one `[ui]` table. Structural only: record kinds are checked against the kernel's
        vocabulary by the registry and the catalogue, which is where that list lives."""
        unknown = sorted(set(data) - {"panels", "record_renderers", "client_module"})
        if unknown:
            raise PluginLoadError(f"Plugin '{plugin}' [ui] declares unknown keys: {', '.join(unknown)}")

        panels: list[UiPanel] = []
        seen: set[str] = set()
        for raw in data.get("panels", []) or []:
            if not isinstance(raw, dict):
                raise PluginLoadError(f"Plugin '{plugin}' [ui].panels entries must be tables")
            missing = sorted({"id", "title", "command"} - set(raw))
            if missing:
                raise PluginLoadError(f"Plugin '{plugin}' panel {raw.get('id', '?')!r} is missing {', '.join(missing)}")
            pid = str(raw["id"])
            if not PANEL_ID_PATTERN.match(pid):
                raise PluginLoadError(
                    f"Plugin '{plugin}' panel id {pid!r}: must match ^[a-z][a-z0-9_-]{{1,40}}$"
                )
            if pid in seen:
                raise PluginLoadError(f"Plugin '{plugin}' declares panel {pid!r} twice")
            seen.add(pid)
            renderer = str(raw.get("renderer", "list"))
            if renderer not in VALID_UI_RENDERERS:
                raise PluginLoadError(
                    f"Plugin '{plugin}' panel {pid!r} asks for renderer {renderer!r}. "
                    f"Must be one of: {', '.join(sorted(VALID_UI_RENDERERS))}"
                )
            command = str(raw["command"]).strip()
            if not command:
                raise PluginLoadError(f"Plugin '{plugin}' panel {pid!r} has an empty `command`")
            panels.append(UiPanel(id=pid, title=str(raw["title"]), command=command, renderer=renderer))

        kinds = data.get("record_renderers", []) or []
        if not isinstance(kinds, list):
            raise PluginLoadError(f"Plugin '{plugin}' [ui].record_renderers must be a list of record kinds")
        cleaned: list[str] = []
        for kind in kinds:
            kind = str(kind).strip()
            if not RECORD_KIND_PATTERN.match(kind):
                raise PluginLoadError(f"Plugin '{plugin}' declares an invalid record kind in record_renderers: {kind!r}")
            if kind not in cleaned:
                cleaned.append(kind)

        module = data.get("client_module")
        if module is not None:
            module = str(module).strip()
            # Served at `/static/plugins/<plugin>/<path under static/>` (see `harness.kernel.plugin_paths`), i.e.
            # out of the plugin's own `static/` directory and nowhere else. An absolute path or an escaping `../`
            # would read outside the plugin dir, so it is refused here, at parse time, not by the route.
            if not module.startswith("static/") or module.startswith("/") or ".." in Path(module).parts:
                raise PluginLoadError(
                    f"Plugin '{plugin}' [ui].client_module {module!r}: must be a path under static/ inside the plugin dir"
                )
        return cls(panels=panels, record_renderers=cleaned, client_module=module or None)


@dataclass(slots=True)
class PluginManifest:
    name: str
    version: str
    api_version: str
    summary: str = ""
    entry: str = ""
    provides: list[str] = field(default_factory=list)
    capabilities: list[str] = field(default_factory=list)
    requires: list[str] = field(default_factory=list)
    platforms: list[str] = field(default_factory=list)
    settings_schema: str | None = None
    ui: UiContribution | None = None
    compat: dict[str, str] = field(default_factory=dict)
    dir_path: Path | None = None
    trust: str = "builtin"

    def to_dict(self) -> dict[str, Any]:
        return {
            "name": self.name,
            "version": self.version,
            "api_version": self.api_version,
            "summary": self.summary,
            "entry": self.entry,
            "provides": self.provides,
            "capabilities": self.capabilities,
            "requires": self.requires,
            "platforms": self.platforms,
            "settings_schema": self.settings_schema,
            "ui": self.ui.to_dict() if self.ui else None,
            "compat": self.compat,
            "dir_path": str(self.dir_path) if self.dir_path else None,
            "trust": self.trust,
        }

    @classmethod
    def from_dict(cls, data: dict[str, Any], dir_path: Path | None = None) -> PluginManifest:
        name = data.get("name", "")
        if not name:
            raise PluginLoadError("Plugin manifest missing required 'name' field")
        if not NAME_PATTERN.match(name):
            raise PluginLoadError(
                f"Invalid plugin name '{name}': must match pattern ^[a-z][a-z0-9-]{{1,40}}$"
            )

        version = data.get("version", "")
        if not version:
            raise PluginLoadError(f"Plugin '{name}' missing required 'version' field")

        api_ver = str(data.get("api_version", ""))
        if not api_ver:
            raise PluginLoadError(f"Plugin '{name}' missing required 'api_version' field")
        if api_ver != SDK_API_VERSION:
            raise IncompatiblePlugin(
                f"Plugin '{name}' requires api_version '{api_ver}', but current SDK_API_VERSION is '{SDK_API_VERSION}'"
            )

        provides = data.get("provides", [])
        if not isinstance(provides, list):
            raise PluginLoadError(f"Plugin '{name}' 'provides' must be a list of strings")
        for p in provides:
            if p not in VALID_PROVIDES:
                raise PluginLoadError(
                    f"Plugin '{name}' declares invalid component type in 'provides': '{p}'. "
                    f"Must be one of: {sorted(VALID_PROVIDES)}"
                )

        capabilities = data.get("capabilities", [])
        if not isinstance(capabilities, list):
            raise PluginLoadError(f"Plugin '{name}' 'capabilities' must be a list of strings")
        for c in capabilities:
            if c not in VALID_CAPABILITIES:
                raise PluginLoadError(
                    f"Plugin '{name}' declares unknown capability: '{c}'. "
                    f"Must be one of: {sorted(VALID_CAPABILITIES)}"
                )

        return cls(
            name=name,
            version=version,
            api_version=api_ver,
            summary=data.get("summary", ""),
            entry=data.get("entry", ""),
            provides=provides,
            capabilities=capabilities,
            requires=data.get("requires", []),
            platforms=data.get("platforms", []),
            settings_schema=data.get("settings_schema"),
            ui=UiContribution.from_dict(data["ui"], plugin=name) if isinstance(data.get("ui"), dict) else None,
            compat=data.get("compat", {}),
            dir_path=dir_path,
            trust=data.get("trust", "builtin"),
        )

    @classmethod
    def load(cls, path: Path | str, trust: str = "builtin") -> PluginManifest:
        """
        Loads and validates a harness_plugin.toml manifest from a file or directory.
        """
        p = Path(path).resolve()
        if p.is_dir():
            manifest_file = p / "harness_plugin.toml"
            dir_path = p
        else:
            manifest_file = p
            dir_path = p.parent

        if not manifest_file.exists():
            raise PluginLoadError(f"Plugin manifest file not found: {manifest_file}")

        try:
            with open(manifest_file, "rb") as f:
                doc = tomllib.load(f)
        except Exception as e:
            raise PluginLoadError(f"Failed to parse TOML manifest at '{manifest_file}': {e}") from e

        if "plugin" not in doc or not isinstance(doc["plugin"], dict):
            raise PluginLoadError(f"Manifest at '{manifest_file}' missing required [plugin] table")

        plugin_data = dict(doc["plugin"])
        if "settings" in doc and isinstance(doc["settings"], dict):
            plugin_data["settings_schema"] = doc["settings"].get("schema")
        if "ui" in doc and isinstance(doc["ui"], dict):
            plugin_data["ui"] = doc["ui"]
        if "compat" in doc and isinstance(doc["compat"], dict):
            plugin_data["compat"] = doc["compat"]
        plugin_data["trust"] = trust

        manifest = cls.from_dict(plugin_data, dir_path=dir_path)
        if manifest.ui and manifest.ui.client_module and trust not in CLIENT_MODULE_TRUST:
            # The module is dropped, the plugin is kept. Refusing to *load* would turn one authoring mistake in a
            # workspace plugin into a kernel that will not boot — and `discover_directory` re-raises every other
            # manifest error, so that would be the behaviour. Dropping it costs the plugin the custom rendering
            # and nothing else: its `[ui].panels` still resolve, through the generic renderer its declaration
            # names, which is the fallback WS-05a built for exactly this. `harness plugin doctor` and the log
            # line are where the author learns why; the web route re-checks the tier rather than trusting here.
            logger.warning(
                "Plugin '%s' declares [ui].client_module (%s) but its trust level %r may not run code in the "
                "browser (allowed: %s); the module is ignored and its panels use their declared renderer. Move "
                "the plugin under the config directory to ship a module.",
                manifest.name,
                manifest.ui.client_module,
                trust,
                ", ".join(sorted(CLIENT_MODULE_TRUST)),
            )
            manifest.ui.client_module = None
        return manifest
