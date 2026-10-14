"""
Plugin registry for discovery, manifest validation, dependency resolution,
capability clamping, lifecycle management, and collision prevention.
Fulfills KRN-05 and enforces architectural trust tiers.
"""
from __future__ import annotations

import importlib
import importlib.metadata
import logging
import re
from dataclasses import dataclass
from enum import Enum
from pathlib import Path
from typing import Literal

from harness.kernel.bus import EventBus
from harness.kernel.secrets import Secrets
from harness.kernel.settings import SettingsStore
from harness.sdk.context import SecretsView, SettingsView
from harness.sdk.contracts import (
    ContextSource,
    Executor,
    Hook,
    Plugin,
    Policy,
    Provider,
    Strategy,
    ToolHandler,
)
from harness.sdk.errors import (
    CapabilityDenied,
    IncompatiblePlugin,
    PluginLoadError,
)
from harness.sdk.events import Event
from harness.sdk.manifest import PluginManifest
from harness.sdk.types import SDK_API_VERSION, Capability, ToolSpec

logger = logging.getLogger(__name__)

PLUGIN_NAME_PATTERN = re.compile(r"^[a-z][a-z0-9-]{1,40}$")


class TrustTier(str, Enum):
    BUILTIN = "builtin"
    LOCAL = "local"
    WORKSPACE = "workspace"
    THIRD_PARTY = "third_party"


# Maximum grantable capabilities per trust tier (§9.1)
TIER_ALLOWED_CAPABILITIES: dict[TrustTier, frozenset[Capability]] = {
    TrustTier.BUILTIN: frozenset(
        cap for cap in Capability if cap != Capability.APPROVAL_BYPASS
    ),
    TrustTier.LOCAL: frozenset(
        cap for cap in Capability if cap != Capability.APPROVAL_BYPASS
    ),
    TrustTier.WORKSPACE: frozenset(
        cap for cap in Capability if cap != Capability.APPROVAL_BYPASS
    ),
    TrustTier.THIRD_PARTY: frozenset(
        cap
        for cap in Capability
        if cap not in (Capability.APPROVAL_BYPASS, Capability.SECRETS)
    ),
}


def clamp_capabilities(
    manifest_capabilities: frozenset[Capability] | set[Capability] | list[Capability],
    tier: TrustTier,
    override: frozenset[Capability] | set[Capability] | None = None,
) -> frozenset[Capability]:
    """
    Computes effective capabilities: manifest ∩ tier_allowance ∩ user_override.
    approval:bypass is never granted to plugins.
    """
    requested = frozenset(manifest_capabilities)
    tier_allowed = TIER_ALLOWED_CAPABILITIES.get(tier, frozenset())
    effective = requested & tier_allowed

    # Ensure approval:bypass is strictly never grantable via manifest
    effective = effective - frozenset([Capability.APPROVAL_BYPASS])

    if override is not None:
        effective = effective & frozenset(override)

    return effective


@dataclass
class DiscoveredPlugin:
    manifest: PluginManifest
    tier: TrustTier
    origin: str
    manifest_path: Path | None = None


@dataclass
class LoadedPlugin:
    manifest: PluginManifest
    instance: Plugin
    tier: TrustTier
    effective_capabilities: frozenset[Capability]
    origin: str
    context: PluginContextImpl


class PluginContextImpl:
    """Implementation of PluginContext provided to plugins during lifecycle."""

    def __init__(
        self,
        name: str,
        workspace: Path,
        data_dir: Path,
        settings: SettingsView,
        secrets: SecretsView,
        capabilities: frozenset[Capability],
        registry: PluginRegistry,
    ):
        self.name = name
        self.workspace = workspace
        self.data_dir = data_dir
        self.settings = settings
        self.secrets = secrets
        self.capabilities = capabilities
        self.log = logging.getLogger(f"harness.plugins.{name}")
        self._registry = registry

    def register_tool(self, spec: ToolSpec, handler: ToolHandler) -> None:
        """Registers a tool provided by this plugin, checking collisions and capabilities."""
        # 1. Check capability clamping: tool capabilities cannot exceed plugin capabilities
        for req_cap in spec.capabilities:
            if req_cap not in self.capabilities:
                raise CapabilityDenied(
                    capability=str(req_cap.value if isinstance(req_cap, Capability) else req_cap),
                    plugin=self.name,
                    message=(
                        f"Tool '{spec.name}' requests capability '{req_cap}' "
                        f"which exceeds plugin '{self.name}' effective capabilities"
                    ),
                )
        self._registry._register_tool(spec, handler, self.name)

    def register_provider(self, p: Provider) -> None:
        self._registry._register_provider(p, self.name)

    def register_policy(self, p: Policy) -> None:
        self._registry._register_policy(p, self.name)

    def register_executor(self, e: Executor) -> None:
        self._registry._register_executor(e, self.name)

    def register_context_source(self, s: ContextSource) -> None:
        self._registry._register_context_source(s, self.name)

    def register_store(
        self, kind: Literal["transcript", "search", "artifacts", "cost"], s: object
    ) -> None:
        self._registry._register_store(kind, s, self.name)

    def register_strategy(self, s: Strategy) -> None:
        self._registry._register_strategy(s, self.name)

    def register_hook(self, h: Hook) -> None:
        self._registry._register_hook(h, self.name)


