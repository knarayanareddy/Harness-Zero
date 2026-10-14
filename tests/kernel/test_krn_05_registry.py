"""
Tests for KRN-05: PluginRegistry discovery, capability clamping,
collision detection, dependency resolution, and boundary enforcement.
"""
from __future__ import annotations

from pathlib import Path
from typing import Any

import pytest

from harness.kernel.bus import EventBus
from harness.kernel.registry import (
    DiscoveredPlugin,
    PluginRegistry,
    TrustTier,
    clamp_capabilities,
)
from harness.kernel.secrets import Secrets
from harness.kernel.settings import SettingsStore
from harness.sdk.contracts import Plugin, Strategy
from harness.sdk.errors import (
    CapabilityDenied,
    IncompatiblePlugin,
    PluginLoadError,
)
from harness.sdk.manifest import PluginManifest
from harness.sdk.types import SDK_API_VERSION, Capability, RiskClass, ToolAnnotations, ToolSpec


class DummyPlugin(Plugin):
    def __init__(self, manifest: PluginManifest | None = None):
        self.manifest = manifest or PluginManifest(
            name="dummy-plugin",
            version="0.1.0",
            api_version=SDK_API_VERSION,
            summary="A dummy plugin",
            entry="dummy",
            provides=["tool"],
        )
        self.setup_called = False
        self.teardown_called = False
        self.ctx: Any = None

    async def setup(self, ctx: Any) -> None:
        self.setup_called = True
        self.ctx = ctx

    async def teardown(self) -> None:
        self.teardown_called = True


class FaultyPlugin(Plugin):
    async def setup(self, ctx: Any) -> None:
        raise RuntimeError("Setup failed catastrophically!")

    async def teardown(self) -> None:
        pass


def test_capability_clamping():
    """Validates capability clamping rules across trust tiers."""
    requested = frozenset([
        Capability.FS_READ,
        Capability.SECRETS,
        Capability.APPROVAL_BYPASS,
    ])

    # Builtin & Local clamp out APPROVAL_BYPASS
    clamped_builtin = clamp_capabilities(requested, TrustTier.BUILTIN)
    assert Capability.APPROVAL_BYPASS not in clamped_builtin
    assert Capability.FS_READ in clamped_builtin
    assert Capability.SECRETS in clamped_builtin

    # Third party clamps out both APPROVAL_BYPASS and SECRETS
    clamped_tp = clamp_capabilities(requested, TrustTier.THIRD_PARTY)
    assert Capability.APPROVAL_BYPASS not in clamped_tp
    assert Capability.SECRETS not in clamped_tp
    assert Capability.FS_READ in clamped_tp

    # User override restricts further
    override = frozenset([Capability.FS_READ])
    clamped_override = clamp_capabilities(requested, TrustTier.BUILTIN, override=override)
    assert clamped_override == frozenset([Capability.FS_READ])


def test_manifest_api_version_refused(tmp_path: Path):
    """Wrong api_version must be refused with clear message."""
    registry = PluginRegistry(
        workspace=tmp_path,
        settings=SettingsStore(tmp_path / "settings.json"),
        secrets=Secrets(tmp_path / "secrets.json"),
    )
    bad_manifest = PluginManifest(
        name="bad-plugin",
        version="0.1.0",
        api_version="999",  # Incompatible version
        summary="Bad version plugin",
        entry="tests.bad:Plugin",
    )
    dp = DiscoveredPlugin(manifest=bad_manifest, tier=TrustTier.LOCAL, origin="test")

    with pytest.raises(IncompatiblePlugin) as exc:
        registry.register_discovered(dp)
    assert "api_version '999'" in str(exc.value)
    assert f"current SDK version is '{SDK_API_VERSION}'" in str(exc.value)


def test_invalid_plugin_name_rejected(tmp_path: Path):
    """Invalid plugin name rejected."""
    registry = PluginRegistry(
        workspace=tmp_path,
        settings=SettingsStore(tmp_path / "settings.json"),
        secrets=Secrets(tmp_path / "secrets.json"),
    )
    bad_manifest = PluginManifest(
        name="Bad_Plugin_Uppercase",
        version="0.1.0",
        api_version=SDK_API_VERSION,
        summary="Invalid name",
        entry="tests.bad:Plugin",
    )
    dp = DiscoveredPlugin(manifest=bad_manifest, tier=TrustTier.LOCAL, origin="test")

    with pytest.raises(PluginLoadError) as exc:
        registry.register_discovered(dp)
    assert "Invalid plugin name" in str(exc.value)


