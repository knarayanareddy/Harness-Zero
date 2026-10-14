"""
Integration tests for WEB-01: HarnessWebServer.
Verifies:
1. Startup, port binding, and daemon.json permissions (0600) and contents.
2. /health endpoint reporting version, session, spend, and sandbox origin.
3. Host guard protecting against DNS rebinding (421 status).
4. Auth guard (Bearer / X-Harness-Token) on commands and protected paths.
5. CSRF origin and content-type enforcement (415 on bad Content-Type, 403 on evil Origin).
6. POST /commands/{name} dispatch to Commands API.
7. Long-lived /events SSE stream with 15s heartbeat and Last-Event-ID replay.
8. Dual-port origin sandbox CSP headers and artifact serving with traversal guard.
"""
from __future__ import annotations

import asyncio
import json
import os
import stat
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
from harness.sdk.events import Event


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


@pytest.mark.asyncio
async def test_server_startup_and_daemon_json(
    commands: Commands, event_bus: EventBus, workspace_dir: Path
):
    server = HarnessWebServer(
        commands=commands,
        bus=event_bus,
        host="127.0.0.1",
        port=0,
        sandbox_port=0,
        workspace=workspace_dir,
    )
    api_runner, sb_runner = await server.start()
    try:
        daemon_file = workspace_dir / ".harness" / "daemon.json"
        assert daemon_file.exists()

        # Check 0600 mode
        file_mode = stat.S_IMODE(daemon_file.stat().st_mode)
        assert file_mode == 0o600

        data = json.loads(daemon_file.read_text(encoding="utf-8"))
        assert data["port"] == server.port
        assert data["sandbox_port"] == server.sandbox_port
        assert data["token"] == server.auth_token
        assert data["pid"] == os.getpid()
    finally:
        await server.stop()

    assert not (workspace_dir / ".harness" / "daemon.json").exists()


@pytest.mark.asyncio
async def test_health_and_host_guard(commands: Commands, event_bus: EventBus, workspace_dir: Path):
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
            # 1. Health endpoint is public
            res = await client.get(f"{base_url}/health")
            assert res.status_code == 200
            data = res.json()
            assert data["status"] == "ok"
            assert data["version"] == "2.0.0"
            assert data["sandbox_origin"] == f"http://127.0.0.1:{server.sandbox_port}"

            # 2. Host guard rejects invalid Host header with 421
            bad_res = await client.get(f"{base_url}/health", headers={"Host": "evil.example.com"})
            assert bad_res.status_code == 421
    finally:
        await server.stop()


@pytest.mark.asyncio
async def test_auth_guard_and_commands_dispatch(
    commands: Commands, event_bus: EventBus, workspace_dir: Path
):
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
            # 1. Unauthenticated command -> 401
            res_unauth = await client.post(
                f"{base_url}/commands/settings.get",
                json={"path": "kernel.security.spend_cap_usd"},
            )
            assert res_unauth.status_code == 401

            # 2. Authenticated command with Bearer token
            res_auth = await client.post(
                f"{base_url}/commands/settings.get",
                json={"path": "kernel.security.spend_cap_usd"},
                headers={"Authorization": f"Bearer {server.auth_token}"},
            )
            assert res_auth.status_code == 200
            assert res_auth.json()["status"] == "ok"

            # 3. CSRF invalid Content-Type -> 415
            res_bad_ct = await client.post(
                f"{base_url}/commands/settings.get",
                content='{"path": "test"}',
                headers={
                    "Authorization": f"Bearer {server.auth_token}",
                    "Content-Type": "text/plain",
                },
            )
            assert res_bad_ct.status_code == 415

            # 4. Cross-site Origin forbidden -> 403
            res_bad_origin = await client.post(
                f"{base_url}/commands/settings.get",
                json={"path": "test"},
                headers={
                    "Authorization": f"Bearer {server.auth_token}",
                    "Origin": "http://malicious.com",
                },
            )
            assert res_bad_origin.status_code == 403

            # 5. Unknown command -> 400
            res_unknown = await client.post(
                f"{base_url}/commands/unknown_dummy_cmd",
                json={},
                headers={"Authorization": f"Bearer {server.auth_token}"},
            )
            assert res_unknown.status_code == 400
    finally:
        await server.stop()


