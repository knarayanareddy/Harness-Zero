import os
import stat
from pathlib import Path

import httpx
import pytest
from aiohttp import web

from harness.core.coordinator import AgentCoordinator
from harness.core.daemon import HarnessDaemon


@pytest.mark.asyncio
async def test_auth_all_routes_protected_except_public(tmp_path: Path):
    """
    STOP-01: Verifies that public routes respond without auth token,
    and every other registered route requires X-Harness-Token (401 without it).
    """
    coord = AgentCoordinator(workspace=tmp_path)
    daemon = HarnessDaemon(coordinator=coord, host="127.0.0.1", port=0, sandbox_port=0)

    app_runner = web.AppRunner(daemon.app)
    await app_runner.setup()
    site = web.TCPSite(app_runner, host="127.0.0.1", port=0)
    await site.start()
    port = site._server.sockets[0].getsockname()[1]
    daemon.port = port
    base_url = f"http://127.0.0.1:{port}"

    try:
        async with httpx.AsyncClient() as client:
            # 1. Public routes should return 200 without token
            for path in ["/", "/ui", "/health"]:
                res = await client.get(f"{base_url}{path}")
                assert res.status_code == 200, f"Expected 200 for public path {path}, got {res.status_code}"

            # 2. Protected GET routes without token -> 401
            for path in ["/skills", "/tasks", "/files", "/artifacts", "/diffs", "/sessions"]:
                res = await client.get(f"{base_url}{path}")
                assert res.status_code == 401, f"Expected 401 for unauthenticated path {path}, got {res.status_code}"

            # 3. Protected GET routes with token -> not 401
            for path in ["/skills", "/tasks", "/files", "/artifacts", "/diffs", "/sessions"]:
                res = await client.get(
                    f"{base_url}{path}",
                    headers={"X-Harness-Token": daemon.auth_token},
                )
                assert res.status_code != 401, f"Expected not 401 for authenticated path {path}, got {res.status_code}"

            # 4. Bearer Authorization header support
            res_bearer = await client.get(
                f"{base_url}/skills",
                headers={"Authorization": f"Bearer {daemon.auth_token}"},
            )
            assert res_bearer.status_code == 200
    finally:
        await app_runner.cleanup()


@pytest.mark.asyncio
async def test_daemon_json_written_with_mode_0600(tmp_path: Path):
    """
    STOP-01: daemon.json is written with 0600 permissions and contains token, port, sandbox_port, pid.
    """
    coord = AgentCoordinator(workspace=tmp_path)
    daemon = HarnessDaemon(coordinator=coord, host="127.0.0.1", port=0, sandbox_port=0)

    api_runner, sandbox_runner = await daemon.start()
    try:
        daemon_file = tmp_path / ".harness" / "daemon.json"
        assert daemon_file.exists()

        file_stat = daemon_file.stat()
        file_mode = stat.S_IMODE(file_stat.st_mode)
        assert file_mode == 0o600, f"Expected mode 0600, got {oct(file_mode)}"

        import json
        data = json.loads(daemon_file.read_text(encoding="utf-8"))
        assert data["port"] == daemon.port
        assert data["sandbox_port"] == daemon.sandbox_port
        assert data["token"] == daemon.auth_token
        assert data["pid"] == os.getpid()
    finally:
        daemon.cleanup_daemon_json()
        await api_runner.cleanup()
        await sandbox_runner.cleanup()

    assert not (tmp_path / ".harness" / "daemon.json").exists()


