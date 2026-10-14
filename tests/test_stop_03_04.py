import base64
import stat
from pathlib import Path

import httpx
import pytest

from harness.core.coordinator import AgentCoordinator
from harness.core.daemon import HarnessDaemon
from harness.kernel.validators import validate_safe_filename, validate_session_id
from harness.models.router import ModelRouter


def test_validator_session_id():
    # Valid session ID
    assert validate_session_id("session_0123456789ab") == "session_0123456789ab"

    # Traversal and invalid formats
    with pytest.raises(ValueError):
        validate_session_id("../../../etc")
    with pytest.raises(ValueError):
        validate_session_id("session_xyz")
    with pytest.raises(ValueError):
        validate_session_id("")
    with pytest.raises(ValueError):
        validate_session_id("session_0123456789a")  # too short
    with pytest.raises(ValueError):
        validate_session_id("session_0123456789abc")  # too long


def test_validator_safe_filename(tmp_path: Path):
    assert validate_safe_filename("report.md", tmp_path) == "report.md"

    with pytest.raises(ValueError):
        validate_safe_filename("../report.md", tmp_path)
    with pytest.raises(ValueError):
        validate_safe_filename("/etc/passwd", tmp_path)
    with pytest.raises(ValueError):
        validate_safe_filename(".", tmp_path)
    with pytest.raises(ValueError):
        validate_safe_filename("..", tmp_path)


def test_atomic_switch_session(tmp_path: Path):
    coord = AgentCoordinator(workspace=tmp_path)
    original_sid = coord.session_id

    # Attempting to switch to an invalid session ID should raise ValueError and leave state untouched
    with pytest.raises(ValueError):
        coord.switch_session("../../evil_session")

    assert coord.session_id == original_sid
    assert not (tmp_path / "evil_session").exists()


@pytest.mark.asyncio
async def test_session_traversal_via_http(tmp_path: Path):
    coord = AgentCoordinator(workspace=tmp_path)
    daemon = HarnessDaemon(coordinator=coord, host="127.0.0.1", port=0, sandbox_port=0)

    api_runner, sandbox_runner = await daemon.start()
    base_url = f"http://127.0.0.1:{daemon.port}"
    headers = {"X-Harness-Token": daemon.auth_token}

    try:
        async with httpx.AsyncClient(headers=headers) as client:
            # 1. Traversal in session_id switch
            res_switch = await client.post(
                f"{base_url}/sessions/switch",
                json={"session_id": "../../escape_attempt"},
            )
            assert res_switch.status_code == 400
            assert not (tmp_path.parent / "escape_attempt").exists()

            # 2. Traversal in session_id get
            res_get = await client.get(f"{base_url}/sessions/..%2F..%2Fescape")
            assert res_get.status_code in (400, 404)

            # 3. Traversal in artifact get
            res_art = await client.get(f"{base_url}/artifacts/..%2F..%2Fsecret.txt")
            assert res_art.status_code == 400

            # 4. Traversal in media upload (JSON)
            res_up = await client.post(
                f"{base_url}/media/upload",
                json={
                    "filename": "../evil.png",
                    "data": base64.b64encode(b"dummy").decode("ascii"),
                },
            )
            assert res_up.status_code == 400

            # 5. Non-allowlisted extension in media upload
            res_bad_ext = await client.post(
                f"{base_url}/media/upload",
                json={
                    "filename": "payload.py",
                    "data": base64.b64encode(b"print(1)").decode("ascii"),
                },
            )
            assert res_bad_ext.status_code == 400
    finally:
        daemon.cleanup_daemon_json()
        await api_runner.cleanup()
        await sandbox_runner.cleanup()


@pytest.mark.asyncio
async def test_secrets_and_settings_clean(tmp_path: Path):
    """
    STOP-04:
    - settings.json written with 0600 mode.
    - No raw api_keys exposed in GET /settings.
    - Fabricated account section deleted.
    - MCP server statuses are not_configured.
    """
    coord = AgentCoordinator(workspace=tmp_path)
    coord.router.api_keys["openai"] = "sk-super-secret-key-12345"

    daemon = HarnessDaemon(coordinator=coord, host="127.0.0.1", port=0, sandbox_port=0)
    api_runner, sandbox_runner = await daemon.start()
    base_url = f"http://127.0.0.1:{daemon.port}"
    headers = {"X-Harness-Token": daemon.auth_token}

    try:
        async with httpx.AsyncClient(headers=headers) as client:
            # 1. Check GET /settings
            res = await client.get(f"{base_url}/settings")
            assert res.status_code == 200
            settings = res.json()

            # Verify no secret key is in response
            assert "sk-super-secret-key-12345" not in res.text
            assert settings["configured_providers"]["openai"] is True

            # Verify fake account section is gone
            assert "account" not in settings

            # Verify MCP server statuses are not_configured
            mcp_servers = settings.get("extensions", {}).get("mcp_servers", {})
            for info in mcp_servers.values():
                assert info["status"] == "not_configured"

            # 2. Check POST /settings file mode 0600
            res_post = await client.post(
                f"{base_url}/settings",
                json={"general": {"theme": "light"}},
            )
            assert res_post.status_code == 200

            sf = tmp_path / ".harness" / "settings.json"
            assert sf.exists()
            mode = stat.S_IMODE(sf.stat().st_mode)
            assert mode == 0o600, f"Expected 0600, got {oct(mode)}"
    finally:
        daemon.cleanup_daemon_json()
        await api_runner.cleanup()
        await sandbox_runner.cleanup()


def test_no_load_hermes_env():
    # Verify _load_hermes_env has been deleted
    assert not hasattr(ModelRouter, "_load_hermes_env")
