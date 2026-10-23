"""Skill names never grant filesystem authority, including in locked mode."""
from __future__ import annotations

import os

import pytest

from harness.kernel.bootstrap import bootstrap_kernel
from harness.plugins.context.skills_catalog.source import discover_skills
from harness.sdk import skill_resources
from harness.sdk.types import ToolCall


@pytest.mark.asyncio
@pytest.mark.parametrize("alias", ["absolute", "traversal", "folder-link", "leaf-link", "root-link"])
async def test_locked_skill_reader_rejects_outside_canaries(tmp_path, alias):
    workspace = tmp_path / "workspace"
    skills = workspace / "skills"
    skills.mkdir(parents=True)
    private = tmp_path / "private-skill"
    private.mkdir()
    canary = "PRIVATE_SKILL_BYTES_NOT_PRESENT_IN_REQUEST"
    (private / "SKILL.md").write_text(canary)
    name = "alias"
    if alias == "absolute":
        name = str(private)
    elif alias == "traversal":
        name = os.path.relpath(private, skills)
    elif alias == "folder-link":
        (skills / name).symlink_to(private, target_is_directory=True)
    elif alias == "leaf-link":
        (skills / name).mkdir()
        (skills / name / "SKILL.md").symlink_to(private / "SKILL.md")
    else:
        skills.rmdir()
        skills.symlink_to(tmp_path, target_is_directory=True)
        name = private.name
    runtime = await bootstrap_kernel(workspace=workspace, config_dir=tmp_path / "config",
        overrides={"kernel.security.preset": "locked"}, resume_session=False)
    try:
        result = await runtime.gate.execute(
            ToolCall(id="skill-canary", name="view_skill", arguments={"skill_name": name}),
            session_id="session_000000000001",
        )
        assert canary not in result.output
        assert result.is_error
        assert canary not in str(discover_skills(workspace))
    finally:
        await runtime.registry.unload_all()


@pytest.mark.asyncio
async def test_locked_skill_aliases_and_builtin_still_work(tmp_path):
    workspace = tmp_path / "workspace"
    skill = workspace / ".harness/skills/Custom_Deployer"
    skill.mkdir(parents=True)
    content = "---\nname: Custom_Deployer\ndescription: Confined deployment guide\n---\n# Allowed playbook"
    (skill / "SKILL.md").write_text(content)
    runtime = await bootstrap_kernel(workspace=workspace, config_dir=tmp_path / "config",
        overrides={"kernel.security.preset": "locked"}, resume_session=False)
    try:
        for name in ("Custom_Deployer", "custom-deployer", "CUSTOM_DEPLOYER", "code-architect"):
            result = await runtime.gate.execute(
                ToolCall(id=f"positive-{name}", name="view_skill", arguments={"skill_name": name}),
                session_id="session_000000000001",
            )
            assert not result.is_error
            assert ("Code Architect Playbook" if name == "code-architect" else content) in result.output
        catalogue = discover_skills(workspace)
        assert any(s["name"] == "Custom_Deployer" and s["description"] == "Confined deployment guide" for s in catalogue)
        assert any(s["name"] == "code-architect" for s in catalogue)
    finally:
        await runtime.registry.unload_all()


