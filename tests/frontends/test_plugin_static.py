"""WS-05c — the web route that serves a plugin's own client module (and everything it must refuse).

`ui.contributions` hands the browser a URL per `[ui].client_module`; this file fetches *that* URL rather than a
copy of it, because the failure mode in phase 05b was precisely that the two were built independently: the URL was
`"/" + path`, which landed in the client's own `/static/` tree, 404'd, and left the plugin's panel on the generic
renderer with nothing but a console line to show for it.

The refusals are the other half of the contract: an undeclared sibling, a traversal, a plugin that is installed but
switched off, and a plugin whose tier may not run browser code all answer 404 — the same answer, so nothing here
can be used to find out what a machine has installed.
"""
from __future__ import annotations

from pathlib import Path

import httpx
import pytest

from harness.frontends.web.server import HarnessWebServer
from harness.kernel.approvals import ApprovalBroker
from harness.kernel.bus import EventBus
from harness.kernel.commands import Commands
from harness.kernel.gate import PolicyGate
from harness.kernel.jail import WorkspaceJail
from harness.kernel.registry import PluginRegistry, TrustTier
from harness.kernel.runner import TurnRunner
from harness.kernel.secrets import Secrets
from harness.kernel.session import SessionManager
from harness.kernel.settings import SettingsStore
from harness.sdk.manifest import UiContribution
from harness.sdk.plugin_static import CLIENT_MODULE_ROOT

MANIFEST = """\
[plugin]
name = "{name}"
version = "1.0.0"
api_version = "1"
summary = "A vault, as a panel"
provides = ["tool"]
entry = "plugin:{cls}Plugin"

[ui]
panels = [{{ id = "{panel}", title = "Vault", command = "sessions.list", renderer = "table" }}]
client_module = "static/probe.js"
"""

MODULE_BODY = "export const panels = { probe: { async render() {} } };\n"
PLUGIN_CLASS = """\
class {cls}Plugin:
    async def setup(self, ctx):
        self.ctx = ctx

    async def teardown(self):
        pass
"""


def write_plugin(root: Path, *, name: str = "obsidian", cls: str = "Obsidian", panel: str = "probe") -> Path:
    """A plugin directory in the shape discovery expects: `<root>/<family>/<name>/harness_plugin.toml`."""
    plugin = root / "tools" / name
    (plugin / "static").mkdir(parents=True, exist_ok=True)
    (plugin / "harness_plugin.toml").write_text(MANIFEST.format(name=name, cls=cls, panel=panel), encoding="utf-8")
    (plugin / "plugin.py").write_text(PLUGIN_CLASS.format(cls=cls), encoding="utf-8")
    (plugin / "static" / "probe.js").write_text(MODULE_BODY, encoding="utf-8")
    (plugin / "static" / "undeclared.js").write_text("export const leaked = 1;\n", encoding="utf-8")
    return plugin


def build(tmp_path: Path, *, tier: TrustTier = TrustTier.LOCAL, disabled: list[str] | None = None):
    settings = SettingsStore(tmp_path / "settings.json")
    if disabled:
        settings.set("kernel.plugins.disabled", disabled)
    secrets = Secrets(tmp_path / "secrets")
    bus = EventBus()
    registry = PluginRegistry(
        workspace=tmp_path,
        settings=settings,
        secrets=secrets,
        bus=bus,
        data_dir=tmp_path / "data",
    )
    registry.discover_directory(tmp_path / "plugins", tier)
    gate = PolicyGate(
        registry=registry,
        jail=WorkspaceJail(tmp_path),
        settings=settings,
        secrets=secrets,
        approvals=ApprovalBroker(bus),
        bus=bus,
    )
    commands = Commands(
        workspace=tmp_path,
        session_manager=SessionManager(workspace=tmp_path, registry=registry, settings=settings, bus=bus),
        runner=TurnRunner(registry=registry, gate=gate, settings=settings, bus=bus),
        gate=gate,
        settings=settings,
        secrets=secrets,
        registry=registry,
        bus=bus,
    )
    return registry, commands, bus


async def serve(tmp_path: Path, *, tier: TrustTier = TrustTier.LOCAL, disabled: list[str] | None = None):
    registry, commands, bus = build(tmp_path, tier=tier, disabled=disabled)
    await registry.load_all()
    server = HarnessWebServer(
        commands=commands, bus=bus, host="127.0.0.1", port=0, sandbox_port=0, workspace=tmp_path
    )
    await server.start()
    return registry, commands, server


@pytest.fixture
async def served(tmp_path: Path):
    """A running server in front of a kernel that loaded one local plugin with a client module."""
    write_plugin(tmp_path / "plugins")
    registry, commands, server = await serve(tmp_path)
    try:
        yield f"http://127.0.0.1:{server.port}", commands
    finally:
        await registry.unload_all()
        await server.stop()


# ---------------------------------------------------------------- the flow the browser takes


