"""
Tests for SDK-01: harness plugin new scaffolder and templates.
Verifies manifest generation, conformance tests, and Acceptance S18 (§11).
"""
from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path

import pytest
from click.testing import CliRunner

from harness.cli import main
from harness.sdk.manifest import PluginManifest
from harness.sdk.scaffold import scaffold_plugin


@pytest.mark.parametrize(
    "kind",
    ["tool", "context", "provider", "policy", "executor", "strategy", "hook", "store"],
)
def test_scaffold_all_kinds(kind: str, tmp_path: Path):
    """Scaffolds every supported component kind and validates files & manifest."""
    name = f"my-{kind}-plugin"
    out_dir = tmp_path / name
    res = scaffold_plugin(name=name, kind=kind, target_dir=out_dir, summary=f"Test {kind} plugin")

    assert res == out_dir
    assert (out_dir / "harness_plugin.toml").exists()
    assert (out_dir / "settings.schema.json").exists()
    assert (out_dir / "plugin.py").exists()
    assert (out_dir / "README.md").exists()
    assert (out_dir / "tests" / "test_conformance.py").exists()

    # Manifest must parse without errors
    manifest = PluginManifest.load(out_dir)
    assert manifest.name == name
    assert manifest.version == "0.1.0"
    assert manifest.api_version == "1"
    assert manifest.provides == [kind]
    assert manifest.settings_schema == "settings.schema.json"

    # Schema must parse as valid json
    schema = json.loads((out_dir / "settings.schema.json").read_text(encoding="utf-8"))
    assert schema["type"] == "object"

    # Conformance test must pass under pytest
    run_res = subprocess.run(
        [sys.executable, "-m", "pytest", "-q", str(out_dir / "tests" / "test_conformance.py")],
        capture_output=True,
        text=True,
        cwd=str(out_dir),
    )
    assert run_res.returncode == 0, f"Conformance test failed for {kind}:\n{run_res.stderr}\n{run_res.stdout}"


def test_scaffold_acceptance_s18(tmp_path: Path):
    """
    Acceptance S18:
    harness plugin new --kind context my_rules
    implement a 10-line ContextSource
    conformance tests pass!
    """
    runner = CliRunner()
    target_dir = tmp_path / "my_rules"
    res = runner.invoke(
        main,
        ["plugin", "new", "my-rules", "--kind", "context", "--dir", str(target_dir)],
    )
    assert res.exit_code == 0
    assert "Successfully scaffolded" in res.output

    # Custom implementation
    custom_plugin = """from __future__ import annotations
from typing import Any
from harness.sdk.contracts import ContextSource
from harness.sdk.types import ContextBlock, Stability
from harness.sdk.context import PluginContext

class CustomRulesContextSource(ContextSource):
    name = "my-rules"
    async def blocks(self, ctx: Any) -> list[ContextBlock]:
        return [
            ContextBlock(id="my-rules:custom", stability=Stability.SESSION, text="Rule 1: Always verify.")
        ]

class MyRulesPlugin:
    def __init__(self, manifest=None):
        self.manifest = manifest
    async def setup(self, ctx: PluginContext) -> None:
        ctx.register_context_source(CustomRulesContextSource())
    async def teardown(self) -> None:
        pass
"""
    (target_dir / "plugin.py").write_text(custom_plugin, encoding="utf-8")

    custom_test = """import pytest
from harness.sdk.testing import ContextKit
from plugin import CustomRulesContextSource

@pytest.mark.asyncio
async def test_conformance():
    kit = ContextKit()
    source = CustomRulesContextSource()
    await kit.test_block_ids_unique(source, ctx=None)
    await kit.test_static_blocks_byte_identical(source, ctx=None)
"""
    (target_dir / "tests" / "test_conformance.py").write_text(custom_test, encoding="utf-8")

    # Run conformance test
    test_res = subprocess.run(
        [sys.executable, "-m", "pytest", "-q", str(target_dir / "tests" / "test_conformance.py")],
        capture_output=True,
        text=True,
        cwd=str(target_dir),
    )
    assert test_res.returncode == 0, f"S18 test failed:\n{test_res.stdout}\n{test_res.stderr}"


def test_scaffold_invalid_name_or_kind(tmp_path: Path):
    """Rejects invalid plugin names and unsupported kinds."""
    with pytest.raises(ValueError, match="Invalid plugin name"):
        scaffold_plugin(name="123_invalid", kind="tool", target_dir=tmp_path / "bad")

    with pytest.raises(ValueError, match="Invalid plugin kind"):
        scaffold_plugin(name="good-name", kind="non_existent", target_dir=tmp_path / "bad")