@pytest.mark.asyncio
async def test_host_guard_dns_rebinding(tmp_path: Path):
    """
    STOP-01 / SEC-03: Host header validation rejects malicious or unexpected Host headers with 421.
    """
    coord = AgentCoordinator(workspace=tmp_path)
    daemon = HarnessDaemon(coordinator=coord, host="127.0.0.1", port=0, sandbox_port=0)

    app_runner = web.AppRunner(daemon.app)
    await app_runner.setup()
    site = web.TCPSite(app_runner, host="127.0.0.1", port=0)
    await site.start()
    port = site._server.sockets[0].getsockname()[1]
    daemon.port = port
    base_url = f"http://127.0.0.1:{port}"

    try:
        async with httpx.AsyncClient() as client:
            # Bad host -> 421
            res_bad = await client.get(f"{base_url}/health", headers={"Host": "evil.example"})
            assert res_bad.status_code == 421

            # Legitimate host -> 200
            res_ok = await client.get(f"{base_url}/health", headers={"Host": f"127.0.0.1:{port}"})
            assert res_ok.status_code == 200

            res_localhost = await client.get(f"{base_url}/health", headers={"Host": f"localhost:{port}"})
            assert res_localhost.status_code == 200
    finally:
        await app_runner.cleanup()


@pytest.mark.asyncio
async def test_csrf_content_type_and_origin_guards(tmp_path: Path):
    """
    STOP-01 / SEC-02: Enforces Content-Type application/json and checks Origin / Sec-Fetch-Site.
    """
    coord = AgentCoordinator(workspace=tmp_path)
    daemon = HarnessDaemon(coordinator=coord, host="127.0.0.1", port=0, sandbox_port=0)

    app_runner = web.AppRunner(daemon.app)
    await app_runner.setup()
    site = web.TCPSite(app_runner, host="127.0.0.1", port=0)
    await site.start()
    port = site._server.sockets[0].getsockname()[1]
    daemon.port = port
    base_url = f"http://127.0.0.1:{port}"

    try:
        async with httpx.AsyncClient() as client:
            # text/plain on mutating route with valid token -> 415
            res_text = await client.post(
                f"{base_url}/file/save",
                content='{"path":"test.txt","content":"pwned"}',
                headers={
                    "Content-Type": "text/plain",
                    "X-Harness-Token": daemon.auth_token,
                },
            )
            assert res_text.status_code == 415

            # evil Origin on mutating route -> 403
            res_origin = await client.post(
                f"{base_url}/file/save",
                json={"path": "test.txt", "content": "safe"},
                headers={
                    "Origin": "https://evil.example",
                    "X-Harness-Token": daemon.auth_token,
                },
            )
            assert res_origin.status_code == 403

            # Sec-Fetch-Site: cross-site -> 403
            res_cross = await client.post(
                f"{base_url}/file/save",
                json={"path": "test.txt", "content": "safe"},
                headers={
                    "Sec-Fetch-Site": "cross-site",
                    "X-Harness-Token": daemon.auth_token,
                },
            )
            assert res_cross.status_code == 403
    finally:
        await app_runner.cleanup()


@pytest.mark.asyncio
async def test_csp_headers_on_api_and_sandbox_origins(tmp_path: Path):
    """
    STOP-02 / SEC-05: Verifies CSP headers on Port 8000 (API) and Port 8001 (Sandbox).
    """
    coord = AgentCoordinator(workspace=tmp_path)
    daemon = HarnessDaemon(coordinator=coord, host="127.0.0.1", port=0, sandbox_port=0)

    api_runner, sandbox_runner = await daemon.start()
    base_api = f"http://127.0.0.1:{daemon.port}"
    base_sandbox = f"http://127.0.0.1:{daemon.sandbox_port}"

    try:
        async with httpx.AsyncClient() as client:
            # 1. API index page CSP
            res_api = await client.get(f"{base_api}/")
            assert res_api.status_code == 200
            csp_api = res_api.headers.get("content-security-policy", "")
            assert "default-src 'self'" in csp_api
            assert f"frame-src http://127.0.0.1:{daemon.sandbox_port}" in csp_api
            assert "frame-ancestors 'none'" in csp_api

            # 2. Sandbox index page CSP
            res_sb = await client.get(f"{base_sandbox}/")
            assert res_sb.status_code == 200
            csp_sb = res_sb.headers.get("content-security-policy", "")
            assert "default-src 'none'" in csp_sb
            assert "connect-src 'none'" in csp_sb
            assert f"frame-ancestors http://127.0.0.1:{daemon.port}" in csp_sb
    finally:
        daemon.cleanup_daemon_json()
        await api_runner.cleanup()
        await sandbox_runner.cleanup()
