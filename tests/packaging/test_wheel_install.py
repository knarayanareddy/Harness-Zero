"""Prove builtin resources survive wheel and sdist distribution boundaries."""
from __future__ import annotations

import hashlib
import json
import os
import subprocess
import sys
import sysconfig
import tarfile
import tomllib
import zipfile
from pathlib import Path

from packaging.requirements import Requirement
from packaging.utils import canonicalize_name

ROOT = Path(__file__).resolve().parents[2]
BUILTIN_SKILLS = {
    "code-architect": "24732833afc2a27bcfef6a1ae22278fc038ae3fc00ab9d36dc28d59de96e9991",
    "deep-research": "c3859868108a0a263941244a52f4bbffd39a088d08d5df02dabfcfd2442b135c",
    "desktop-automator": "3a393ff0fa8523eb9efe40e52395768c59cec2194f88a4e034da527c1cabbbcd",
    "test-evaluator": "8bbf2441d2277526d04c6a155efb80b956db4bb0e31a1ec017e4076673e1b192",
}


def _run(
    command: list[str], *, cwd: Path, timeout: int = 120, env: dict[str, str] | None = None
) -> subprocess.CompletedProcess[str]:
    result = subprocess.run(command, cwd=cwd, env=env, capture_output=True, text=True, timeout=timeout, check=False)
    shown = list(command)
    if "-c" in shown:
        shown[shown.index("-c") + 1] = "<installed-artifact-probe>"
    display = " ".join(shown)
    print(f"$ {display}\n{result.stdout}{result.stderr}")
    assert result.returncode == 0, f"command failed ({result.returncode}): {display}"
    return result


def _resource_files() -> dict[Path, bytes]:
    """Resolve data files that the kernel's actual entry point consumes."""
    skill_root = ROOT / "builtin" / "skills"
    skills = {path.parent.name: path for path in skill_root.glob("*/SKILL.md")}
    assert set(skills) == set(BUILTIN_SKILLS), f"required source skills missing/changed: {sorted(skills)}"
    for name, expected in BUILTIN_SKILLS.items():
        actual = hashlib.sha256(skills[name].read_bytes()).hexdigest()
        assert actual == expected, f"original {name} bytes changed: {actual}"

    manifests = sorted((ROOT / "harness" / "plugins").rglob("harness_plugin.toml"))
    assert manifests, "the CLI's builtin plugin manifests are required package inputs"
    resources = {Path("harness/frontends/web/ui.toml")}
    resources.update(
        path.relative_to(ROOT)
        for path in (ROOT / "harness/frontends/web/static").rglob("*")
        if path.is_file()
    )
    for manifest_path in manifests:
        resources.add(manifest_path.relative_to(ROOT))
        manifest = tomllib.loads(manifest_path.read_text(encoding="utf-8"))
        plugin = manifest.get("plugin", {})
        for key in ("settings_schema",):
            value = plugin.get(key)
            if value:
                resources.add((manifest_path.parent / value).relative_to(ROOT))
        module = manifest.get("ui", {}).get("client_module")
        if module:
            resources.add((manifest_path.parent / module).relative_to(ROOT))

    files: dict[Path, bytes] = {}
    for relative in sorted(resources):
        source = ROOT / relative
        assert source.is_file(), f"entrypoint-referenced resource is missing: {relative}"
        files[relative] = source.read_bytes()
    return files


def _assert_wheel(wheel: Path, resources: dict[Path, bytes]) -> None:
    with zipfile.ZipFile(wheel) as archive:
        names = set(archive.namelist())
        skill_inventory = sorted(name for name in names if name.endswith("/SKILL.md"))
        expected_skills = sorted(f"harness/builtin/skills/{name}/SKILL.md" for name in BUILTIN_SKILLS)
        assert skill_inventory == expected_skills, skill_inventory
        for name, expected_sha in BUILTIN_SKILLS.items():
            member = f"harness/builtin/skills/{name}/SKILL.md"
            content = archive.read(member)
            actual_sha = hashlib.sha256(content).hexdigest()
            assert actual_sha == expected_sha, f"{wheel.name}:{member} sha256={actual_sha}"
            print(f"WHEEL_SKILL {wheel.name} {member} sha256={actual_sha} bytes={len(content)}")
        for relative, expected in resources.items():
            member = relative.as_posix()
            assert member in names, f"{wheel.name} missing entrypoint resource {member}"
            assert archive.read(member) == expected, f"{wheel.name} changed entrypoint resource bytes: {member}"
    print(f"WHEEL_INVENTORY {wheel.name} skill_count={len(skill_inventory)} resource_count={len(resources)}")