async def test_the_url_the_kernel_advertises_is_the_url_that_serves_the_bytes(served):
    base, commands = served
    modules = (await commands.ui_contributions())["client_modules"]
    mine = [m for m in modules if m["plugin"] == "obsidian"]
    assert mine, f"the loaded plugin's module is missing from `ui.contributions`: {modules}"
    url = mine[0]["url"]
    assert url == f"{CLIENT_MODULE_ROOT}/obsidian/probe.js"
    async with httpx.AsyncClient() as client:
        res = await client.get(f"{base}{url}")
    assert res.status_code == 200, res.text
    assert res.text == MODULE_BODY
    assert res.headers["content-type"].startswith("text/javascript")


async def test_a_plugin_panel_survives_alongside_the_client_s_own(served):
    """The declaration is what makes the tab; the module only customises it — so both must be reported."""
    _base, commands = served
    ui = await commands.ui_contributions()
    assert "probe" in [panel["id"] for panel in ui["panels"]], ui["panels"]
    assert "artifact" in [panel["id"] for panel in ui["panels"]], "the built-in client's panels vanished"
    assert ui["unbacked_panels"] == [], "a panel backed by `sessions.list` must be served"


# ---------------------------------------------------------------- what must not be readable


async def test_an_undeclared_file_in_the_same_directory_is_not_a_module(served):
    base, _commands = served
    async with httpx.AsyncClient() as client:
        res = await client.get(f"{base}{CLIENT_MODULE_ROOT}/obsidian/undeclared.js")
    assert res.status_code == 404, res.text
    assert "leaked" not in res.text


@pytest.mark.parametrize(
    "tail",
    [
        # Encoded, because httpx normalises `..` out of a URL before it is sent — and the request the route has
        # to refuse is the one a hostile page makes, not the one an address bar shows.
        "..%2Fsettings.json",
        "%2e%2e%2f%2e%2e%2fetc%2fpasswd",
        "obsidian%2F..%2Fobsidian%2Fprobe.js",
        "probe.js%00.css",  # a NUL in a path is a class of bug all of its own
    ],
)
async def test_traversal_and_lookalikes_are_404_not_500(served, tail: str):
    """A refusal that raises is a stack trace for the browser and an answer an attacker can probe with."""
    base, _commands = served
    async with httpx.AsyncClient() as client:
        res = await client.get(f"{base}{CLIENT_MODULE_ROOT}/obsidian/{tail}")
    assert res.status_code == 404, (tail, res.status_code, res.text[:160])


async def test_a_plugin_that_did_not_load_serves_no_code_its_files_are_still_on_disk(tmp_path: Path):
    """`plugins.disable` and a restart is exactly this state: declared, present, refused.

    The UI contribution is registered *before* `setup()` runs, so a plugin that fails to start still advertises
    its module; the route is where the session-level answer is enforced.
    """
    write_plugin(tmp_path / "plugins")
    registry, _commands, server = await serve(tmp_path, disabled=["obsidian"])
    try:
        assert not registry.is_loaded("obsidian"), "the fixture stopped testing what it claims"
        async with httpx.AsyncClient() as client:
            res = await client.get(f"http://127.0.0.1:{server.port}{CLIENT_MODULE_ROOT}/obsidian/probe.js")
        assert res.status_code == 404, res.text
    finally:
        await registry.unload_all()
        await server.stop()


async def test_an_untrusted_tier_is_refused_at_the_route_not_only_at_parse(tmp_path: Path):
    """Defence in depth: `from_dict` has no tier, so a registry assembled another way is still checked here.

    `PluginManifest.load` drops a workspace plugin's `client_module` declaration (pinned in
    `tests/kernel/test_plugin_static_paths.py`); re-attaching one by hand is how this test reaches the state that
    a different loader would leave, and the route must still refuse it.
    """
    plugin = write_plugin(tmp_path / "plugins")
    registry, commands, server = await serve(tmp_path, tier=TrustTier.WORKSPACE)
    try:
        assert registry.is_loaded("obsidian"), "the fixture plugin must load for the tier check to be the cause"
        assert registry.discovered["obsidian"].manifest.ui.client_module is None
        (plugin / "static" / "probe.js").write_text(MODULE_BODY, encoding="utf-8")
        registry.discovered["obsidian"].manifest.ui = UiContribution(panels=[], client_module="static/probe.js")
        async with httpx.AsyncClient() as client:
            res = await client.get(f"http://127.0.0.1:{server.port}{CLIENT_MODULE_ROOT}/obsidian/probe.js")
        assert res.status_code == 404, res.text
    finally:
        await registry.unload_all()
        await server.stop()


# ---------------------------------------------------------------- the mount order it depends on


async def test_the_client_s_own_static_tree_still_serves_beside_the_new_route(served):
    """`/static/` is a *prefix* resource, so registering the plugin route first is what keeps both alive.

    Swapping those two registrations is a two-line diff that breaks every module import in the studio; this makes
    it a failing test instead of a blank page.
    """
    base, _commands = served
    async with httpx.AsyncClient() as client:
        app_js = await client.get(f"{base}/static/app.js")
        pane = await client.get(f"{base}/static/panels/plugins.js")
    assert app_js.status_code == 200 and "HarnessStudioApp" in app_js.text
    assert pane.status_code == 200 and "plugins.list" in pane.text