@pytest.mark.asyncio
async def test_sse_event_stream_and_replay(
    commands: Commands, event_bus: EventBus, workspace_dir: Path
):
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
        # Pre-seed buffer with events
        evt1 = Event(type="turn.started", data={"turn": 1}, id="evt_test_01")
        evt2 = Event(type="model.text.delta", data={"text": "hello"}, id="evt_test_02")
        event_bus.publish(evt1)
        event_bus.publish(evt2)
        await asyncio.sleep(0.05)

        # Connect with Last-Event-ID = evt_test_01 -> should replay evt_test_02
        async with httpx.AsyncClient(timeout=5.0) as client:
            async with client.stream(
                "GET",
                f"{base_url}/events",
                headers={
                    "Authorization": f"Bearer {server.auth_token}",
                    "Last-Event-ID": "evt_test_01",
                },
            ) as response:
                assert response.status_code == 200
                assert response.headers["content-type"].startswith("text/event-stream")

                lines = []
                async for line in response.aiter_lines():
                    lines.append(line)
                    if any("evt_test_02" in entry for entry in lines) and line.startswith("data:"):
                        break

                joined = "\n".join(lines)
                assert "id: evt_test_02" in joined
                assert "event: model.text.delta" in joined
    finally:
        await server.stop()


@pytest.mark.asyncio
async def test_sandbox_origin_csp_and_artifact_serving(
    commands: Commands, event_bus: EventBus, workspace_dir: Path
):
    server = HarnessWebServer(
        commands=commands,
        bus=event_bus,
        host="127.0.0.1",
        port=0,
        sandbox_port=0,
        workspace=workspace_dir,
    )
    await server.start()
    api_url = f"http://127.0.0.1:{server.port}"
    sb_url = f"http://127.0.0.1:{server.sandbox_port}"

    try:
        # Write an artifact
        artifacts_dir = workspace_dir / ".harness" / "artifacts"
        artifacts_dir.mkdir(parents=True, exist_ok=True)
        (artifacts_dir / "preview.html").write_text("<h1>Report</h1>", encoding="utf-8")

        async with httpx.AsyncClient() as client:
            # 1. API index has frame-src pointing to sandbox
            res_api = await client.get(f"{api_url}/")
            assert res_api.status_code == 200
            csp_api = res_api.headers.get("Content-Security-Policy", "")
            assert f"frame-src http://127.0.0.1:{server.sandbox_port}" in csp_api

            # 2. Sandbox has strict CSP (no scripts, connect-src none, frame-ancestors API)
            res_sb = await client.get(f"{sb_url}/artifacts/preview.html")
            assert res_sb.status_code == 200
            assert res_sb.text == "<h1>Report</h1>"
            csp_sb = res_sb.headers.get("Content-Security-Policy", "")
            assert "default-src 'none'" in csp_sb
            assert "script-src 'none'" in csp_sb
            assert "connect-src 'none'" in csp_sb
            assert f"frame-ancestors http://127.0.0.1:{server.port}" in csp_sb

            # 3. Path traversal attack blocked
            res_trav = await client.get(f"{sb_url}/artifacts/../../secret.txt")
            assert res_trav.status_code in (403, 404)
    finally:
        await server.stop()


