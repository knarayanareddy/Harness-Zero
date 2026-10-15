"""
Integration tests verifying security hardening for settings, secrets, and auth (§4.3, §9 Step 4).

Contracts verified:
1. POST /settings with canary secrets never returns secrets in GET /settings.
2. Workspace settings.json is enforced mode 0600 and never contains api_keys.
3. Canary secrets are routed to Secrets store (~/.harness/secrets.json) with mode 0600.
4. Security settings (permission_mode, session_spend_cap_usd, destructive_command_blocking)
   propagate from POST /settings into kernel SettingsStore.
5. SSE connection with ?token= in query string is strictly rejected (401).
6. Single-use ticket via POST /auth/ticket allows SSE connection and burns on use.
7. HttpOnly harness_token cookie allows SSE connection.
8. Pre-existing workspace api_keys are purged and migrated on server startup.
9. POST /sessions/switch validates session ID against directory traversal.
"""
from __future__ import annotations

import json
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


@pytest.fixture
def workspace_dir(tmp_path: Path) -> Path:
    ws = tmp_path / "test_workspace"
    ws.mkdir(parents=True, exist_ok=True)
    return ws


@pytest.fixture
def settings(tmp_path: Path) -> SettingsStore:
    return SettingsStore(tmp_path / "kernel_settings.json")


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


@pytest.fixture
async def running_server(
    commands: Commands, event_bus: EventBus, workspace_dir: Path
):
    server = HarnessWebServer(
        commands=commands,
        bus=event_bus,
        host="127.0.0.1",
        port=0,
        sandbox_port=0,
        workspace=workspace_dir,
        auth_token="test_master_token_sec_12345",
    )
    await server.start()
    try:
        yield server
    finally:
        await server.stop()


@pytest.mark.asyncio
async def test_canary_secret_never_leaked_and_stored_securely(
    running_server: HarnessWebServer, workspace_dir: Path
):
    """
    Canary secret in POST /settings is:
    - NOT echoed back in POST response
    - NOT returned by GET /settings (has_api_keys is true instead)
    - NOT stored in <workspace>/.harness/settings.json (file is mode 0600)
    - Persisted in kernel Secrets store with mode 0600
    """
    server = running_server
    base_url = f"http://127.0.0.1:{server.port}"
    canary_key = "sk-CANARY-SECRET-999999"

    async with httpx.AsyncClient(timeout=5.0) as client:
        # 1. POST /settings with canary api key
        headers = {"X-Harness-Token": server.auth_token, "Content-Type": "application/json"}
        post_res = await client.post(
            f"{base_url}/settings",
            headers=headers,
            json={"api_keys": {"openai": canary_key}},
        )
        assert post_res.status_code == 200
        post_data = post_res.json()
        assert canary_key not in post_res.text
        assert post_data.get("configured_providers", {}).get("openai") is True

        # 2. GET /settings -> never returns secret
        get_res = await client.get(f"{base_url}/settings", headers=headers)
        assert get_res.status_code == 200
        get_text = get_res.text
        assert canary_key not in get_text
        get_data = get_res.json()
        assert "api_keys" not in get_data
        assert get_data.get("configured_providers", {}).get("openai") is True
        assert get_data.get("has_api_keys", {}).get("openai") is True

        # 3. Check workspace file on disk
        ws_settings_file = workspace_dir / ".harness" / "settings.json"
        assert ws_settings_file.exists()
        # Verify mode 0600 (read/write only by owner)
        file_mode = ws_settings_file.stat().st_mode & 0o777
        assert file_mode == 0o600, f"Expected 0600, got {oct(file_mode)}"

        file_content = ws_settings_file.read_text(encoding="utf-8")
        assert canary_key not in file_content
        disk_data = json.loads(file_content)
        assert "api_keys" not in disk_data

        # 4. Check kernel Secrets store
        assert server.commands.secrets.get("openai_api_key") == canary_key
        assert server.commands.secrets.get("OPENAI_API_KEY") == canary_key
        secrets_file = server.commands.secrets.path
        if secrets_file.exists():
            secrets_mode = secrets_file.stat().st_mode & 0o777
            assert secrets_mode == 0o600


@pytest.mark.asyncio
async def test_security_settings_propagate_to_kernel(running_server: HarnessWebServer):
    """
    default_permission_mode, session_spend_cap_usd, destructive_command_blocking,
    default_provider, and default_model propagate into kernel SettingsStore.
    """
    server = running_server
    base_url = f"http://127.0.0.1:{server.port}"
    headers = {"X-Harness-Token": server.auth_token, "Content-Type": "application/json"}

    payload = {
        "security": {
            "default_permission_mode": "ask",
            "session_spend_cap_usd": 15.75,
            "destructive_command_blocking": True,
        },
        "default_provider": "gemini",
        "default_model": "gemini-2.5-pro",
    }

    async with httpx.AsyncClient(timeout=5.0) as client:
        res = await client.post(f"{base_url}/settings", headers=headers, json=payload)
        assert res.status_code == 200

        # Verify kernel SettingsStore was updated
        assert server.commands.settings.get("kernel.security.permission_mode") == "ask"
        assert server.commands.settings.get("kernel.security.spend_cap_usd") == 15.75
        assert server.commands.settings.get("kernel.security.destructive_blocking") is True
        assert server.commands.settings.get("kernel.provider") == "gemini"
        assert server.commands.settings.get("kernel.model") == "gemini-2.5-pro"


@pytest.mark.asyncio
async def test_sse_query_string_master_token_rejected(running_server: HarnessWebServer):
    """
    Query string ?token=... is rejected with 401 on /events (§4.3 #5).
    """
    server = running_server
    base_url = f"http://127.0.0.1:{server.port}"

    async with httpx.AsyncClient(timeout=5.0) as client:
        # Request with master token in query param and NO headers / cookies
        res = await client.get(f"{base_url}/events?token={server.auth_token}")
        assert res.status_code == 401


