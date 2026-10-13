import pytest
from pathlib import Path
from aiohttp import web
import httpx

from harness.core.coordinator import AgentCoordinator
from harness.core.daemon import HarnessDaemon
from harness.artifacts.manager import ArtifactMetadata

@pytest.mark.asyncio
async def test_ui_and_studio_endpoints(tmp_path: Path):
    coordinator = AgentCoordinator(workspace=tmp_path)
    coordinator.artifacts.write_artifact(
        "report.md",
        "# Architecture Spec\nDetails here.",
        ArtifactMetadata(summary="Test architecture report"),
    )

    daemon = HarnessDaemon(coordinator=coordinator, host="127.0.0.1", port=8990, sandbox_port=8991)

    app_runner = web.AppRunner(daemon.app)
    await app_runner.setup()
    api_site = web.TCPSite(app_runner, host="127.0.0.1", port=8990)
    await api_site.start()

    try:
        async with httpx.AsyncClient() as client:
            # 1. Test Web UI index.html at GET / and GET /ui
            res = await client.get("http://127.0.0.1:8990/")
            assert res.status_code == 200
            assert "Harness-Zero | Sovereign Agent Studio" in res.text

            res_ui = await client.get("http://127.0.0.1:8990/ui")
            assert res_ui.status_code == 200

            # 2. Test artifacts manifest
            res_art = await client.get("http://127.0.0.1:8990/artifacts")
            assert res_art.status_code == 200
            manifest = res_art.json()["manifest"]
            assert "report.md" in manifest

            # 3. Test artifact content fetch
            res_content = await client.get("http://127.0.0.1:8990/artifacts/report.md")
            assert res_content.status_code == 200
            assert "# Architecture Spec" in res_content.text

            # 4. Test spawn-tree endpoint
            res_tree = await client.get("http://127.0.0.1:8990/spawn-tree")
            assert res_tree.status_code == 200
            assert "tree" in res_tree.json()

            # 5. Test economics endpoint
            res_econ = await client.get("http://127.0.0.1:8990/economics")
            assert res_econ.status_code == 200

            # 6. Test code search
            res_search = await client.post("http://127.0.0.1:8990/search/code", json={"pattern": "class"})
            assert res_search.status_code == 200
            assert "matches" in res_search.json()

            # 7. Test file content read & save
            test_file = tmp_path / "hello.py"
            test_file.write_text("print('hello world')", encoding="utf-8")

            res_file = await client.get("http://127.0.0.1:8990/file/content?path=hello.py")
            assert res_file.status_code == 200
            file_data = res_file.json()
            assert file_data["path"] == "hello.py"
            assert file_data["content"] == "print('hello world')"
            assert file_data["lines_count"] == 1

            # Test file save
            res_save = await client.post("http://127.0.0.1:8990/file/save", json={
                "path": "hello.py",
                "content": "print('hello sovereign universe')"
            })
            assert res_save.status_code == 200
            assert test_file.read_text(encoding="utf-8") == "print('hello sovereign universe')"

            # 8. Test diffs endpoint
            res_diffs = await client.get("http://127.0.0.1:8990/diffs")
            assert res_diffs.status_code == 200
            assert "diff" in res_diffs.json()
            assert "modified_files" in res_diffs.json()

            # 9. Test Media Upload & Serving
            upload_payload = {
                "filename": "screenshot_test.png",
                "data": "iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAYAAAAfFcSJAAAADUlEQVR42mNk+M9QDwADhgGAWjR9awAAAABJRU5ErkJggg==",
                "mime_type": "image/png"
            }
            res_upload = await client.post("http://127.0.0.1:8990/media/upload", json=upload_payload)
            assert res_upload.status_code == 200
            up_data = res_upload.json()
            assert up_data["status"] == "ok"
            assert len(up_data["files"]) == 1
            uploaded_fn = up_data["files"][0]["filename"]

            res_media = await client.get(f"http://127.0.0.1:8990/media/{uploaded_fn}")
            assert res_media.status_code == 200
            assert res_media.headers["content-type"] == "image/png"
            assert len(res_media.content) > 0

            # 10. Test Sessions List, New, Switch, and History
            res_sess = await client.get("http://127.0.0.1:8990/sessions")
            assert res_sess.status_code == 200
            sessions_data = res_sess.json()
            assert "sessions" in sessions_data
            assert len(sessions_data["sessions"]) >= 1

            # New Session
            res_new_s = await client.post("http://127.0.0.1:8990/sessions/new")
            assert res_new_s.status_code == 200
            new_sid = res_new_s.json()["session_id"]
            assert new_sid.startswith("session_")
            assert coordinator.session_id == new_sid

            # Switch Session
            res_switch = await client.post("http://127.0.0.1:8990/sessions/switch", json={"session_id": new_sid})
            assert res_switch.status_code == 200
            assert res_switch.json()["status"] == "switched"

            # Session History
            res_hist = await client.get(f"http://127.0.0.1:8990/sessions/{new_sid}")
            assert res_hist.status_code == 200
            assert "history" in res_hist.json()

            # 11. Test Skills Playbook & Plugins
            res_skills = await client.get("http://127.0.0.1:8990/skills")
            assert res_skills.status_code == 200
            skills_list = res_skills.json()["skills"]
            if skills_list:
                first_skill_name = skills_list[0]["name"]
                res_pb = await client.get(f"http://127.0.0.1:8990/skills/{first_skill_name}")
                assert res_pb.status_code == 200
                assert "playbook" in res_pb.json()

            # Plugins endpoint
            res_plugins = await client.get("http://127.0.0.1:8990/plugins")
            assert res_plugins.status_code == 200
            plug_data = res_plugins.json()
            assert "servers" in plug_data
            assert "tools" in plug_data
            assert "filesystem_mcp" in plug_data["servers"]
    finally:
        await app_runner.cleanup()
