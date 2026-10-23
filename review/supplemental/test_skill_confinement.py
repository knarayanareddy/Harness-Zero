"""Additional release-blocking probes; original R1–R19 module remains unchanged."""
from __future__ import annotations

import os

import pytest

from harness.kernel.bootstrap import bootstrap_kernel
from harness.sdk.types import ToolCall


@pytest.mark.asyncio
@pytest.mark.parametrize("alias", ["absolute", "traversal", "symlink"])
async def test_R20_locked_view_skill_cannot_read_private_outside_skill(tmp_path, alias):
    workspace = tmp_path / "workspace"
    skills = workspace / "skills"
    skills.mkdir(parents=True)
    private = tmp_path / "private-skill"
    private.mkdir()
    canary = "DISTINCT_PRIVATE_SKILL_CONTENT_MUST_NOT_BE_READ"
    (private / "SKILL.md").write_text("# Private playbook\n" + canary + "\n")
    if alias == "absolute":
        name = str(private)
    elif alias == "traversal":
        name = os.path.relpath(private, skills)
    else:
        (skills / "outside-alias").symlink_to(private, target_is_directory=True)
        name = "outside-alias"
    assert canary not in name, "Distinguish echoed input from actual private content"
    runtime = await bootstrap_kernel(workspace=workspace, config_dir=tmp_path / "config",
        overrides={"kernel.security.preset": "locked"}, resume_session=False)
    try:
        result = await runtime.gate.execute(
            ToolCall(id="skill-confinement", name="view_skill", arguments={"skill_name": name}),
            session_id="session_000000000001",
        )
        assert canary not in result.output, "Locked skill lookup leaked an outside-file canary"
    finally:
        await runtime.registry.unload_all()
