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
