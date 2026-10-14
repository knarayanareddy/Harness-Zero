from pathlib import Path

import pytest
from aiohttp import web

from harness.core.coordinator import AgentCoordinator
from harness.core.daemon import HarnessDaemon
from harness.models.router import ModelRouter


@pytest.mark.asyncio
async def test_no_home_writes(tmp_path: Path):
    # Ensure home directory has no .harness
    real_home_harness = Path.home() / ".harness"
    assert not real_home_harness.exists()

    # Instantiating router and coordinator must not create ~/.harness
    router = ModelRouter()
    coordinator = AgentCoordinator(workspace=tmp_path / "ws", router=router)
    assert not real_home_harness.exists()

    # Posting settings without api_keys or provider/model must not touch ~/.harness
    daemon = HarnessDaemon(coordinator=coordinator, host="127.0.0.1", port=0, sandbox_port=0)

    app_runner = web.AppRunner(daemon.app)
    await app_runner.setup()
    site = web.TCPSite(app_runner, host="127.0.0.1", port=0)
    await site.start()
    port = site._server.sockets[0].getsockname()[1]

    try:
        import httpx
        async with httpx.AsyncClient() as client:
            res = await client.post(
                f"http://127.0.0.1:{port}/settings",
                json={
                    "system": {"device_name": "test-box"},
                    "time_focus": {"focus_interval_minutes": 25},
                },
            )
            assert res.status_code == 200
            assert not real_home_harness.exists()
    finally:
        await app_runner.cleanup()