def _installed_artifact_probe(wheel: Path, target: Path, work: Path) -> None:
    target.mkdir()
    work.mkdir()
    env = os.environ.copy()
    env.pop("PYTHONPATH", None)
    env["HOME"] = str(work / "home")
    env["HARNESS_CONFIG_DIR"] = str(work / "config")
    _run(
        [
            sys.executable,
            "-m",
            "pip",
            "install",
            "--no-index",
            "--no-deps",
            "--target",
            str(target),
            str(wheel),
        ],
        cwd=work,
        timeout=60,
    )

    expected = dict(BUILTIN_SKILLS)
    dependencies = sorted({sysconfig.get_paths()[key] for key in ("purelib", "platlib")})
    source_roots = {ROOT.resolve()}
    for entry in sys.path:
        candidate = Path(entry or os.getcwd()).resolve()
        if (candidate / "pyproject.toml").is_file() and (candidate / "harness" / "__init__.py").is_file():
            source_roots.add(candidate)
    script = r'''
import asyncio
import hashlib
import json
import os
from pathlib import Path
import sys

artifact_root = Path(sys.argv[1]).resolve()
source_roots = [Path(path).resolve() for path in json.loads(sys.argv[2])]
expected = json.loads(sys.argv[3])
for dependency_path in reversed(json.loads(sys.argv[4])):
    sys.path.insert(0, dependency_path)
sys.path.insert(0, str(artifact_root))
for entry in sys.path:
    path = Path(entry or os.getcwd()).resolve()
    assert path not in source_roots, (path, source_roots)
    assert not ((path / "pyproject.toml").is_file() and (path / "harness" / "__init__.py").is_file()), path

import harness
from harness.kernel.bootstrap import bootstrap_kernel
from harness.kernel.plugin_paths import client_module_file
from harness.plugins.context.skills_catalog.source import discover_skills
from harness.sdk import skill_resources
from harness.sdk.types import ToolCall

package_root = Path(harness.__file__).resolve().parent
assert package_root == artifact_root / "harness", (package_root, artifact_root)
def assert_no_source_harness():
    bad = {}
    for name, module in tuple(sys.modules.items()):
        if name == "harness" or name.startswith("harness.") or name.startswith("harness_plugin__"):
            filename = getattr(module, "__file__", None)
            if filename and not Path(filename).resolve().is_relative_to(package_root):
                bad[name] = filename
    assert not bad, bad

async def probe():
    workspace = Path(sys.argv[5])
    workspace.mkdir(parents=True)
    assert skill_resources.skill_roots(workspace)[0] == package_root / "builtin" / "skills"
    discovered_skills = discover_skills(workspace)
    assert {entry["name"] for entry in discovered_skills} == set(expected), discovered_skills

    runtime = await bootstrap_kernel(
        workspace=workspace,
        config_dir=workspace.parent / "config",
        overrides={"kernel.security.preset": "locked"},
        resume_session=False,
    )
    try:
        package_manifests = set((package_root / "plugins").rglob("harness_plugin.toml"))
        discovered_manifests = {entry.manifest_path.resolve() for entry in runtime.registry.discovered.values() if entry.manifest_path}
        assert discovered_manifests == {path.resolve() for path in package_manifests}
        contributions = runtime.registry.ui_contributions()
        assert any(item["plugin"] == "web" for item in contributions["panels"])
        for name, entry in runtime.registry.discovered.items():
            module = getattr(entry.manifest.ui, "client_module", None) if entry.manifest.ui else None
            if module:
                tail = module.removeprefix("static/")
                resolved = client_module_file(entry, name, tail)
                assert resolved is not None and resolved.is_file(), (name, module)
                assert resolved.resolve().is_relative_to(package_root), resolved

        for name, digest in expected.items():
            content = skill_resources.read_skill(name, workspace)
            assert content is not None
            assert hashlib.sha256(content.encode("utf-8")).hexdigest() == digest
            result = await runtime.gate.execute(
                ToolCall(id=f"view-{name}", name="view_skill", arguments={"skill_name": name}),
                session_id="session_000000000001",
            )
            assert not result.is_error and content in result.output, (name, result.output)

        private = workspace.parent / "private-skill"
        private.mkdir()
        (private / "SKILL.md").write_text(
            "---\nname: outside-canary\ndescription: PRIVATE_SKILL_BYTES_NOT_PRESENT_IN_REQUEST\n---\nsecret",
            encoding="utf-8",
        )
        skills_root = workspace / "skills"
        skills_root.mkdir()
        probes = [str(private), os.path.relpath(private, skills_root)]
        (skills_root / "folder-link").symlink_to(private, target_is_directory=True)
        probes.append("folder-link")
        (skills_root / "leaf-link").mkdir()
        (skills_root / "leaf-link" / "SKILL.md").symlink_to(private / "SKILL.md")
        probes.append("leaf-link")
        for index, name in enumerate(probes):
            result = await runtime.gate.execute(
                ToolCall(id=f"negative-{index}", name="view_skill", arguments={"skill_name": name}),
                session_id="session_000000000001",
            )
            assert result.is_error and "PRIVATE_SKILL_BYTES_NOT_PRESENT_IN_REQUEST" not in result.output
            assert all(entry["name"] != "outside-canary" for entry in discover_skills(workspace))
        print("SDK_BUILTIN_ROOT", skill_resources.skill_roots(workspace)[0])
        print("INSTALLED_DISCOVERY", sorted(entry["name"] for entry in discovered_skills))
        ui_modules = sum(
            bool(entry.manifest.ui and entry.manifest.ui.client_module)
            for entry in runtime.registry.discovered.values()
        )
        print("INSTALLED_PLUGIN_MANIFESTS", len(discovered_manifests), "UI_CLIENT_MODULES", ui_modules)
    finally:
        await runtime.registry.unload_all()

asyncio.run(probe())
from click.testing import CliRunner
from harness.cli import main
cli_workspace = Path(sys.argv[5]).parent / "cli-workspace"
cli_workspace.mkdir()
result = CliRunner().invoke(main, ["run", "offline artifact turn", "--provider", "mock", "--workspace", str(cli_workspace)])
assert result.exit_code == 0, (result.output, result.exception)
assert "[mock] offline artifact turn" in result.output, result.output
print("CLI_KERNEL_TURN", result.output)
assert_no_source_harness()
'''
    probe_workspace = work / "probe-workspace"
    _run(
        [
            sys.executable,
            "-I",
            "-S",
            "-c",
            script,
            str(target),
            json.dumps(sorted(str(path) for path in source_roots)),
            json.dumps(expected),
            json.dumps(dependencies),
            str(probe_workspace),
        ],
        cwd=work,
        env=env,
        timeout=45,
    )