class PluginRegistry:
    """
    Central registry for plugin discovery, capability clamping,
    lifecycle management, and component routing.
    """

    def __init__(
        self,
        workspace: Path,
        settings: SettingsStore,
        secrets: Secrets,
        bus: EventBus | None = None,
        data_dir: Path | None = None,
    ):
        self.workspace = workspace
        self.settings = settings
        self.secrets = secrets
        self.bus = bus
        self.data_dir = data_dir or (workspace / ".harness")

        # Discovered and loaded plugins
        self._discovered: dict[str, DiscoveredPlugin] = {}
        self._loaded: dict[str, LoadedPlugin] = {}

        # Component collections: item -> (component, plugin_name)
        self.tools: dict[str, tuple[ToolSpec, ToolHandler, str]] = {}
        self.providers: dict[str, tuple[Provider, str]] = {}
        self.policies: list[tuple[Policy, str]] = []
        self.executors: dict[str, tuple[Executor, str]] = {}
        self.context_sources: list[tuple[ContextSource, str]] = []
        self.stores: dict[str, tuple[object, str]] = {}
        self.strategies: dict[str, tuple[Strategy, str]] = {}
        self.hooks: list[tuple[Hook, str]] = []

    def _register_tool(self, spec: ToolSpec, handler: ToolHandler, plugin_name: str) -> None:
        if spec.name in self.tools:
            existing_spec, _, existing_origin = self.tools[spec.name]
            raise PluginLoadError(
                f"Duplicate tool name '{spec.name}' registered by plugin '{plugin_name}' "
                f"and already registered by plugin '{existing_origin}'"
            )
        self.tools[spec.name] = (spec, handler, plugin_name)

    def _register_provider(self, p: Provider, plugin_name: str) -> None:
        if p.name in self.providers:
            _, existing_origin = self.providers[p.name]
            raise PluginLoadError(
                f"Duplicate provider name '{p.name}' registered by plugin '{plugin_name}' "
                f"and already registered by plugin '{existing_origin}'"
            )
        self.providers[p.name] = (p, plugin_name)

    def _register_policy(self, p: Policy, plugin_name: str) -> None:
        self.policies.append((p, plugin_name))
        # Keep policies sorted by order attribute (ascending)
        self.policies.sort(key=lambda item: getattr(item[0], "order", 500))

    def _register_executor(self, e: Executor, plugin_name: str) -> None:
        name = getattr(e, "name", plugin_name)
        if name in self.executors:
            _, existing_origin = self.executors[name]
            raise PluginLoadError(
                f"Duplicate executor name '{name}' registered by plugin '{plugin_name}' "
                f"and already registered by plugin '{existing_origin}'"
            )
        self.executors[name] = (e, plugin_name)

    def _register_context_source(self, s: ContextSource, plugin_name: str) -> None:
        self.context_sources.append((s, plugin_name))

    def _register_store(self, kind: str, s: object, plugin_name: str) -> None:
        if kind in self.stores:
            _, existing_origin = self.stores[kind]
            raise PluginLoadError(
                f"Duplicate store kind '{kind}' registered by plugin '{plugin_name}' "
                f"and already registered by plugin '{existing_origin}'"
            )
        self.stores[kind] = (s, plugin_name)

    def _register_strategy(self, s: Strategy, plugin_name: str) -> None:
        if s.name in self.strategies:
            _, existing_origin = self.strategies[s.name]
            raise PluginLoadError(
                f"Duplicate strategy name '{s.name}' registered by plugin '{plugin_name}' "
                f"and already registered by plugin '{existing_origin}'"
            )
        self.strategies[s.name] = (s, plugin_name)

    def _register_hook(self, h: Hook, plugin_name: str) -> None:
        self.hooks.append((h, plugin_name))

    def register_discovered(self, plugin: DiscoveredPlugin) -> None:
        """Registers a discovered plugin manifest, validating API version and naming."""
        name = plugin.manifest.name
        if not PLUGIN_NAME_PATTERN.match(name):
            raise PluginLoadError(
                f"Invalid plugin name '{name}'. Must match pattern {PLUGIN_NAME_PATTERN.pattern}"
            )

        if plugin.manifest.api_version != SDK_API_VERSION:
            raise IncompatiblePlugin(
                f"Plugin '{name}' requires api_version '{plugin.manifest.api_version}', "
                f"but current SDK version is '{SDK_API_VERSION}'"
            )

        if name in self._discovered:
            existing = self._discovered[name]
            raise PluginLoadError(
                f"Plugin name collision for '{name}': origin 1='{existing.origin}', origin 2='{plugin.origin}'"
            )

        self._discovered[name] = plugin

    def discover_directory(self, root: Path, tier: TrustTier) -> list[DiscoveredPlugin]:
        """Discovers plugins in subdirectories containing harness_plugin.toml."""
        found: list[DiscoveredPlugin] = []
        if not root.exists() or not root.is_dir():
            return found

        for entry in root.iterdir():
            if entry.is_dir():
                manifest_file = entry / "harness_plugin.toml"
                if manifest_file.exists():
                    try:
                        manifest = PluginManifest.load(manifest_file)
                        dp = DiscoveredPlugin(
                            manifest=manifest,
                            tier=tier,
                            origin=str(manifest_file),
                            manifest_path=manifest_file,
                        )
                        self.register_discovered(dp)
                        found.append(dp)
                    except Exception as err:
                        logger.warning(f"Failed to load plugin manifest at {manifest_file}: {err}")
                        raise
        return found

    def discover_entry_points(self, group: str = "harness.plugins") -> list[DiscoveredPlugin]:
        """Discovers plugins registered as Python package entry points."""
        found: list[DiscoveredPlugin] = []
        try:
            eps = importlib.metadata.entry_points(group=group)
        except Exception:
            return found

        for ep in eps:
            try:
                # Load entry point object (could be manifest dict or manifest path or plugin class)
                loaded = ep.load()
                manifest: PluginManifest
                if isinstance(loaded, PluginManifest):
                    manifest = loaded
                elif isinstance(loaded, dict):
                    manifest = PluginManifest.from_dict(loaded)
                elif hasattr(loaded, "manifest") and isinstance(loaded.manifest, PluginManifest):
                    manifest = loaded.manifest
                else:
                    logger.warning(f"Entry point {ep.name} did not expose a PluginManifest")
                    continue

                dp = DiscoveredPlugin(
                    manifest=manifest,
                    tier=TrustTier.LOCAL,
                    origin=f"entry_point:{ep.name}",
                )
                self.register_discovered(dp)
                found.append(dp)
            except Exception as err:
                logger.warning(f"Failed to load entry point {ep.name}: {err}")
                raise
        return found

    def discover_all(
        self,
        builtin_dir: Path | None = None,
        local_dir: Path | None = None,
        workspace_dir: Path | None = None,
        include_entry_points: bool = True,
    ) -> None:
        """Discovers plugins across all standard discovery roots in tier order."""
        # 1. Built-in plugins
        if builtin_dir and builtin_dir.exists():
            self.discover_directory(builtin_dir, TrustTier.BUILTIN)

        # 2. Local user plugins (~/.harness/plugins)
        if local_dir and local_dir.exists():
            self.discover_directory(local_dir, TrustTier.LOCAL)

        # 3. Entry points
        if include_entry_points:
            self.discover_entry_points()

        # 4. Workspace plugins (<workspace>/.harness/plugins)
        if workspace_dir and workspace_dir.exists():
            trusted = self.settings.get("kernel.plugins.trusted_workspace_plugins", False)
            if trusted:
                self.discover_directory(workspace_dir, TrustTier.WORKSPACE)
            else:
                logger.info(
                    f"Skipping workspace plugins at {workspace_dir} "
                    "because kernel.plugins.trusted_workspace_plugins is false"
                )

    def resolve_load_order(self) -> list[DiscoveredPlugin]:
        """
        Performs dependency topological sort according to manifest.requires.
        Raises PluginLoadError on missing dependencies or dependency cycles.
        """
        visited: set[str] = set()
        visiting: set[str] = set()
        order: list[DiscoveredPlugin] = []

        disabled = set(self.settings.get("kernel.plugins.disabled", []))
        enabled_list = self.settings.get("kernel.plugins.enabled", [])
        enabled_filter = set(enabled_list) if enabled_list else None

        # Filter out disabled plugins
        candidates: dict[str, DiscoveredPlugin] = {}
        for name, dp in self._discovered.items():
            if name in disabled:
                continue
            if enabled_filter is not None and name not in enabled_filter:
                continue
            candidates[name] = dp

        def dfs(name: str, stack: list[str]) -> None:
            if name in visiting:
                cycle_str = " -> ".join(stack + [name])
                raise PluginLoadError(f"Dependency cycle detected: {cycle_str}")
            if name in visited:
                return

            visiting.add(name)
            stack.append(name)

            dp = candidates.get(name)
            if dp is None:
                # Missing required dependency
                raise PluginLoadError(
                    f"Plugin '{stack[-2]}' requires dependency '{name}', which was not found or is disabled."
                )

            for req in dp.manifest.requires:
                dfs(req, stack)

            stack.pop()
            visiting.remove(name)
            visited.add(name)
            order.append(dp)

        for name in sorted(candidates.keys()):
            if name not in visited:
                dfs(name, [])

        return order

    def _instantiate_plugin(self, dp: DiscoveredPlugin) -> Plugin:
        """Instantiates the Plugin contract class referenced in entry."""
        entry_str = dp.manifest.entry
        if not entry_str:
            raise PluginLoadError(f"Plugin '{dp.manifest.name}' declares empty entry.")

        try:
            if ":" in entry_str:
                mod_name, cls_name = entry_str.split(":", 1)
                mod = importlib.import_module(mod_name)
                cls = getattr(mod, cls_name)
            else:
                mod = importlib.import_module(entry_str)
                if hasattr(mod, "Plugin"):
                    cls = mod.Plugin
                elif hasattr(mod, "plugin"):
                    return mod.plugin
                else:
                    raise AttributeError(f"Module {entry_str} has no Plugin class or plugin object")

            # Try instantiating with manifest, then without
            try:
                inst = cls(dp.manifest)
            except TypeError:
                inst = cls()
            return inst
        except Exception as err:
            raise PluginLoadError(
                f"Failed to load entry '{entry_str}' for plugin '{dp.manifest.name}': {err}"
            ) from err

    async def load_all(self) -> None:
        """
        Loads all discovered plugins in topological order.
        A throwing plugin setup() does not abort boot of other plugins.
        """
        order = self.resolve_load_order()

        for dp in order:
            name = dp.manifest.name
            try:
                # Compute effective capabilities
                overrides = self.settings.get(f"kernel.plugins.capabilities.{name}")
                effective_caps = clamp_capabilities(
                    dp.manifest.capabilities,
                    dp.tier,
                    override=frozenset(Capability(c) for c in overrides) if overrides else None,
                )

                # Instantiate
                instance = self._instantiate_plugin(dp)

                # Build context
                plugin_data_dir = self.data_dir / "plugins" / name
                plugin_data_dir.mkdir(parents=True, exist_ok=True)
                settings_view = self.settings.get_view(f"plugins.{name}")
                secrets_view = self.secrets.get_view(effective_caps, plugin_name=name)

                ctx = PluginContextImpl(
                    name=name,
                    workspace=self.workspace,
                    data_dir=plugin_data_dir,
                    settings=settings_view,
                    secrets=secrets_view,
                    capabilities=effective_caps,
                    registry=self,
                )

                # Initialize
                await instance.setup(ctx)

                loaded = LoadedPlugin(
                    manifest=dp.manifest,
                    instance=instance,
                    tier=dp.tier,
                    effective_capabilities=effective_caps,
                    origin=dp.origin,
                    context=ctx,
                )
                self._loaded[name] = loaded

                # Emit plugin.loaded event
                if self.bus:
                    evt = Event(
                        type="plugin.loaded",
                        data={
                            "name": name,
                            "version": dp.manifest.version,
                            "provides": [p.value if hasattr(p, "value") else str(p) for p in dp.manifest.provides],
                            "capabilities": [c.value for c in effective_caps],
                            "trust": dp.tier.value,
                        },
                    )
                    self.bus.publish(evt)

            except Exception as err:
                logger.exception(f"Plugin '{name}' failed during setup: {err}")
                if self.bus:
                    evt = Event(
                        type="plugin.error",
                        data={"name": name, "error": str(err)},
                    )
                    self.bus.publish(evt)
                # Continue boot! Do not raise or abort.

    async def unload_all(self) -> None:
        """Shuts down all loaded plugins in reverse topological order."""
        for name in reversed(list(self._loaded.keys())):
            loaded = self._loaded[name]
            try:
                await loaded.instance.teardown()
            except Exception as err:
                logger.warning(f"Error during plugin '{name}' teardown: {err}")
        self._loaded.clear()
        self.tools.clear()
        self.providers.clear()
        self.policies.clear()
        self.executors.clear()
        self.context_sources.clear()
        self.stores.clear()
        self.strategies.clear()
        self.hooks.clear()

    def get_loaded(self, name: str) -> LoadedPlugin | None:
        return self._loaded.get(name)

    def is_loaded(self, name: str) -> bool:
        return name in self._loaded
