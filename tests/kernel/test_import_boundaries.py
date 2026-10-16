"""
Architectural boundary enforcement via AST scans (§9.4).
Enforces:
1. harness/plugins/** imports ONLY harness.sdk (+ stdlib/third-party).
2. harness/sdk/** imports NO harness.* outside harness.sdk.
3. harness/kernel/** imports NO harness.plugins.*.
4. harness/frontends/** imports only harness.kernel.commands, harness.kernel.bus, harness.sdk.
"""
from __future__ import annotations

import ast
from pathlib import Path


def _get_imports(file_path: Path) -> list[tuple[int, str]]:
    """Returns a list of (line_number, imported_module) for a given python file."""
    imports: list[tuple[int, str]] = []
    tree = ast.parse(file_path.read_text(encoding="utf-8"), filename=str(file_path))

    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            for alias in node.names:
                imports.append((node.lineno, alias.name))
        elif isinstance(node, ast.ImportFrom):
            if node.module:
                imports.append((node.lineno, node.module))
            elif node.level > 0:
                # Relative import within same package
                pass

    return imports


def test_sdk_imports_no_external_harness_packages():
    """harness/sdk/** must never import any harness package outside harness.sdk."""
    sdk_dir = Path(__file__).resolve().parents[2] / "harness" / "sdk"
    assert sdk_dir.exists()

    violations = []
    for py_file in sdk_dir.rglob("*.py"):
        for lineno, mod in _get_imports(py_file):
            if mod == "harness" or (mod.startswith("harness.") and not mod.startswith("harness.sdk")):
                violations.append(f"{py_file.name}:{lineno} -> {mod}")

    assert not violations, (
        "SDK boundary violation: harness/sdk must not import outside harness.sdk:\n"
        + "\n".join(violations)
    )


def test_plugins_import_only_sdk():
    """harness/plugins/** must only import harness.sdk from the harness namespace."""
    plugins_dir = Path(__file__).resolve().parents[2] / "harness" / "plugins"
    if not plugins_dir.exists():
        return

    violations = []
    for py_file in plugins_dir.rglob("*.py"):
        for lineno, mod in _get_imports(py_file):
            if mod == "harness" or (mod.startswith("harness.") and not mod.startswith("harness.sdk")):
                violations.append(f"{py_file.name}:{lineno} -> {mod}")

    assert not violations, (
        "Plugin boundary violation: plugins must only import harness.sdk:\n"
        + "\n".join(violations)
    )


def test_kernel_imports_no_plugins():
    """harness/kernel/** must never import from harness.plugins.*."""
    kernel_dir = Path(__file__).resolve().parents[2] / "harness" / "kernel"
    assert kernel_dir.exists()

    violations = []
    for py_file in kernel_dir.rglob("*.py"):
        for lineno, mod in _get_imports(py_file):
            if mod.startswith("harness.plugins"):
                violations.append(f"{py_file.name}:{lineno} -> {mod}")

    assert not violations, (
        "Kernel boundary violation: kernel must not import harness.plugins:\n"
        + "\n".join(violations)
    )


def test_frontends_allowed_imports():
    """harness/frontends/** imports only harness.kernel.commands, harness.kernel.bus, harness.sdk."""
    frontends_dir = Path(__file__).resolve().parents[2] / "harness" / "frontends"
    if not frontends_dir.exists():
        return

    allowed_prefixes = (
        "harness.sdk",
        "harness.kernel.commands",
        "harness.kernel.bus",
    )

    violations = []
    for py_file in frontends_dir.rglob("*.py"):
        for lineno, mod in _get_imports(py_file):
            if mod.startswith("harness."):
                if not any(mod == p or mod.startswith(p + ".") for p in allowed_prefixes):
                    violations.append(f"{py_file.name}:{lineno} -> {mod}")

    assert not violations, (
        "Frontend boundary violation: frontends restricted to sdk, bus, commands:\n"
        + "\n".join(violations)
    )

def test_plugins_do_not_import_sibling_plugins():
    """No plugin imports another plugin's modules (WS-02 `boundaries`).

    Plugins compose through the registry — a tool, a policy, a store — never by importing a sibling.
    A sibling import means the two can no longer be enabled, disabled or shipped independently, which
    is the entire point of the plugin tree.
    """
    root = Path(__file__).resolve().parents[2]
    plugins_dir = root / "harness" / "plugins"
    assert plugins_dir.exists()

    packages = set()
    for toml in plugins_dir.glob("*/*/harness_plugin.toml"):
        rel = toml.parent.relative_to(plugins_dir)
        packages.add("harness.plugins." + ".".join(rel.parts))

    offenders: list[str] = []
    for py_file in sorted(plugins_dir.rglob("*.py")):
        if "__pycache__" in py_file.parts:
            continue
        rel = py_file.relative_to(plugins_dir)
        own = "harness.plugins." + ".".join(rel.parts[:-1])
        for lineno, mod in _get_imports(py_file):
            target = ".".join(mod.split(".")[:4])
            if target in packages and target != own:
                offenders.append(f"{py_file.name}:{lineno} -> {mod} (plugin {'.'.join(Path(target).parts[2:4])})")

    assert not offenders, "plugin-to-plugin import (compose through the registry instead):\n" + "\n".join(sorted(set(offenders)))
