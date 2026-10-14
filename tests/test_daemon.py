from pathlib import Path

import pytest
from aiohttp import web

from harness.artifacts.manager import ArtifactMetadata
from harness.core.coordinator import AgentCoordinator
from harness.core.daemon import HarnessDaemon
from harness.core.types import ModelResponse


@pytest.mark.asyncio
async def test_daemon_dual_port_endpoints(tmp_path: Path):
    coordinator = AgentCoordinator(workspace=tmp_path)
    coordinator.router.set_mock_responses([ModelResponse(content="Hello from mock")])
    coordinator.artifacts.write_artifact(
        "preview.html",
        "<h1>Generative UI Widget</h1>",
        ArtifactMetadata(summary="Preview demo widget"),
    )

    daemon = HarnessDaemon(coordinator=coordinator, port=0, sandbox_port=0)

    # Test API App
    app_runner = web.AppRunner(daemon.app)
    await app_runner.setup()
    api_site = web.TCPSite(app_runner, host="127.0.0.1", port=0)
    await api_site.start()
    api_port = api_site._server.sockets[0].getsockname()[1]
    daemon.port = api_port

    # Test Sandbox App
    sandbox_runner = web.AppRunner(daemon.sandbox_app)
    await sandbox_runner.setup()
    sandbox_site = web.TCPSite(sandbox_runner, host="127.0.0.1", port=0)
    await sandbox_site.start()
    sandbox_port = sandbox_site._server.sockets[0].getsockname()[1]
    daemon.sandbox_port = sandbox_port

    try:
        import httpx
        async with httpx.AsyncClient() as client:
            # 1. API Health
            res_api = await client.get(f"http://127.0.0.1:{api_port}/health")
            assert res_api.status_code == 200
            assert res_api.json()["status"] == "online"

            # 2. JSON-RPC Call
            rpc_payload = {"jsonrpc": "2.0", "method": "turn.submit", "params": {"prompt": "Hello"}, "id": 1}
            res_rpc = await client.post(f"http://127.0.0.1:{api_port}/rpc", json=rpc_payload)
            assert res_rpc.status_code == 200
            assert "result" in res_rpc.json()

            # 3. Sandbox Preview
            res_sb = await client.get(f"http://127.0.0.1:{sandbox_port}/artifacts/preview.html")
            assert res_sb.status_code == 200
            assert "<h1>Generative UI Widget</h1>" in res_sb.text
            assert "postMessage" in res_sb.text  # Verified sandboxed bridge script is injected
    finally:
        await app_runner.cleanup()
        await sandbox_runner.cleanup()
