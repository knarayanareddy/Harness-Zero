from pathlib import Path
import pytest
from aiohttp.test_utils import AioHTTPTestCase, unittest_run_loop
from aiohttp import web

from harness.core.coordinator import AgentCoordinator
from harness.core.daemon import HarnessDaemon
from harness.artifacts.manager import ArtifactMetadata


@pytest.mark.asyncio
async def test_daemon_dual_port_endpoints(tmp_path: Path):
    coordinator = AgentCoordinator(workspace=tmp_path)
    coordinator.artifacts.write_artifact(
        "preview.html",
        "<h1>Generative UI Widget</h1>",
        ArtifactMetadata(summary="Preview demo widget"),
    )

    daemon = HarnessDaemon(coordinator=coordinator, port=8888, sandbox_port=8889)

    # Test API App
    app_runner = web.AppRunner(daemon.app)
    await app_runner.setup()
    api_site = web.TCPSite(app_runner, host="127.0.0.1", port=8888)
    await api_site.start()

    # Test Sandbox App
    sandbox_runner = web.AppRunner(daemon.sandbox_app)
    await sandbox_runner.setup()
    sandbox_site = web.TCPSite(sandbox_runner, host="127.0.0.1", port=8889)
    await sandbox_site.start()

    try:
        import httpx
        async with httpx.AsyncClient() as client:
            # 1. API Health
            res_api = await client.get("http://127.0.0.1:8888/health")
            assert res_api.status_code == 200
            assert res_api.json()["status"] == "online"

            # 2. JSON-RPC Call
            rpc_payload = {"jsonrpc": "2.0", "method": "turn.submit", "params": {"prompt": "Hello"}, "id": 1}
            res_rpc = await client.post("http://127.0.0.1:8888/rpc", json=rpc_payload)
            assert res_rpc.status_code == 200
            assert "result" in res_rpc.json()

            # 3. Sandbox Preview (Port 8889)
            res_sb = await client.get("http://127.0.0.1:8889/artifacts/preview.html")
            assert res_sb.status_code == 200
            assert "<h1>Generative UI Widget</h1>" in res_sb.text
            assert "postMessage" in res_sb.text  # Verified sandboxed bridge script is injected
    finally:
        await app_runner.cleanup()
        await sandbox_runner.cleanup()
