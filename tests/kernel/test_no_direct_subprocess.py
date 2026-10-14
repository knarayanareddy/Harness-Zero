"""
AST scan verifying tool plugins do not import subprocess directly (§9.4).
All process execution must go through ctx.executor.
Exemptions are strictly limited to executor and bridge plugins.
"""
from __future__ import annotations

import ast
from pathlib import Path

# Subdirectories or files exempted from direct subprocess restriction
EXEMPTIONS = (
    "executors",
    "executor",
    "bridges",
    "bridge",
)


def _checks_direct_subprocess(file_path: Path) -> list[tuple[int, str]]:
    """Scans for import subprocess or asyncio.subprocess."""
    violations: list[tuple[int, str]] = []
    tree = ast.parse(file_path.read_text(encoding="utf-8"), filename=str(file_path))

    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            for alias in node.names:
                if alias.name == "subprocess" or alias.name.startswith("subprocess."):
                    violations.append((node.lineno, alias.name))
        elif isinstance(node, ast.ImportFrom):
            if node.module == "subprocess" or (node.module and node.module.startswith("subprocess.")):
                violations.append((node.lineno, node.module))
            elif node.module == "asyncio.subprocess":
                violations.append((node.lineno, node.module))

    return violations


def test_no_direct_subprocess_in_plugins():
    """Ensures plugins do not directly import subprocess outside explicit exemptions."""
    plugins_dir = Path(__file__).resolve().parents[2] / "harness" / "plugins"
    if not plugins_dir.exists():
        return

    violations = []
    for py_file in plugins_dir.rglob("*.py"):
        rel_parts = py_file.relative_to(plugins_dir).parts
        # Check exemptions
        is_exempt = any(exempt in rel_parts for exempt in EXEMPTIONS)
        if is_exempt:
            continue

        for lineno, mod in _checks_direct_subprocess(py_file):
            violations.append(f"{py_file.name}:{lineno} -> {mod}")

    assert not violations, (
        "Direct subprocess usage prohibited in plugins; use ctx.executor:\n"
        + "\n".join(violations)
    )