@pytest.mark.parametrize("consumer", ["view", "catalogue"])
@pytest.mark.parametrize("boundary", ["workspace-before", "root-before", "folder-before", "leaf-before", "root-pinned", "folder-pinned"])
def test_skill_swaps_cannot_redirect_acquired_descriptors(tmp_path, monkeypatch, consumer, boundary):
    workspace = tmp_path / "workspace"
    root = workspace / "skills"
    folder = root / "alias"
    folder.mkdir(parents=True)
    inside = "SAFE_PINNED_SKILL_CONTENT"
    canary = "PRIVATE_SWAP_CONTENT_NOT_IN_NAME"
    (folder / "SKILL.md").write_text(inside)
    outside_workspace = tmp_path / "private-workspace"
    outside_root = outside_workspace / "skills"
    outside_folder = outside_root / "alias"
    outside_folder.mkdir(parents=True)
    (outside_folder / "SKILL.md").write_text(canary)
    real_open = skill_resources._open
    fired = False
    target = {"workspace-before": workspace, "root-before": root, "folder-before": folder,
        "leaf-before": folder / "SKILL.md", "root-pinned": root, "folder-pinned": folder}[boundary]
    parent_identity = target.parent.stat()

    def exchange(target, private):
        target.rename(target.with_name(target.name + "-held"))
        target.symlink_to(private, target_is_directory=target != folder / "SKILL.md")

    def swap_open(path, flags, *, dir_fd=None):
        nonlocal fired
        parent = os.fstat(dir_fd) if dir_fd is not None else None
        at_target = parent is not None and (parent.st_dev, parent.st_ino) == (parent_identity.st_dev, parent_identity.st_ino)
        before = {"workspace-before": "workspace", "root-before": "skills", "folder-before": "alias", "leaf-before": "SKILL.md"}
        if not fired and at_target and boundary in before and path == before[boundary]:
            fired = True
            target, private = {
                "workspace-before": (workspace, outside_workspace),
                "root-before": (root, outside_root),
                "folder-before": (folder, outside_folder),
                "leaf-before": (folder / "SKILL.md", outside_folder / "SKILL.md"),
            }[boundary]
            exchange(target, private)
        fd = real_open(path, flags, dir_fd=dir_fd)
        if not fired and at_target and boundary in {"root-pinned", "folder-pinned"} and path == ("skills" if boundary == "root-pinned" else "alias"):
            fired = True
            exchange(root if boundary == "root-pinned" else folder,
                outside_root if boundary == "root-pinned" else outside_folder)
        return fd

    monkeypatch.setattr(skill_resources, "_open", swap_open)
    if consumer == "view":
        result = skill_resources.read_skill("alias", workspace)
        assert result == (inside if boundary.endswith("pinned") else None)
    else:
        result = discover_skills(workspace)
        aliases = [entry for entry in result if entry["name"] == "alias"]
        assert aliases == ([{"name": "alias", "description": inside, "path": str(folder / "SKILL.md")}] if boundary.endswith("pinned") else [])
    assert fired, "The intended read/acquisition boundary must actually be exercised"
    assert canary not in str(result)


@pytest.mark.parametrize("kind", ["oversize", "invalid-utf8", "fifo", "directory"])
def test_skill_reader_rejects_nonregular_or_unbounded_content(tmp_path, kind):
    folder = tmp_path / "skills/invalid"
    folder.mkdir(parents=True)
    path = folder / "SKILL.md"
    if kind == "oversize":
        path.write_bytes(b"x" * (skill_resources.MAX_SKILL_BYTES + 1))
    elif kind == "invalid-utf8":
        path.write_bytes(b"\xff")
    elif kind == "fifo":
        os.mkfifo(path)
    else:
        path.mkdir()
    assert skill_resources.read_skill("invalid", tmp_path) is None
    assert all(s["name"] != "invalid" for s in discover_skills(tmp_path))


def test_skill_reader_closes_every_owned_descriptor(tmp_path, monkeypatch):
    folder = tmp_path / "skills/valid"
    folder.mkdir(parents=True)
    (folder / "SKILL.md").write_text("valid content")
    original = skill_resources._open
    fds = []

    def capture(*args, **kwargs):
        fd = original(*args, **kwargs)
        fds.append(fd)
        return fd

    monkeypatch.setattr(skill_resources, "_open", capture)
    assert skill_resources.read_skill("valid", tmp_path) == "valid content"
    assert any(s["name"] == "valid" for s in discover_skills(tmp_path))
    for fd in set(fds):
        with pytest.raises(OSError):
            os.fstat(fd)