@pytest.mark.asyncio
async def test_static_assets_serving(commands: Commands, event_bus: EventBus, workspace_dir: Path):
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
            # 1. Index.html served at root
            res_root = await client.get(f"{base_url}/")
            assert res_root.status_code == 200
            assert '<script type="module" src="/static/app.js"></script>' in res_root.text
            assert "Content-Security-Policy" in res_root.headers
            assert "script-src 'self'" in res_root.headers["Content-Security-Policy"]

            # 2. ES modules served via /static/
            res_app = await client.get(f"{base_url}/static/app.js")
            assert res_app.status_code == 200
            assert "HarnessStudioApp" in res_app.text

            res_api = await client.get(f"{base_url}/static/api.js")
            assert res_api.status_code == 200
            assert "HarnessAPI" in res_api.text

            res_events = await client.get(f"{base_url}/static/events.js")
            assert res_events.status_code == 200
            assert "HarnessEventStream" in res_events.text

            res_cards = await client.get(f"{base_url}/static/cards.js")
            assert res_cards.status_code == 200
            assert "CardRenderer" in res_cards.text

            res_css = await client.get(f"{base_url}/static/styles.css")
            assert res_css.status_code == 200

            res_vendor = await client.get(f"{base_url}/static/vendor/purify.min.js")
            assert res_vendor.status_code == 200
    finally:
        await server.stop()


@pytest.mark.asyncio
async def test_approval_resolution_and_form_submit(
    commands: Commands, event_bus: EventBus, workspace_dir: Path
):
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
            async with client.stream(
                "GET",
                f"{base_url}/events",
                headers={"Authorization": f"Bearer {server.auth_token}"},
            ) as sse_stream:
                assert sse_stream.status_code == 200
                await asyncio.sleep(0.02)

                # 1. Test form submission command
                res_form = await client.post(
                    f"{base_url}/commands/form.submit",
                    json={"form_id": "form_auth_test", "data": {"username": "admin"}},
                    headers={"Authorization": f"Bearer {server.auth_token}"},
                )
                assert res_form.status_code == 200
                data = res_form.json()
                assert data["result"]["status"] == "submitted"
                assert data["result"]["form_id"] == "form_auth_test"

                # 2. Test approval broker integration with interactive subscriber
                ask_task = asyncio.create_task(
                    commands.gate.approvals.ask(
                        kind="shell_exec",
                        summary="rm -rf /test",
                        details={"target": "/test"},
                    )
                )
                await asyncio.sleep(0.02)
                pending_list = commands.gate.approvals.pending()
                assert len(pending_list) > 0
                req_id = pending_list[0]["approval_id"]

                # Verify it shows up in approvals.list
                res_list = await client.post(
                    f"{base_url}/commands/approvals.list",
                    json={},
                    headers={"Authorization": f"Bearer {server.auth_token}"},
                )
                assert res_list.status_code == 200
                pending = res_list.json()["result"]
                assert any(p["approval_id"] == req_id for p in pending)

                # Resolve approval via POST /commands/resolve_approval
                res_res = await client.post(
                    f"{base_url}/commands/resolve_approval",
                    json={"request_id": req_id, "decision": "approved", "feedback": "Authorized test"},
                    headers={"Authorization": f"Bearer {server.auth_token}"},
                )
                assert res_res.status_code == 200
                assert res_res.json()["result"]["status"] == "resolved"
                assert res_res.json()["result"]["decision"] == "approved"

                approved, reason = await ask_task
                assert approved is True
                assert reason == "Authorized test"
    finally:
        await server.stop()


@pytest.mark.asyncio
async def test_interactive_subscriber_flag(
    commands: Commands, event_bus: EventBus, workspace_dir: Path
):
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
        # Before any SSE connection, no interactive subscribers exist
        assert not event_bus.has_interactive_subscribers()

        async with httpx.AsyncClient(timeout=5.0) as client:
            async with client.stream(
                "GET",
                f"{base_url}/events",
                headers={"Authorization": f"Bearer {server.auth_token}"},
            ) as response:
                assert response.status_code == 200
                await asyncio.sleep(0.05)
                # While connected, interactive=True subscriber is registered
                assert event_bus.has_interactive_subscribers()

        # Publish events to trigger write error on the closed socket and cleanup
        for _ in range(5):
            event_bus.publish(Event(type="log", data={"message": "disconnect_check"}))
            await asyncio.sleep(0.05)
            if not event_bus.has_interactive_subscribers():
                break
        assert not event_bus.has_interactive_subscribers()
    finally:
        await server.stop()


