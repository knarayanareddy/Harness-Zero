"""
Tests for SDK-02: harness plugin test and harness plugin doctor.
Validates boundary reporting, capability checks, manifest linting, and CLI conformance (§11, S15).
"""
from __future__ import annotations

from pathlib import Path

from click.testing import CliRunner

from harness.cli import main
from harness.sdk.doctor import diagnose_plugin
from harness.sdk.scaffold import scaffold_plugin


def test_diagnose_valid_scaffolded_plugin(tmp_path: Path):
    """A freshly scaffolded plugin passes all doctor checks with zero failures."""
    plugin_dir = tmp_path / "valid-tool"
    scaffold_plugin("valid-tool", "tool", target_dir=plugin_dir)

    report = diagnose_plugin(plugin_dir)
    assert report.passed is True
    assert report.plugin_name == "valid-tool"

    categories = {c.category for c in report.checks}
    assert "manifest" in categories
    assert "schema" in categories
    assert "boundaries" in categories
    assert "deprecations" in categories


def test_diagnose_missing_manifest(tmp_path: Path):
    """Reports failure when harness_plugin.toml is absent."""
    empty_dir = tmp_path / "empty"
    empty_dir.mkdir()

    report = diagnose_plugin(empty_dir)
    assert report.passed is False
    assert any(c.status == "FAIL" and "Missing manifest" in c.message for c in report.checks)


def test_diagnose_boundary_violations(tmp_path: Path):
    """Detects forbidden imports outside harness.sdk."""
    bad_dir = tmp_path / "bad-boundary"
    scaffold_plugin("bad-boundary", "context", target_dir=bad_dir)

    # Inject boundary violation into plugin.py
    code = (bad_dir / "plugin.py").read_text(encoding="utf-8")
    code = "import harness.kernel.bus\n" + code
    (bad_dir / "plugin.py").write_text(code, encoding="utf-8")

    report = diagnose_plugin(bad_dir)
    assert report.passed is False
    assert any("Plugins must only import harness.sdk" in c.message for c in report.checks)


def test_diagnose_direct_subprocess_violation(tmp_path: Path):
    """Detects direct subprocess imports."""
    sub_dir = tmp_path / "sub-violator"
    scaffold_plugin("sub-violator", "tool", target_dir=sub_dir)

    code = (sub_dir / "plugin.py").read_text(encoding="utf-8")
    code = "import subprocess\n" + code
    (sub_dir / "plugin.py").write_text(code, encoding="utf-8")

    report = diagnose_plugin(sub_dir)
    assert report.passed is False
    assert any("Direct subprocess import forbidden" in c.message for c in report.checks)


def test_diagnose_capability_under_declaration(tmp_path: Path):
    """Detects usage of ctx.secrets when secrets capability is not declared."""
    cap_dir = tmp_path / "cap-violator"
    scaffold_plugin("cap-violator", "context", target_dir=cap_dir)

    # Access ctx.secrets without declaring capability
    code = (cap_dir / "plugin.py").read_text(encoding="utf-8")
    code += "\ndef dummy(ctx):\n    return ctx.secrets.get('KEY')\n"
    (cap_dir / "plugin.py").write_text(code, encoding="utf-8")

    report = diagnose_plugin(cap_dir)
    assert report.passed is False
    assert any("lacks 'secrets' capability" in c.message for c in report.checks)


def test_cli_plugin_doctor_all():
    """harness plugin doctor --all checks all 35 builtin plugins and passes."""
    runner = CliRunner()
    res = runner.invoke(main, ["plugin", "doctor", "--all"])
    assert res.exit_code == 0
    assert "Plugin doctor checks passed cleanly" in res.output


def test_cli_plugin_doctor_target_failure(tmp_path: Path):
    """harness plugin doctor returns exit code 1 when plugin has errors."""
    bad_dir = tmp_path / "broken-plugin"
    bad_dir.mkdir()
    (bad_dir / "harness_plugin.toml").write_text("invalid = toml :::", encoding="utf-8")

    runner = CliRunner()
    res = runner.invoke(main, ["plugin", "doctor", str(bad_dir)])
    assert res.exit_code == 1
    assert "Plugin doctor detected errors" in res.output


def test_cli_plugin_test_target(tmp_path: Path):
    """harness plugin test <target> executes conformance tests on scaffolded plugin."""
    p_dir = tmp_path / "tested-plugin"
    scaffold_plugin("tested-plugin", "context", target_dir=p_dir)

    runner = CliRunner()
    res = runner.invoke(main, ["plugin", "test", str(p_dir)])
    assert res.exit_code == 0
    assert "All plugin conformance tests passed" in res.output
