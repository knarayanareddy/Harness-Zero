"""
Static contract test suite for the modular web frontend (Step 3).

Verifies:
1. All DOM element IDs referenced by app.js exist in static/index.html.
2. Zero occurrences of alert() across static/*.js and static/*.html.
3. Zero occurrences of /turn/stream across static/*.js and static/*.html.
4. Zero inline event handlers (onclick, onsubmit, onchange) in static/*.html and static/*.js.
5. Zero inline <script> tags without src in static/index.html.
6. The web server emits strict CSP with `script-src 'self'` without `'unsafe-inline'` or `'unsafe-eval'`.
"""

import re
from pathlib import Path

import httpx
import pytest

from harness.frontends.web.server import HarnessWebServer
from harness.kernel.approvals import ApprovalBroker
from harness.kernel.bus import EventBus
from harness.kernel.commands import Commands
from harness.kernel.gate import PolicyGate
from harness.kernel.jail import WorkspaceJail
from harness.kernel.registry import PluginRegistry
from harness.kernel.runner import TurnRunner
from harness.kernel.secrets import Secrets
from harness.kernel.session import SessionManager
from harness.kernel.settings import SettingsStore

STATIC_DIR = Path(__file__).parent.parent.parent / "harness" / "frontends" / "web" / "static"


@pytest.fixture
def workspace_dir(tmp_path: Path) -> Path:
    ws = tmp_path / "test_workspace"
    ws.mkdir(parents=True, exist_ok=True)
    return ws


@pytest.fixture
def settings(tmp_path: Path) -> SettingsStore:
    return SettingsStore(tmp_path / "settings.json")


@pytest.fixture
def secrets(tmp_path: Path) -> Secrets:
    return Secrets(tmp_path / "secrets.json")


@pytest.fixture
def event_bus() -> EventBus:
    return EventBus()


@pytest.fixture
def commands(
    workspace_dir: Path,
    settings: SettingsStore,
    secrets: Secrets,
    event_bus: EventBus,
) -> Commands:
    reg = PluginRegistry(workspace=workspace_dir, settings=settings, secrets=secrets, bus=event_bus)
    jail = WorkspaceJail(workspace_dir)
    approvals = ApprovalBroker(event_bus)
    gate = PolicyGate(
        registry=reg,
        jail=jail,
        settings=settings,
        secrets=secrets,
        approvals=approvals,
        bus=event_bus,
    )
    runner = TurnRunner(registry=reg, gate=gate, settings=settings, bus=event_bus)
    sm = SessionManager(workspace=workspace_dir, registry=reg, settings=settings, bus=event_bus)
    return Commands(
        workspace=workspace_dir,
        session_manager=sm,
        runner=runner,
        gate=gate,
        settings=settings,
        secrets=secrets,
        registry=reg,
        bus=event_bus,
    )


def test_element_ids_contract():
    """All DOM element IDs queried by app.js must exist in index.html."""
    app_js_path = STATIC_DIR / "app.js"
    index_html_path = STATIC_DIR / "index.html"

    assert app_js_path.exists(), f"Missing {app_js_path}"
    assert index_html_path.exists(), f"Missing {index_html_path}"

    app_content = app_js_path.read_text(encoding="utf-8")
    index_content = index_html_path.read_text(encoding="utf-8")

    # Extract all getElementById calls
    id_matches = re.findall(r"getElementById\(['\"]([^'\"]+)['\"]\)", app_content)
    assert len(id_matches) > 0, "No getElementById found in app.js"

    missing_ids = []
    for elem_id in set(id_matches):
        # Look for id="elem_id" or id='elem_id'
        if f'id="{elem_id}"' not in index_content and f"id='{elem_id}'" not in index_content:
            missing_ids.append(elem_id)

    assert not missing_ids, f"DOM element IDs referenced in app.js missing from index.html: {missing_ids}"


def test_no_alert_calls_in_frontend():
    """Frontend files must have zero alert() calls (use dynamic UI notices instead)."""
    frontend_files = list(STATIC_DIR.glob("*.js")) + list(STATIC_DIR.glob("*.html"))
    assert len(frontend_files) > 0

    violations = []
    for f in frontend_files:
        content = f.read_text(encoding="utf-8")
        if "alert(" in content:
            violations.append(f.name)

    assert not violations, f"Files containing alert() calls: {violations}"


def test_no_turn_stream_in_static_frontend():
    """The modular frontend must use /commands and /events, not the legacy /turn/stream."""
    frontend_files = list(STATIC_DIR.glob("*.js")) + list(STATIC_DIR.glob("*.html"))
    violations = []
    for f in frontend_files:
        content = f.read_text(encoding="utf-8")
        if "/turn/stream" in content:
            violations.append(f.name)

    assert not violations, f"Files referencing /turn/stream: {violations}"


def test_no_inline_scripts_or_handlers():
    """index.html must not contain inline scripts or inline event handlers."""
    index_path = STATIC_DIR / "index.html"
    content = index_path.read_text(encoding="utf-8")

    # Check for inline handlers
    handler_pattern = re.compile(r"\s(onclick|onsubmit|onchange|onkeydown|onkeyup)\s*=", re.IGNORECASE)
    assert not handler_pattern.search(content), "Found inline event handlers in index.html"

    # Check that any <script> tags have a src attribute
    script_pattern = re.compile(r"<script(?![^>]*\bsrc=)[^>]*>(.*?)</script>", re.IGNORECASE | re.DOTALL)
    inline_scripts = script_pattern.findall(content)
    # Filter empty tags if any
    inline_scripts = [s.strip() for s in inline_scripts if s.strip()]
    assert not inline_scripts, f"Found inline script blocks in index.html: {inline_scripts}"


@pytest.mark.asyncio
async def test_strict_csp_header(commands: Commands, event_bus: EventBus, workspace_dir: Path):
    """Web server must emit strict CSP without unsafe-inline or unsafe-eval in script-src."""
    server = HarnessWebServer(
        commands=commands,
        bus=event_bus,
        host="127.0.0.1",
        port=0,
        sandbox_port=0,
        workspace=workspace_dir,
    )
    await server.start()
    base_url = f"http://127.0.0.1:{server.port}"

    try:
        async with httpx.AsyncClient() as client:
            res = await client.get(f"{base_url}/")
            assert res.status_code == 200
            csp = res.headers.get("Content-Security-Policy", "")

            assert "script-src 'self'" in csp, f"Expected script-src 'self' in CSP: {csp}"
            # Ensure script-src does not have unsafe-inline or unsafe-eval
            script_src_part = [part for part in csp.split(";") if "script-src" in part]
            assert script_src_part, "Missing script-src directive in CSP"
            assert "'unsafe-inline'" not in script_src_part[0], f"unsafe-inline in script-src: {script_src_part[0]}"
            assert "'unsafe-eval'" not in script_src_part[0], f"unsafe-eval in script-src: {script_src_part[0]}"
    finally:
        await server.stop()