def test_dev_extra_declares_artifact_test_build_tools() -> None:
    project = tomllib.loads((ROOT / "pyproject.toml").read_text())
    declared = {canonicalize_name(Requirement(requirement).name) for requirement in project["project"]["optional-dependencies"]["dev"]}
    assert {"build", "hatchling"} <= declared


def test_noneditable_wheel_and_sdist_rebuilt_wheel_resources(tmp_path: Path) -> None:
    """Install real artifacts outside the checkout and exercise their resource consumers."""
    resources = _resource_files()
    wheel_dir = tmp_path / "direct-wheel"
    sdist_dir = tmp_path / "source-dist"
    rebuilt_dir = tmp_path / "rebuilt-wheel"
    for directory in (wheel_dir, sdist_dir, rebuilt_dir):
        directory.mkdir()

    _run([sys.executable, "-m", "build", "--wheel", "--no-isolation", "--outdir", str(wheel_dir)], cwd=ROOT)
    _run([sys.executable, "-m", "build", "--sdist", "--no-isolation", "--outdir", str(sdist_dir)], cwd=ROOT)
    wheel = next(wheel_dir.glob("*.whl"))
    sdist = next(sdist_dir.glob("*.tar.gz"))

    with tarfile.open(sdist) as archive:
        expected_members = sorted(f"builtin/skills/{name}/SKILL.md" for name in BUILTIN_SKILLS)
        members = sorted(
            member.name.removeprefix(f"{archive.getnames()[0].split('/')[0]}/")
            for member in archive.getmembers()
            if member.isfile() and member.name.endswith("/SKILL.md")
        )
        assert members == expected_members, members
        for name, expected_sha in BUILTIN_SKILLS.items():
            member = f"{sdist.name.removesuffix('.tar.gz')}/builtin/skills/{name}/SKILL.md"
            content = archive.extractfile(member)
            assert content is not None
            actual_sha = hashlib.sha256(content.read()).hexdigest()
            assert actual_sha == expected_sha, (member, actual_sha)
            print(f"SDIST_SKILL {member} sha256={actual_sha}")

    _run(
        [sys.executable, "-m", "build", "--wheel", "--no-isolation", "--outdir", str(rebuilt_dir), str(sdist)],
        cwd=tmp_path,
    )
    rebuilt_wheel = next(rebuilt_dir.glob("*.whl"))
    _assert_wheel(wheel, resources)
    _assert_wheel(rebuilt_wheel, resources)

    _installed_artifact_probe(wheel, tmp_path / "installed-direct", tmp_path / "outside-direct")
    _installed_artifact_probe(rebuilt_wheel, tmp_path / "installed-rebuilt", tmp_path / "outside-rebuilt")
