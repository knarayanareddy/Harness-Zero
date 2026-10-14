"""
Plugin Manifest Definition & Parser.
Validates harness_plugin.toml manifests against SDK contracts.
"""
from __future__ import annotations

import re
import tomllib
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from harness.sdk.errors import IncompatiblePlugin, PluginLoadError
from harness.sdk.types import SDK_API_VERSION, Capability

NAME_PATTERN = re.compile(r"^[a-z][a-z0-9-]{1,40}$")

VALID_PROVIDES = frozenset({
    "provider",
    "tool",
    "policy",
    "executor",
    "context",
    "store",
    "strategy",
    "hook",
    "bridge",
    "command",
})

VALID_CAPABILITIES = frozenset(c.value for c in Capability)


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
        if "compat" in doc and isinstance(doc["compat"], dict):
            plugin_data["compat"] = doc["compat"]
        plugin_data["trust"] = trust

        return cls.from_dict(plugin_data, dir_path=dir_path)
