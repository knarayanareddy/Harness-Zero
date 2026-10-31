"""Exercise the PyInstaller recipe without requiring PyInstaller or a native build."""
from __future__ import annotations

import runpy
import shutil
from pathlib import Path
from types import SimpleNamespace
from typing import Any

import pytest

ROOT = Path(__file__).resolve().parents[2]
SPEC = ROOT / "packaging" / "macos" / "harness-engine.spec"
BUILTIN_SKILLS = {
    "code-architect",
    "deep-research",
    "desktop-automator",
    "test-evaluator",
}


def _run_spec(spec_path: Path) -> tuple[dict[str, Any], list[dict[str, Any]]]:
    analyses: list[dict[str, Any]] = []

    def analysis(*args: object, **kwargs: object) -> SimpleNamespace:
        analyses.append(kwargs)
        return SimpleNamespace(pure=[], scripts=[], binaries=[], datas=kwargs["datas"])

    runpy.run_path(
        str(spec_path),
        init_globals={
            "SPECPATH": str(spec_path.parent),
            "Analysis": analysis,
            "PYZ": lambda *args: None,
            "EXE": lambda *args, **kwargs: None,
        },
    )
    assert len(analyses) == 1
    return analyses[0], analyses


def _make_repository(root: Path, *, include_ui_manifest: bool) -> Path:
    """Create a small, owned recipe fixture; never remove/rename source inputs."""
    spec_path = root / "packaging" / "macos" / "harness-engine.spec"
    spec_path.parent.mkdir(parents=True)
    shutil.copyfile(SPEC, spec_path)

    web = root / "harness" / "frontends" / "web"
    (web / "static").mkdir(parents=True)
    (web / "static" / "app.js").write_text("// fixture client\n", encoding="utf-8")
    if include_ui_manifest:
        (web / "ui.toml").write_text("[ui]\npanels = []\n", encoding="utf-8")

    plugin = root / "harness" / "plugins" / "fixture"
    plugin.mkdir(parents=True)
    (plugin / "__init__.py").write_text("", encoding="utf-8")
    (plugin / "plugin.py").write_text("VALUE = True\n", encoding="utf-8")
    (root / "harness" / "engine_main.py").write_text("", encoding="utf-8")

    for name in sorted(BUILTIN_SKILLS):
        skill = root / "builtin" / "skills" / name / "SKILL.md"
        skill.parent.mkdir(parents=True)
        skill.write_text(f"---\nname: {name}\n---\n", encoding="utf-8")
    return spec_path


def _data_pairs(analysis: dict[str, Any]) -> set[tuple[str, str]]:
    return {(str(source), str(destination)) for source, destination in analysis["datas"]}


def test_frozen_recipe_adds_core_ui_manifest_and_keeps_real_resources() -> None:
    analysis, _ = _run_spec(SPEC)
    datas = _data_pairs(analysis)

    expected = {
        (str(ROOT / "harness/frontends/web/ui.toml"), "harness/frontends/web"),
        (str(ROOT / "harness/frontends/web/static"), "harness/frontends/web/static"),
        (str(ROOT / "harness/plugins"), "harness/plugins"),
        (str(ROOT / "builtin"), "builtin"),
    }
    assert expected <= datas, datas
    assert (ROOT / "harness/frontends/web/ui.toml").is_file()
    assert (ROOT / "harness/frontends/web/static/app.js").is_file()
    assert list((ROOT / "harness/plugins").rglob("*.py")), "plugin source inputs are missing"
    skill_metadata = {
        path.parent.name
        for path in (ROOT / "builtin" / "skills").glob("*/SKILL.md")
    }
    assert skill_metadata == BUILTIN_SKILLS, skill_metadata
    assert analysis["hiddenimports"], "plugin hidden-import discovery must remain enabled"


@pytest.mark.parametrize("ui_path_kind", ["missing", "directory"])
def test_frozen_recipe_rejects_missing_or_nonfile_ui_manifest_before_analysis(
    tmp_path: Path, ui_path_kind: str
) -> None:
    spec_path = _make_repository(tmp_path / "missing-ui", include_ui_manifest=False)
    root = spec_path.parents[2]
    ui_manifest = root / "harness/frontends/web/ui.toml"
    if ui_path_kind == "directory":
        ui_manifest.mkdir()
    assert (root / "harness/frontends/web/static/app.js").is_file()
    assert list((root / "harness/plugins").rglob("*.py"))
    assert len(list((root / "builtin/skills").glob("*/SKILL.md"))) == 4

    analyses: list[dict[str, Any]] = []

    def analysis(*args: object, **kwargs: object) -> SimpleNamespace:
        analyses.append(kwargs)
        return SimpleNamespace(pure=[], scripts=[], binaries=[], datas=kwargs["datas"])

    with pytest.raises(FileNotFoundError, match="ui.toml"):
        runpy.run_path(
            str(spec_path),
            init_globals={
                "SPECPATH": str(spec_path.parent),
                "Analysis": analysis,
                "PYZ": lambda *args: None,
                "EXE": lambda *args, **kwargs: None,
            },
        )
    assert not analyses, "a missing required input must be rejected before PyInstaller Analysis"


def test_frozen_recipe_accepts_complete_isolated_repository_fixture(tmp_path: Path) -> None:
    spec_path = _make_repository(tmp_path / "complete", include_ui_manifest=True)
    analysis, _ = _run_spec(spec_path)
    root = spec_path.parents[2]
    datas = _data_pairs(analysis)

    assert (str(root / "harness/frontends/web/ui.toml"), "harness/frontends/web") in datas
    assert (str(root / "harness/frontends/web/static"), "harness/frontends/web/static") in datas
    assert (str(root / "harness/plugins"), "harness/plugins") in datas
    assert (str(root / "builtin"), "builtin") in datas
    assert "harness.plugins.fixture.plugin" in analysis["hiddenimports"]
    assert {path.parent.name for path in (root / "builtin/skills").glob("*/SKILL.md")} == BUILTIN_SKILLS
