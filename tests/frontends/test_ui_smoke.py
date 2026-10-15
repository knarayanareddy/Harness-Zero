"""
Headless UI smoke test integration with pytest (Step 4, §5).
Verifies in CI that Node.js / jsdom can boot the web frontend, connect SSE,
submit a turn, render assistant text and tool cards in the DOM, and interact with settings.
"""

import json
import os
import shutil
import subprocess
import sys
import time
from pathlib import Path

import httpx
import pytest


def get_mock_script(target_file: str) -> list[dict]:
    return [
        {
            "text": "Let me look at the file.",
            "tool_calls": [
                {
                    "id": "c1",
                    "name": "view_file",
                    "arguments": {
                        "file_path": target_file,
                    },
                }
            ],
            "stop_reason": "tool_use",
        },
        {
            "text": "The file contains hello world.",
            "stop_reason": "end_turn",
        },
    ]


@pytest.mark.asyncio
async def test_headless_ui_smoke(tmp_path: Path):
    """Start daemon with mock provider, execute headless UI smoke script via Node.js, and verify clean DOM rendering."""
    # Check if node is available
    node_bin = shutil.which("node")
    if not node_bin:
        pytest.skip("Node.js is not installed on this runner")

    # Check if jsdom is installed
    check_jsdom = subprocess.run(
        [node_bin, "-e", "import('jsdom')"],
        capture_output=True,
    )
    if check_jsdom.returncode != 0:
        pytest.skip("jsdom is not installed in node environment")

    # Setup temporary workspace and mock script
    ws = tmp_path / "smoke_ws"
    ws.mkdir(parents=True, exist_ok=True)
    (ws / "hello.txt").write_text("1 | hello world\n", encoding="utf-8")

    mock_json = tmp_path / "mock.json"
    mock_json.write_text(
        json.dumps(get_mock_script(str(ws / "hello.txt"))),
        encoding="utf-8",
    )

    cfg_dir = tmp_path / "cfg"
    cfg_dir.mkdir(parents=True, exist_ok=True)
    (cfg_dir / "settings.json").write_text(
        json.dumps({
            "schema_version": 1,
            "kernel": {
                "provider": "mock",
                "model": "mock-v1",
            },
        }),
        encoding="utf-8",
    )

    # Use ephemeral port
    port = 8791
    sandbox_port = 8792

    env = dict(os.environ)
    env["HOME"] = str(cfg_dir)
    env["HARNESS_CONFIG_DIR"] = str(cfg_dir)
    env["HARNESS_MOCK_SCRIPT"] = str(mock_json)

    proc = subprocess.Popen(
        [
            sys.executable,
            "-m",
            "harness.cli",
            "ui",
            "-w",
            str(ws),
            "-p",
            str(port),
            "-s",
            str(sandbox_port),
            "-h",
            "127.0.0.1",
            "--no-open",
        ],
        env=env,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
    )

    base_url = f"http://127.0.0.1:{port}"

    try:
        # Wait for daemon to become healthy
        healthy = False
        async with httpx.AsyncClient() as client:
            for _ in range(30):
                try:
                    res = await client.get(f"{base_url}/health", timeout=1.0)
                    if res.status_code == 200:
                        healthy = True
                        break
                except Exception:
                    time.sleep(0.2)

        assert healthy, "Daemon failed to start within timeout"

        # Execute ui_smoke.mjs
        smoke_script = Path(__file__).parent / "ui_smoke.mjs"
        smoke_env = dict(os.environ)
        smoke_env["BASE"] = base_url
        smoke_env["WAIT_MS"] = "6000"

        smoke_res = subprocess.run(
            [node_bin, str(smoke_script)],
            env=smoke_env,
            capture_output=True,
            text=True,
            timeout=20,
        )

        print("STDOUT:", smoke_res.stdout)
        print("STDERR:", smoke_res.stderr)
        assert smoke_res.returncode == 0, f"ui_smoke.mjs failed with code {smoke_res.returncode}"
        assert "✅ [UI Smoke Test Passed Successfully]" in smoke_res.stdout

    finally:
        proc.terminate()
        try:
            proc.wait(timeout=3)
        except subprocess.TimeoutExpired:
            proc.kill()