@pytest.mark.asyncio
async def test_sse_ticket_auth_single_use(running_server: HarnessWebServer):
    """
    1. Unauthenticated POST /auth/ticket is rejected (401).
    2. Authenticated POST /auth/ticket returns a short-lived ticket.
    3. GET /events?ticket={ticket} succeeds (200 text/event-stream).
    4. Second GET /events?ticket={ticket} is rejected (401) because the ticket burned.
    """
    server = running_server
    base_url = f"http://127.0.0.1:{server.port}"

    async with httpx.AsyncClient(timeout=5.0) as client:
        # 1. Unauthenticated ticket request
        unauth_res = await client.post(f"{base_url}/auth/ticket", json={})
        assert unauth_res.status_code == 401

        # 2. Authenticated ticket request
        auth_res = await client.post(
            f"{base_url}/auth/ticket",
            headers={"X-Harness-Token": server.auth_token},
            json={},
        )
        assert auth_res.status_code == 200
        ticket_data = auth_res.json()
        assert "ticket" in ticket_data
        ticket = ticket_data["ticket"]
        assert len(ticket) >= 32

        # 3. Connect to /events with single-use ticket
        async with client.stream("GET", f"{base_url}/events?ticket={ticket}") as stream_res:
            assert stream_res.status_code == 200
            assert stream_res.headers["content-type"].startswith("text/event-stream")

        # 4. Attempt second connection with same burned ticket -> 401
        replay_res = await client.get(f"{base_url}/events?ticket={ticket}")
        assert replay_res.status_code == 401


@pytest.mark.asyncio
async def test_sse_cookie_auth(running_server: HarnessWebServer):
    """
    1. GET / sets HttpOnly harness_token cookie.
    2. EventSource connection with that cookie succeeds without headers.
    """
    server = running_server
    base_url = f"http://127.0.0.1:{server.port}"

    async with httpx.AsyncClient(timeout=5.0) as client:
        # 1. Visit index
        index_res = await client.get(f"{base_url}/")
        assert index_res.status_code == 200
        assert "set-cookie" in index_res.headers
        cookie_header = index_res.headers["set-cookie"]
        assert "harness_token=" in cookie_header
        assert "HttpOnly" in cookie_header
        assert "SameSite=strict" in cookie_header or "SameSite=Strict" in cookie_header

        # 2. Connect to /events with cookie
        async with client.stream(
            "GET",
            f"{base_url}/events",
            cookies={"harness_token": server.auth_token},
        ) as stream_res:
            assert stream_res.status_code == 200
            assert stream_res.headers["content-type"].startswith("text/event-stream")


@pytest.mark.asyncio
async def test_workspace_secrets_purged_on_startup(
    commands: Commands, event_bus: EventBus, workspace_dir: Path
):
    """
    If a workspace pre-exists with mode 0644 and plaintext api_keys in settings.json,
    HarnessWebServer.__init__ migrates keys to Secrets and rewrites the file mode 0600 without api_keys.
    """
    harness_dir = workspace_dir / ".harness"
    harness_dir.mkdir(parents=True, exist_ok=True)
    settings_file = harness_dir / "settings.json"

    # Pre-populate unsafe settings
    preexisting_secret = "sk-ant-PREEXISTING-998877"
    settings_file.write_text(
        json.dumps({
            "general": {"theme": "light"},
            "api_keys": {"anthropic": preexisting_secret},
        }),
        encoding="utf-8",
    )
    settings_file.chmod(0o644)

    # Instantiate server
    server = HarnessWebServer(
        commands=commands,
        bus=event_bus,
        host="127.0.0.1",
        port=0,
        sandbox_port=0,
        workspace=workspace_dir,
    )

    # Verify disk state
    file_mode = settings_file.stat().st_mode & 0o777
    assert file_mode == 0o600, f"Expected 0600, got {oct(file_mode)}"

    content = settings_file.read_text(encoding="utf-8")
    assert preexisting_secret not in content
    data = json.loads(content)
    assert "api_keys" not in data
    assert data.get("general", {}).get("theme") == "light"

    # Verify secret was safely migrated into Secrets store
    assert server.commands.secrets.get("anthropic_api_key") == preexisting_secret


@pytest.mark.asyncio
async def test_session_switch_validation(
    running_server: HarnessWebServer, commands: Commands
):
    """
    POST /sessions/switch:
    - Rejects directory traversal session_id (400)
    - Rejects non-existent session_id (404)
    - Successfully switches to an existing session (200)
    """
    server = running_server
    base_url = f"http://127.0.0.1:{server.port}"
    headers = {"X-Harness-Token": server.auth_token, "Content-Type": "application/json"}

    # Create real session
    real_session = await commands.session_manager.create_session()

    async with httpx.AsyncClient(timeout=5.0) as client:
        # Traversal attempt
        res_trav = await client.post(
            f"{base_url}/sessions/switch",
            headers=headers,
            json={"session_id": "../../etc/passwd"},
        )
        assert res_trav.status_code == 400

        # Non-existent session (valid format, but doesn't exist)
        res_nonexistent = await client.post(
            f"{base_url}/sessions/switch",
            headers=headers,
            json={"session_id": "session_000000000000"},
        )
        assert res_nonexistent.status_code == 404

        # Valid session
        res_valid = await client.post(
            f"{base_url}/sessions/switch",
            headers=headers,
            json={"session_id": real_session.session_id},
        )
        assert res_valid.status_code == 200
        data = res_valid.json()
        assert data.get("status") == "switched"
        assert data.get("session_id") == real_session.session_id