def test_plugin_name_collision_reports_both_origins(tmp_path: Path):
    """Duplicate plugin names must fail with both origins named."""
    registry = PluginRegistry(
        workspace=tmp_path,
        settings=SettingsStore(tmp_path / "settings.json"),
        secrets=Secrets(tmp_path / "secrets.json"),
    )
    m1 = PluginManifest(
        name="my-plugin",
        version="0.1.0",
        api_version=SDK_API_VERSION,
        summary="Origin 1",
        entry="mod1:Plugin",
    )
    dp1 = DiscoveredPlugin(manifest=m1, tier=TrustTier.BUILTIN, origin="/path/to/origin1")
    registry.register_discovered(dp1)

    m2 = PluginManifest(
        name="my-plugin",
        version="0.2.0",
        api_version=SDK_API_VERSION,
        summary="Origin 2",
        entry="mod2:Plugin",
    )
    dp2 = DiscoveredPlugin(manifest=m2, tier=TrustTier.LOCAL, origin="/path/to/origin2")

    with pytest.raises(PluginLoadError) as exc:
        registry.register_discovered(dp2)

    msg = str(exc.value)
    assert "Plugin name collision for 'my-plugin'" in msg
    assert "/path/to/origin1" in msg
    assert "/path/to/origin2" in msg


def test_duplicate_tool_name_reports_both_origins(tmp_path: Path):
    """Duplicate tool names must fail with both plugin origins named."""
    registry = PluginRegistry(
        workspace=tmp_path,
        settings=SettingsStore(tmp_path / "settings.json"),
        secrets=Secrets(tmp_path / "secrets.json"),
    )
    spec1 = ToolSpec(
        name="read_file",
        description="Reads a file",
        input_schema={"type": "object"},
        risk_class=RiskClass.READ,
        annotations=ToolAnnotations(),
    )
    spec2 = ToolSpec(
        name="read_file",
        description="Duplicate reader",
        input_schema={"type": "object"},
        risk_class=RiskClass.READ,
        annotations=ToolAnnotations(),
    )

    async def dummy_handler(args, ctx):
        return None

    registry._register_tool(spec1, dummy_handler, "plugin-alpha")

    with pytest.raises(PluginLoadError) as exc:
        registry._register_tool(spec2, dummy_handler, "plugin-beta")

    msg = str(exc.value)
    assert "Duplicate tool name 'read_file'" in msg
    assert "plugin-alpha" in msg
    assert "plugin-beta" in msg


def test_duplicate_strategy_name_reports_both_origins(tmp_path: Path):
    """Duplicate strategy names must fail with both plugin origins named."""
    registry = PluginRegistry(
        workspace=tmp_path,
        settings=SettingsStore(tmp_path / "settings.json"),
        secrets=Secrets(tmp_path / "secrets.json"),
    )

    class DummyStrategy(Strategy):
        def __init__(self, name: str):
            self.name = name

        async def run_turn(self, ctx, services):
            return None

    s1 = DummyStrategy("single_loop")
    s2 = DummyStrategy("single_loop")

    registry._register_strategy(s1, "builtin-core")

    with pytest.raises(PluginLoadError) as exc:
        registry._register_strategy(s2, "custom-plugin")

    msg = str(exc.value)
    assert "Duplicate strategy name 'single_loop'" in msg
    assert "builtin-core" in msg
    assert "custom-plugin" in msg


def test_clamped_capability_raises_capability_denied(tmp_path: Path):
    """Registering a tool requiring capabilities not granted to plugin raises CapabilityDenied."""
    registry = PluginRegistry(
        workspace=tmp_path,
        settings=SettingsStore(tmp_path / "settings.json"),
        secrets=Secrets(tmp_path / "secrets.json"),
    )
    # Plugin only granted FS_READ
    from harness.kernel.registry import PluginContextImpl

    plugin_ctx = PluginContextImpl(
        name="read-only-plugin",
        workspace=tmp_path,
        data_dir=tmp_path / "data",
        settings=registry.settings.get_view("plugins.read-only-plugin"),
        secrets=registry.secrets.get_view(frozenset([Capability.FS_READ])),
        capabilities=frozenset([Capability.FS_READ]),
        registry=registry,
    )

    # Tool requests EXEC
    excessive_spec = ToolSpec(
        name="execute_command",
        description="Executes shell command",
        input_schema={"type": "object"},
        risk_class=RiskClass.EXEC,
        annotations=ToolAnnotations(),
        capabilities=frozenset([Capability.EXEC]),
    )

    async def handler(args, c):
        return None

    with pytest.raises(CapabilityDenied) as exc:
        plugin_ctx.register_tool(excessive_spec, handler)

    assert "Capability 'exec' denied" in str(exc.value)
    assert "read-only-plugin" in str(exc.value)


