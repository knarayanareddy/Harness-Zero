"""
Plugin registry for discovery, manifest validation, dependency resolution,
capability clamping, lifecycle management, and collision prevention.
Fulfills KRN-05 and enforces architectural trust tiers.
"""
from __future__ import annotations

import importlib
import importlib.metadata
import importlib.util
import json
import logging
import re
import sys
from collections.abc import Callable
from dataclasses import dataclass
from enum import Enum
from pathlib import Path
from typing import Any, Literal

from harness.kernel.bus import EventBus
from harness.kernel.plugin_paths import client_module_url, plugin_dir
from harness.kernel.sandbox import SandboxService
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
    SandboxBackend,
    Strategy,
    ToolHandler,
)
from harness.sdk.errors import (
    CapabilityDenied,
    IncompatiblePlugin,
    PluginLoadError,
)
from harness.sdk.events import Event, PluginError, PluginLoaded
from harness.sdk.manifest import PluginManifest, UiContribution
from harness.sdk.types import SDK_API_VERSION, Capability, HookEvent, ToolSpec

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


@dataclass(frozen=True)
class RegisteredPluginCommand:
    """A plugin-owned frontend command, kept with its dispatch handler and public schema."""

    name: str
    handler: Callable[..., Any]
    plugin: str
    args_schema: dict[str, Any] | None = None
    exposed: bool = True


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
        self._data_dir = data_dir
        self.settings = settings
        self.secrets = secrets
        self.capabilities = capabilities
        self.log = logging.getLogger(f"harness.plugins.{name}")
        self._registry = registry
        # This is a live registry-owned service: a sandbox backend plugin may be loaded after this context was
        # created, and policies must query the current registration rather than capture an early empty snapshot.
        self.sandbox = registry.sandbox

    @property
    def data_dir(self) -> Path:
        """Lazily creates and returns the plugin data directory on first use."""
        self._data_dir.mkdir(parents=True, exist_ok=True)
        return self._data_dir

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

    def unregister_tool(self, name: str) -> bool:
        """Removes a tool owned by this plugin so a live integration can publish changed definitions."""
        return self._registry._unregister_tool(name, self.name)

    def emit_event(
        self,
        payload: Any,
        *,
        session_id: str | None = None,
        turn_id: str | None = None,
    ) -> None:
        """Publishes a typed event produced by this plugin."""
        event_type = getattr(type(payload), "EVENT_TYPE", None)
        if not event_type:
            raise PluginLoadError(f"Plugin '{self.name}' can only emit registered EventPayload values")
        if self._registry.bus is not None:
            self._registry.bus.publish(
                Event(type=event_type, data=payload, session_id=session_id, turn_id=turn_id)
            )

    async def emit_record(
        self,
        payload: Any,
        *,
        content: str,
        metadata: dict[str, Any],
        session_id: str | None = None,
        turn_id: str | None = None,
    ) -> dict[str, Any] | None:
        """Append a plugin fact to the named session (or active session) before publishing it."""
        from harness.kernel.context import append_and_publish_record

        manager = getattr(self._registry, "session_manager", None)
        if session_id is not None and manager is not None:
            session = manager.get_session(session_id)
        else:
            session = getattr(manager, "active_session", None) if manager is not None else None
        effective_session_id = session_id or getattr(session, "session_id", None)
        return await append_and_publish_record(
            log=getattr(session, "log", None),
            bus=self._registry.bus,
            session_id=effective_session_id,
            turn_id=turn_id,
            payload=payload,
            content=content,
            metadata=metadata,
        )

    def register_service(self, name: str, service: object) -> None:
        """Registers a unique service consumed by kernel components by name."""
        if not re.fullmatch(r"[a-z][a-z0-9_-]{0,63}", name):
            raise PluginLoadError(f"Plugin '{self.name}' service name {name!r} is invalid")
        if service is None:
            raise PluginLoadError(f"Plugin '{self.name}' service '{name}' cannot be None")
        self._registry._register_service(name, service, self.name)

    def register_command(
        self,
        name: str,
        handler: Callable[..., Any],
        *,
        args_schema: dict[str, Any] | None = None,
        exposed: bool = True,
    ) -> None:
        """Registers a namespaced frontend command for this plugin."""
        if not re.fullmatch(r"[a-z][a-z0-9_-]{0,39}", name):
            raise PluginLoadError(
                f"Plugin '{self.name}' command name {name!r} must be a local identifier without dots or aliases"
            )
        if not callable(handler):
            raise PluginLoadError(f"Plugin '{self.name}' command '{name}' handler must be callable")
        self._registry._register_command(
            f"plugins.{self.name}.{name}",
            handler,
            self.name,
            args_schema=args_schema,
            exposed=exposed,
        )

    def register_provider(self, p: Provider) -> None:
        self._registry._register_provider(p, self.name)

    def register_policy(self, p: Policy) -> None:
        self._registry._register_policy(p, self.name)

    def register_executor(self, e: Executor) -> None:
        self._registry._register_executor(e, self.name)

    def register_sandbox_backend(self, backend: SandboxBackend) -> None:
        self._registry._register_sandbox_backend(backend, self.name)

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
        # Attached by bootstrap after the registry has loaded plugins. Plugin contexts remain live, so an
        # asynchronous integration can append a durable notice to the current session when its remote state changes.
        self.session_manager: Any | None = None
        # One live backend registry shared by policy evaluation, status reporting, and every executor context.
        self.sandbox = SandboxService(settings=settings, workspace=workspace)

        # Discovered and loaded plugins
        self._discovered: dict[str, DiscoveredPlugin] = {}
        self._loaded: dict[str, LoadedPlugin] = {}

        # Secrets namespaces contributed by plugin settings schemas (WS-02): a plugin declares
        # the secret keys it reads, so the settings UI never has to guess a list by hand.
        self._secret_props: dict[str, dict[str, Any]] = {}
        self.settings_namespaces: dict[str, str] = {"kernel": "kernel"}
        # Settings namespaces registered only so old spellings keep resolving; they are excluded
        # from the generated catalog and the settings UI so a key is never listed twice.
        self._alias_namespaces: set[str] = set()

        # UI contributions (WS-05): source name -> what it asks the client to mount. A panel, a record
        # renderer and a client module are only ever *names* resolved by the client, so nothing here runs.
        self._ui: dict[str, UiContribution] = {}

        # Component collections. Commands additionally retain their declared argument schema and owner so the
        # generic dispatcher and `commands.list` stay one source of truth.
        self.tools: dict[str, tuple[ToolSpec, ToolHandler, str]] = {}
        self.commands: dict[str, RegisteredPluginCommand] = {}
        self.providers: dict[str, tuple[Provider, str]] = {}
        self.policies: list[tuple[Policy, str]] = []
        self.executors: dict[str, tuple[Executor, str]] = {}
        self.services: dict[str, tuple[object, str]] = {}
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

    def _unregister_tool(self, name: str, plugin_name: str) -> bool:
        """Removes a registered tool only when the requesting plugin owns it."""
        entry = self.tools.get(name)
        if entry is None:
            return False
        if entry[2] != plugin_name:
            raise PluginLoadError(
                f"Plugin '{plugin_name}' cannot unregister tool '{name}' owned by '{entry[2]}'"
            )
        del self.tools[name]
        return True

    def _register_service(self, name: str, service: object, plugin_name: str) -> None:
        if name in self.services:
            _, existing_origin = self.services[name]
            raise PluginLoadError(
                f"Duplicate service name '{name}' registered by plugin '{plugin_name}' "
                f"and already registered by plugin '{existing_origin}'"
            )
        self.services[name] = (service, plugin_name)

    def get_service(self, name: str) -> object | None:
        """Returns the service registered under ``name``, or ``None`` when it is absent."""
        entry = self.services.get(name)
        return entry[0] if entry is not None else None

    def _register_command(
        self,
        name: str,
        handler: Callable[..., Any],
        plugin_name: str,
        *,
        args_schema: dict[str, Any] | None = None,
        exposed: bool = True,
    ) -> None:
        if not name.startswith(f"plugins.{plugin_name}."):
            raise PluginLoadError(
                f"Plugin '{plugin_name}' commands must be namespaced as 'plugins.{plugin_name}.<name>'"
            )
        if name in self.commands:
            existing = self.commands[name]
            raise PluginLoadError(
                f"Duplicate command '{name}' registered by plugin '{plugin_name}' "
                f"and already registered by plugin '{existing.plugin}'"
            )
        if name in {"plugins", "plugins.list"}:
            raise PluginLoadError(f"Plugin command name {name!r} is reserved")
        if args_schema is not None:
            if not isinstance(args_schema, dict) or args_schema.get("type", "object") != "object":
                raise PluginLoadError(f"Plugin command '{name}' args_schema must be a JSON Schema object")
            if args_schema.get("additionalProperties") is not False:
                raise PluginLoadError(
                    f"Plugin command '{name}' args_schema must set additionalProperties=false"
                )
        self.commands[name] = RegisteredPluginCommand(
            name=name,
            handler=handler,
            plugin=plugin_name,
            args_schema=args_schema,
            exposed=bool(exposed),
        )

    #: Members every provider must have; checked at registration so a contract gap fails the plugin load
    #: loudly instead of surfacing as a missing picker entry or an AttributeError mid-turn.
    _PROVIDER_MEMBERS: tuple[str, ...] = ("name", "caps", "is_configured", "is_reachable", "list_models", "generate", "aclose")

    def _register_provider(self, p: Provider, plugin_name: str) -> None:
        missing = [m for m in self._PROVIDER_MEMBERS if not hasattr(p, m)]
        if missing:
            raise PluginLoadError(
                f"Provider registered by plugin '{plugin_name}' does not implement the Provider contract "
                f"(missing: {', '.join(missing)}); see harness.sdk.contracts.Provider"
            )
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

    def _register_sandbox_backend(self, backend: SandboxBackend, plugin_name: str) -> None:
        self.sandbox.register_backend(backend, plugin_name)

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

    def register_tool(self, spec: ToolSpec, handler: Any, plugin_name: str = "builtin") -> None:
        self._register_tool(spec, handler, plugin_name)

    def register_provider(self, p: Provider, plugin_name: str = "builtin") -> None:
        self._register_provider(p, plugin_name)

    def register_policy(self, p: Policy, plugin_name: str = "builtin") -> None:
        self._register_policy(p, plugin_name)

    def register_executor(self, e: Executor, plugin_name: str = "builtin") -> None:
        self._register_executor(e, plugin_name)

    def register_sandbox_backend(self, backend: SandboxBackend, plugin_name: str = "builtin") -> None:
        self._register_sandbox_backend(backend, plugin_name)

    def register_strategy(self, s: Strategy, plugin_name: str = "builtin") -> None:
        self._register_strategy(s, plugin_name)

    def register_hook(self, h: Hook, plugin_name: str = "builtin") -> None:
        self._register_hook(h, plugin_name)

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
        """Discovers plugins in directory or subdirectories containing harness_plugin.toml."""
        found: list[DiscoveredPlugin] = []
        if not root.exists() or not root.is_dir():
            return found

        for manifest_file in sorted(root.rglob("harness_plugin.toml")):
            try:
                # The tier is what decides whether a `[ui].client_module` may run in the browser, so it has to be
                # *this* directory's tier: `load`'s default is `builtin`, which made the check unreachable for
                # every plugin discovered from disk (R58).
                manifest = PluginManifest.load(manifest_file, trust=tier.value)
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

    @staticmethod
    def _import_entry(dp: DiscoveredPlugin, mod_name: str):
        """Import the module ``entry`` names, falling back to the plugin's own directory.

        A built-in's entry is an ordinary dotted path inside the ``harness`` package, and that is the case
        ``import_module`` serves. A plugin *installed* by a user — under ``<config>/plugins/<name>`` or the
        workspace's ``.harness/plugins`` — has no importable package name: ``plugin:MyPlugin`` is exactly the
        shape the SDK scaffold writes (`harness/sdk/scaffold.py`), and ``import_module("plugin")`` raised
        ``ModuleNotFoundError: No module named 'plugin'``, which ``load_all`` turned into a ``plugin.error``
        event. The plugin was discovered, declared its settings and panels, and then never loaded: silently,
        because no test had ever installed one (R60).

        So a name that does not resolve is tried as a path inside the plugin directory, loaded under a name
        nothing else can collide with. Nothing is made more trustworthy by this — what may *run* is still
        decided by the tier and the capability clamp, and `[ui].client_module` by `CLIENT_MODULE_TRUST`.
        """
        try:
            return importlib.import_module(mod_name)
        except ModuleNotFoundError as exc:
            relative = Path(*[part for part in mod_name.split(".") if part not in ("", ".")])
            root = plugin_dir(dp)
            for candidate in (root / f"{relative}.py", root / relative / "__init__.py"):
                if candidate.is_file():
                    unique = f"harness_plugin__{dp.manifest.name}__{mod_name}".replace("-", "_")
                    spec = importlib.util.spec_from_file_location(unique, candidate)
                    if spec is None or spec.loader is None:
                        break
                    module = importlib.util.module_from_spec(spec)
                    sys.modules[unique] = module  # the module's own `@dataclass`/self-references resolve via this
                    spec.loader.exec_module(module)
                    return module
            raise exc

    def _instantiate_plugin(self, dp: DiscoveredPlugin) -> Plugin:
        """Instantiates the Plugin contract class referenced in entry."""
        entry_str = dp.manifest.entry
        if not entry_str:
            raise PluginLoadError(f"Plugin '{dp.manifest.name}' declares empty entry.")

        try:
            if ":" in entry_str:
                mod_name, cls_name = entry_str.split(":", 1)
                mod = self._import_entry(dp, mod_name)
                cls = getattr(mod, cls_name)
            else:
                mod = self._import_entry(dp, entry_str)
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

                # Declare settings/secret surface *before* setup(), so ctx.settings.get() during
                # setup resolves the schema default instead of silently returning None.
                self._register_settings_schema(dp)
                self._register_ui_contribution(dp)

                # Instantiate
                instance = self._instantiate_plugin(dp)

                # Build context
                plugin_data_dir = self.data_dir / "plugins" / name
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
                        data=PluginLoaded(
                            name=name,
                            version=dp.manifest.version,
                            provides=[p.value if hasattr(p, "value") else str(p) for p in dp.manifest.provides],
                            capabilities=[
                                c.value if hasattr(c, "value") else str(c)
                                for c in effective_caps
                            ],
                            trust=dp.tier.value,
                        ),
                    )
                    self.bus.publish(evt)

            except Exception as err:
                logger.exception(f"Plugin '{name}' failed during setup: {err}")
                if self.bus:
                    evt = Event(
                        type="plugin.error",
                        data=PluginError(name=name, error=str(err)),
                    )
                    self.bus.publish(evt)
                # Continue boot! Do not raise or abort.

        self._register_secrets_schema()

    def _register_settings_schema(self, dp: DiscoveredPlugin) -> None:
        """Registers a plugin's ``settings.schema.json`` with the settings store (WS-02).

        The file is the single declaration of what a plugin reads: its own namespace
        (``plugins.<name>`` unless ``x-namespace`` says otherwise), optional compatibility
        namespaces (``x-alias-namespaces``) and the secret keys it asks for (``x-secrets``).
        ``Commands.settings_schema`` and ``harness catalog`` aggregate exactly these, so a
        setting cannot exist in the UI without a reader and vice versa.
        """
        rel = dp.manifest.settings_schema
        if not rel:
            return
        base = dp.manifest.dir_path or (Path(dp.origin).parent if dp.origin else None)
        if base is None:
            raise PluginLoadError(f"Plugin '{dp.manifest.name}' declares settings_schema without a plugin directory")
        path = Path(base) / rel
        if not path.is_file():
            raise PluginLoadError(f"Plugin '{dp.manifest.name}' declares settings_schema '{rel}' which does not exist")
        try:
            doc = json.loads(path.read_text(encoding="utf-8"))
        except json.JSONDecodeError as err:
            raise PluginLoadError(f"Plugin '{dp.manifest.name}' settings_schema is not valid JSON: {err}") from err
        if not isinstance(doc, dict) or not isinstance(doc.get("properties"), dict):
            raise PluginLoadError(
                f"Plugin '{dp.manifest.name}' settings_schema must be an object with a 'properties' map"
            )

        props = doc["properties"]
        name = dp.manifest.name
        namespace = doc.get("x-namespace") or f"plugins.{name}"
        self.settings.register_schema(namespace, {"type": "object", "additionalProperties": False, "properties": props})

        self.settings_namespaces[namespace] = dp.manifest.name
        for alias, mapping in (doc.get("x-alias-namespaces") or {}).items():
            # ``{"<alias key>": "<property name>"}``: a compatibility spelling may rename the leaf
            # (legacy ``extensions.mcp_servers`` for ``servers``). Aliases stay open — they exist so
            # old files keep working, not to police writes — and never show in the UI a second time.
            self.settings.register_schema(
                alias,
                {
                    "type": "object",
                    "additionalProperties": True,
                    "properties": {
                        alias_key: props[src] for alias_key, src in mapping.items() if src in props
                    },
                },
            )
            self._alias_namespaces.add(alias)
            self.settings_namespaces.setdefault(alias, dp.manifest.name)

        for key, meta in (doc.get("x-secrets") or {}).items():
            prop = {"type": "string", "x-ui": {"group": "Secrets", "widget": "secret"}}
            prop.update(meta if isinstance(meta, dict) else {"title": str(meta)})
            self._secret_props[key] = prop

        # A plugin that declares its keys at a root namespace (providers.anthropic) is still
        # readable through its own view prefix, so mirror the types there as an alias.
        if namespace != f"plugins.{name}":
            self.settings.register_schema(
                f"plugins.{name}",
                {"type": "object", "additionalProperties": True, "properties": dict(props)},
            )
            self._alias_namespaces.add(f"plugins.{name}")
            self.settings_namespaces.setdefault(f"plugins.{name}", name)

    def _register_ui_contribution(self, dp: DiscoveredPlugin) -> None:
        """Records a plugin's `[ui]` manifest block for `ui_contributions()` below."""
        ui = dp.manifest.ui
        if ui is not None:
            self.register_ui_contribution(dp.manifest.name, ui)

    def register_ui_contribution(self, source: str, ui: UiContribution) -> None:
        """Adds one feature's UI contribution. `source` is a plugin name, or `web` for the shipped client."""
        if source in self._ui:
            raise PluginLoadError(f"UI contribution registered twice for {source!r}")
        self._ui[source] = ui

    def _load_builtin_ui(self) -> None:
        """The shipped client's own panels live in `harness/frontends/web/ui.toml`, in the same shape.

        Read as data rather than imported, because the kernel must not depend on the frontend package to
        describe it — and a distribution without the web frontend simply has no built-in panels, which is a
        valid answer, not an error.
        """
        if "web" in self._ui:
            return
        path = Path(__file__).resolve().parents[1] / "frontends" / "web" / "ui.toml"
        if not path.is_file():
            return
        import tomllib

        try:
            doc = tomllib.loads(path.read_text(encoding="utf-8"))
        except Exception as e:  # noqa: BLE001 - a malformed manifest is a load error, whatever it is
            raise PluginLoadError(f"Failed to parse {path}: {e}") from e
        ui = doc.get("ui")
        if isinstance(ui, dict):
            self.register_ui_contribution("web", UiContribution.from_dict(ui, plugin="web"))

    def ui_contributions(self) -> dict[str, Any]:
        """Everything the client should mount, attributed to whoever declared it.

        Only *loaded* plugins contribute: disabling a feature unloads it, so its panel and its renderers
        disappear from this response without a second enable/disable check anywhere — which is the whole point
        of a feature owning its UI instead of the client knowing about it.

        ``{"panels": [{id,title,command,renderer,plugin}], "record_renderers": [{kind,plugin}],
        "client_modules": [{plugin,path,url}]}``
        """
        self._load_builtin_ui()
        panels: list[dict[str, Any]] = []
        renderers: list[dict[str, str]] = []
        modules: list[dict[str, str]] = []
        for source in sorted(self._ui):
            ui = self._ui[source]
            for panel in ui.panels:
                panels.append({**panel.to_dict(), "plugin": source})
            for kind in ui.record_renderers:
                renderers.append({"kind": kind, "plugin": source})
            if ui.client_module:
                # One derivation of the address, shared with the route that serves it
                # (`harness/kernel/plugin_paths.py:client_module_url`): the URL used to be `"/" + path`, which
                # collided with the client's own `/static/` tree and 404'd — silently, because a module that
                # fails to import only means the panel loses its customisation.
                modules.append(
                    {
                        "plugin": source,
                        "path": ui.client_module,
                        "url": client_module_url(source, ui.client_module),
                    }
                )
        seen: set[str] = set()
        dupes: set[str] = set()
        for panel in panels:
            if panel["id"] in seen:
                dupes.add(panel["id"])
            seen.add(panel["id"])
        claimed = sorted(dupes)
        if claimed:
            raise PluginLoadError(
                f"Two features claim the same panel id: {', '.join(claimed)} — a panel id is the client's key"
            )
        panels.sort(key=lambda p: p["id"])
        renderers.sort(key=lambda r: (r["kind"], r["plugin"]))
        modules.sort(key=lambda m: m["plugin"])
        return {"panels": panels, "record_renderers": renderers, "client_modules": modules}

    @property
    def alias_namespaces(self) -> frozenset[str]:
        """Settings namespaces kept only for compatibility (never rendered twice)."""
        return frozenset(self._alias_namespaces)

    def settings_owner(self, namespace: str) -> str | None:
        """Which plugin registered ``namespace`` (the owner column of the generated catalog)."""
        return self.settings_namespaces.get(namespace)

    def _register_secrets_schema(self) -> None:
        """Merges every plugin's declared secret keys into the ``secrets`` UI namespace."""
        self.settings_namespaces["secrets"] = "kernel"
        existing = self.settings.get_registered_schemas().get("secrets", {})
        props = dict(existing.get("properties") or {})
        props.update(self._secret_props)
        self.settings.register_schema(
            "secrets",
            {"type": "object", "additionalProperties": True, "properties": props},
        )

    def strategy_names(self) -> list[str]:
        """Names of the registered strategies — the enum the UI offers for ``kernel.strategy``."""
        return sorted(self.strategies)

    async def unload_all(self) -> None:
        """Shuts down all loaded plugins in reverse topological order."""
        for name in reversed(list(self._loaded.keys())):
            loaded = self._loaded[name]
            try:
                await loaded.instance.teardown()
            except Exception as err:
                logger.warning(f"Error during plugin '{name}' teardown: {err}")
        self._loaded.clear()
        self.sandbox.clear_backends()
        self.tools.clear()
        self.commands.clear()
        self.providers.clear()
        self.policies.clear()
        self.executors.clear()
        self.services.clear()
        self.context_sources.clear()
        self.stores.clear()
        self.strategies.clear()
        self.hooks.clear()
        # Contributions are collected while loading, so unloading drops them — which is what makes
        # "disable a feature, its panel and renderers go with it" true without any client-side check (WS-05).
        self._ui.clear()

    @property
    def discovered(self) -> dict[str, DiscoveredPlugin]:
        return dict(self._discovered)

    def get_loaded(self, name: str) -> LoadedPlugin | None:
        return self._loaded.get(name)

    def is_loaded(self, name: str) -> bool:
        return name in self._loaded

    def get_provider(self, name: str) -> Provider | None:
        """Returns Provider by name if registered."""
        if name in self.providers:
            return self.providers[name][0]
        return None

    def get_all_providers(self) -> list[Provider]:
        """Returns list of all registered Providers."""
        return [p for p, _ in self.providers.values()]

    def get_tool_spec(self, name: str) -> ToolSpec | None:
        """Returns ToolSpec for registered tool by name."""
        if name in self.tools:
            return self.tools[name][0]
        return None

    def get_all_tool_specs(self) -> list[ToolSpec]:
        """Returns list of all registered ToolSpecs."""
        return [entry[0] for entry in self.tools.values()]

    def get_strategy(self, name: str) -> Strategy | None:
        """Returns Strategy by name if registered."""
        if name in self.strategies:
            return self.strategies[name][0]
        return None

    def get_hooks_for_event(self, event: HookEvent) -> list[Hook]:
        """Returns registered Hooks subscribed to the given HookEvent."""
        return [h for h, _ in self.hooks if event in getattr(h, "events", set())]

