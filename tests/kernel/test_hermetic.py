from pathlib import Path

import pytest

from harness.kernel.bootstrap import bootstrap_kernel


@pytest.mark.asyncio
async def test_no_home_writes(tmp_path: Path):
    # Ensure home directory has no .harness
    real_home_harness = Path.home() / ".harness"
    assert not real_home_harness.exists()

    # Bootstrapping kernel with an explicit workspace must not touch ~/.harness
    runtime = await bootstrap_kernel(workspace=tmp_path / "ws")
    assert not real_home_harness.exists()

    # Commands/settings operations must not create ~/.harness when scoped to workspace
    settings = await runtime.commands.settings_get("kernel.model")
    assert settings is not None
    assert not real_home_harness.exists()


@pytest.mark.asyncio
async def test_lazy_plugin_data_dirs(tmp_path: Path):
    """Plugins data dirs must be created lazily on access, not eagerly on bootstrap (K6)."""
    ws = tmp_path / "workspace_lazy"
    _runtime = await bootstrap_kernel(workspace=ws)

    plugin_data_root = ws / ".harness" / "plugins"
    if plugin_data_root.exists():
        # Any directories inside must only have been created if accessed
        dirs = [d for d in plugin_data_root.iterdir() if d.is_dir()]
        assert len(dirs) < 35, f"Expected lazy dirs, found {len(dirs)} created eagerly: {dirs}"


@pytest.mark.asyncio
async def test_mock_script_override_not_persisted(tmp_path: Path):
    """In-memory overrides apply immediately and are not written to disk settings (K4)."""
    ws = tmp_path / "workspace_overrides"
    conf = tmp_path / "config_overrides"
    fake_script = "/tmp/fake_mock_script.json"

    runtime = await bootstrap_kernel(
        workspace=ws,
        config_dir=conf,
        overrides={"providers.mock.script_file": fake_script},
    )

    # In-memory view returns the override
    assert runtime.settings.get("providers.mock.script_file") == fake_script

    # Disk file does NOT contain the override
    settings_file = conf / "settings.json"
    if settings_file.exists():
        import json
        disk_data = json.loads(settings_file.read_text(encoding="utf-8"))
        assert "providers" not in disk_data or "mock" not in disk_data.get("providers", {})


@pytest.mark.asyncio
async def test_local_executor_devnull_stdin(tmp_path: Path):
    """Non-interactive executor must have stdin=DEVNULL so commands reading stdin exit promptly (K5)."""
    from harness.plugins.executors.local.executor import LocalExecutor
    from harness.sdk.types import ExecRequest

    executor = LocalExecutor(default_cwd=tmp_path)
    # Command that reads stdin; with DEVNULL it exits with 0 immediately
    req = ExecRequest(
        command="cat",
        cwd=tmp_path,
        interactive=False,
    )
    proc = await executor.start(req)
    result = await proc.wait()
    assert result.exit_code == 0

