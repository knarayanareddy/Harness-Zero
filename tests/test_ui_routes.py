from pathlib import Path

import httpx
import pytest
from aiohttp import web

from harness.artifacts.manager import ArtifactMetadata
from harness.core.coordinator import AgentCoordinator
from harness.core.daemon import HarnessDaemon


@pytest.mark.asyncio
async def test_ui_and_studio_endpoints(tmp_path: Path):
    coordinator = AgentCoordinator(workspace=tmp_path)
    coordinator.artifacts.write_artifact(
        "report.md",
        "# Architecture Spec\nDetails here.",
        ArtifactMetadata(summary="Test architecture report"),
    )

    daemon = HarnessDaemon(coordinator=coordinator, host="127.0.0.1", port=0, sandbox_port=0)

    app_runner = web.AppRunner(daemon.app)
    await app_runner.setup()
    api_site = web.TCPSite(app_runner, host="127.0.0.1", port=0)
    await api_site.start()
    port = api_site._server.sockets[0].getsockname()[1]
    daemon.port = port
    base_url = f"http://127.0.0.1:{port}"

    try:
        async with httpx.AsyncClient(headers={"X-Harness-Token": daemon.auth_token}) as client:
            # 1. Test Web UI index.html at GET / and GET /ui
            res = await client.get(f"{base_url}/")
            assert res.status_code == 200
            assert "Harness-Zero | Sovereign Agent Studio" in res.text

            res_ui = await client.get(f"{base_url}/ui")
            assert res_ui.status_code == 200

            # 2. Test artifacts manifest
            res_art = await client.get(f"{base_url}/artifacts")
            assert res_art.status_code == 200
            manifest = res_art.json()["manifest"]
            assert "report.md" in manifest

            # 3. Test artifact content fetch
            res_content = await client.get(f"{base_url}/artifacts/report.md")
            assert res_content.status_code == 200
            assert "# Architecture Spec" in res_content.text

            # 4. Test spawn-tree endpoint
            res_tree = await client.get(f"{base_url}/spawn-tree")
            assert res_tree.status_code == 200
            assert "tree" in res_tree.json()

            # 5. Test economics endpoint
            res_econ = await client.get(f"{base_url}/economics")
            assert res_econ.status_code == 200

            # 6. Test code search
            res_search = await client.post(f"{base_url}/search/code", json={"pattern": "class"})
            assert res_search.status_code == 200
            assert "matches" in res_search.json()

            # 7. Test file content read & save
            test_file = tmp_path / "hello.py"
            test_file.write_text("print('hello world')", encoding="utf-8")

            res_file = await client.get(f"{base_url}/file/content?path=hello.py")
            assert res_file.status_code == 200
            file_data = res_file.json()
            assert file_data["path"] == "hello.py"
            assert file_data["content"] == "print('hello world')"
            assert file_data["lines_count"] == 1

            # Test file save
            res_save = await client.post(f"{base_url}/file/save", json={
                "path": "hello.py",
                "content": "print('hello sovereign universe')"
            })
            assert res_save.status_code == 200
            assert test_file.read_text(encoding="utf-8") == "print('hello sovereign universe')"

            # 8. Test diffs endpoint
            res_diffs = await client.get(f"{base_url}/diffs")
            assert res_diffs.status_code == 200
            assert "diff" in res_diffs.json()
            assert "modified_files" in res_diffs.json()

            # 9. Test Media Upload & Serving
            upload_payload = {
                "filename": "screenshot_test.png",
                "data": "iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAYAAAAfFcSJAAAADUlEQVR42mNk+M9QDwADhgGAWjR9awAAAABJRU5ErkJggg==",
                "mime_type": "image/png"
            }
            res_upload = await client.post(f"{base_url}/media/upload", json=upload_payload)
            assert res_upload.status_code == 200
            up_data = res_upload.json()
            assert up_data["status"] == "ok"
            assert len(up_data["files"]) == 1
            uploaded_fn = up_data["files"][0]["filename"]

            res_media = await client.get(f"{base_url}/media/{uploaded_fn}")
            assert res_media.status_code == 200
            assert res_media.headers["content-type"] == "image/png"
            assert len(res_media.content) > 0

            # 10. Test Sessions List, New, Switch, and History
            res_sess = await client.get(f"{base_url}/sessions")
            assert res_sess.status_code == 200
            sessions_data = res_sess.json()
            assert "sessions" in sessions_data
            assert len(sessions_data["sessions"]) >= 1

            # New Session
            res_new_s = await client.post(f"{base_url}/sessions/new")
            assert res_new_s.status_code == 200
            new_sid = res_new_s.json()["session_id"]
            assert new_sid.startswith("session_")
            assert coordinator.session_id == new_sid

            # Switch Session
            res_switch = await client.post(f"{base_url}/sessions/switch", json={"session_id": new_sid})
            assert res_switch.status_code == 200
            assert res_switch.json()["status"] == "switched"

            # Session History
            res_hist = await client.get(f"{base_url}/sessions/{new_sid}")
            assert res_hist.status_code == 200
            assert "history" in res_hist.json()

            # 11. Test Skills Playbook & Plugins
            res_skills = await client.get(f"{base_url}/skills")
            assert res_skills.status_code == 200
            skills_list = res_skills.json()["skills"]
            if skills_list:
                first_skill_name = skills_list[0]["name"]
                res_pb = await client.get(f"{base_url}/skills/{first_skill_name}")
                assert res_pb.status_code == 200
                assert "playbook" in res_pb.json()

            # 12. Test Turn Cancellation & Replicated Claude Cowork Settings
            res_cancel = await client.post(f"{base_url}/turn/cancel")
            assert res_cancel.status_code == 200
            assert res_cancel.json()["status"] in ("idle", "cancelled")

            # Test GET /settings returns all 17 sections reverse-engineered from Claude Desktop
            res_set = await client.get(f"{base_url}/settings")
            assert res_set.status_code == 200
            set_data = res_set.json()
            for expected_section in [
                "general", "account", "privacy", "billing", "capabilities",
                "memory", "reflect", "time_focus", "claude_code", "system",
                "extensions", "developer", "skills", "connectors", "plugins",
                "models", "security"
            ]:
                assert expected_section in set_data, f"Missing {expected_section} in settings"

            assert set_data["system"]["user_files_path"]
            assert set_data["billing"]["plan"] == "Free"

            # Test POST /settings persists modifications across sections
            res_set_post = await client.post(f"{base_url}/settings", json={
                "account": {"full_name": "Test User", "display_name": "testuser"},
                "cowork": {"trusted_folders": ["/tmp/trusted"]},
                "system": {"device_name": "test-device"},
                "time_focus": {"focus_interval_minutes": 30, "quiet_hours_enabled": True},
                "reflect": {"reflection_depth": "deep", "thinking_budget": "high"},
                "claude_code": {"code_theme": "tokyo-night"},
                "models": {"temperature": 0.45, "auto_fallback": True},
                "security": {"session_spend_cap_usd": 12.50}
            })
            assert res_set_post.status_code == 200
            saved = res_set_post.json()
            assert saved["status"] == "saved"
            assert saved["settings"]["account"]["full_name"] == "Test User"
            assert saved["settings"]["time_focus"]["focus_interval_minutes"] == 30
            assert saved["settings"]["reflect"]["reflection_depth"] == "deep"
            assert saved["settings"]["claude_code"]["code_theme"] == "tokyo-night"
            assert saved["settings"]["models"]["temperature"] == 0.45
            assert saved["settings"]["security"]["session_spend_cap_usd"] == 12.50
    finally:
        await app_runner.cleanup()