def test_dependency_topological_sort(tmp_path: Path):
    """Plugins must be ordered such that dependencies load before dependents."""
    registry = PluginRegistry(
        workspace=tmp_path,
        settings=SettingsStore(tmp_path / "settings.json"),
        secrets=Secrets(tmp_path / "secrets.json"),
    )

    # plugin-c requires plugin-b; plugin-b requires plugin-a
    m_a = PluginManifest(name="plugin-a", version="0.1.0", api_version=SDK_API_VERSION, summary="A", entry="a")
    m_b = PluginManifest(name="plugin-b", version="0.1.0", api_version=SDK_API_VERSION, summary="B", entry="b", requires=["plugin-a"])
    m_c = PluginManifest(name="plugin-c", version="0.1.0", api_version=SDK_API_VERSION, summary="C", entry="c", requires=["plugin-b"])

    registry.register_discovered(DiscoveredPlugin(m_c, TrustTier.LOCAL, "c"))
    registry.register_discovered(DiscoveredPlugin(m_a, TrustTier.LOCAL, "a"))
    registry.register_discovered(DiscoveredPlugin(m_b, TrustTier.LOCAL, "b"))

    order = registry.resolve_load_order()
    names = [p.manifest.name for p in order]
    assert names == ["plugin-a", "plugin-b", "plugin-c"]


def test_dependency_missing_raises_error(tmp_path: Path):
    """Missing dependency raises PluginLoadError."""
    registry = PluginRegistry(
        workspace=tmp_path,
        settings=SettingsStore(tmp_path / "settings.json"),
        secrets=Secrets(tmp_path / "secrets.json"),
    )
    m = PluginManifest(
        name="dependent-plugin",
        version="0.1.0",
        api_version=SDK_API_VERSION,
        summary="Dep",
        entry="d",
        requires=["missing-dependency"],
    )
    registry.register_discovered(DiscoveredPlugin(m, TrustTier.LOCAL, "d"))

    with pytest.raises(PluginLoadError) as exc:
        registry.resolve_load_order()
    assert "missing-dependency" in str(exc.value)


def test_dependency_cycle_raises_error(tmp_path: Path):
    """Dependency cycle raises PluginLoadError with cycle path."""
    registry = PluginRegistry(
        workspace=tmp_path,
        settings=SettingsStore(tmp_path / "settings.json"),
        secrets=Secrets(tmp_path / "secrets.json"),
    )
    m1 = PluginManifest(name="cycle-a", version="0.1.0", api_version=SDK_API_VERSION, summary="A", entry="a", requires=["cycle-b"])
    m2 = PluginManifest(name="cycle-b", version="0.1.0", api_version=SDK_API_VERSION, summary="B", entry="b", requires=["cycle-a"])

    registry.register_discovered(DiscoveredPlugin(m1, TrustTier.LOCAL, "a"))
    registry.register_discovered(DiscoveredPlugin(m2, TrustTier.LOCAL, "b"))

    with pytest.raises(PluginLoadError) as exc:
        registry.resolve_load_order()
    assert "Dependency cycle detected" in str(exc.value)


@pytest.mark.asyncio
async def test_throwing_setup_does_not_abort_boot(tmp_path: Path):
    """A throwing setup() must emit plugin.error and NOT abort boot of other plugins."""
    bus = EventBus()
    sub = bus.subscribe()

    registry = PluginRegistry(
        workspace=tmp_path,
        settings=SettingsStore(tmp_path / "settings.json"),
        secrets=Secrets(tmp_path / "secrets.json"),
        bus=bus,
    )

    m_faulty = PluginManifest(
        name="faulty-plugin",
        version="0.1.0",
        api_version=SDK_API_VERSION,
        summary="Faulty",
        entry="faulty",
    )
    m_healthy = PluginManifest(
        name="healthy-plugin",
        version="0.1.0",
        api_version=SDK_API_VERSION,
        summary="Healthy",
        entry="healthy",
    )

    p_faulty = FaultyPlugin()
    p_healthy = DummyPlugin(m_healthy)

    registry.register_discovered(DiscoveredPlugin(m_faulty, TrustTier.LOCAL, "f"))
    registry.register_discovered(DiscoveredPlugin(m_healthy, TrustTier.LOCAL, "h"))

    # Mock instantiation for these two
    def mock_instantiate(dp):
        if dp.manifest.name == "faulty-plugin":
            return p_faulty
        return p_healthy

    registry._instantiate_plugin = mock_instantiate

    await registry.load_all()

    # Faulty plugin was not loaded
    assert not registry.is_loaded("faulty-plugin")
    # Healthy plugin WAS loaded despite faulty plugin failing!
    assert registry.is_loaded("healthy-plugin")
    assert p_healthy.setup_called is True

    # Assert events emitted on bus
    events_received = []
    while not sub.empty():
        events_received.append(sub.get_nowait())

    err_events = [e for e in events_received if e.type == "plugin.error"]
    loaded_events = [e for e in events_received if e.type == "plugin.loaded"]

    assert len(err_events) == 1
    assert err_events[0].data["name"] == "faulty-plugin"
    assert "Setup failed catastrophically" in err_events[0].data["error"]

    assert len(loaded_events) == 1
    assert loaded_events[0].data["name"] == "healthy-plugin"

    # Teardown test
    await registry.unload_all()
    assert p_healthy.teardown_called is True
    assert not registry.is_loaded("healthy-plugin")
