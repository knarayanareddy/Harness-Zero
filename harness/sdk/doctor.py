"""
Plugin Doctor & Conformance Diagnostic Engine for Harness-Zero SDK.
Validates plugin manifests, architectural boundaries (AST checks),
capabilities, deprecations, and schema conformance (§11, SDK-02).
"""
from __future__ import annotations

import ast
import json
import tomllib
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Literal

from harness.sdk.manifest import NAME_PATTERN, VALID_CAPABILITIES, VALID_PROVIDES
from harness.sdk.types import SDK_API_VERSION


@dataclass
class DiagnosticCheck:
    category: str  # "manifest", "boundaries", "capabilities", "schema", "deprecations"
    status: Literal["OK", "WARN", "FAIL"]
    message: str
    details: str | None = None


@dataclass
class DoctorReport:
    plugin_name: str
    plugin_path: Path
    checks: list[DiagnosticCheck] = field(default_factory=list)

    @property
    def passed(self) -> bool:
        return all(c.status != "FAIL" for c in self.checks)

    @property
    def has_warnings(self) -> bool:
        return any(c.status == "WARN" for c in self.checks)


def _get_ast_imports(file_path: Path) -> list[tuple[int, str]]:
    """Extracts line numbers and module names imported in python file."""
    imports: list[tuple[int, str]] = []
    try:
        tree = ast.parse(file_path.read_text(encoding="utf-8"), filename=str(file_path))
    except Exception:
        return imports

    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            for alias in node.names:
                imports.append((node.lineno, alias.name))
        elif isinstance(node, ast.ImportFrom):
            if node.module:
                imports.append((node.lineno, node.module))

    return imports


def diagnose_plugin(plugin_dir: Path | str) -> DoctorReport:
    """
    Performs comprehensive diagnostic inspection on a plugin directory.
    Checks:
    1. Manifest presence and valid fields.
    2. Settings schema existence and validity.
    3. Import boundary violations (must only import harness.sdk).
    4. Prohibited direct subprocess imports.
    5. Capability alignment (e.g. secrets, exec).
    6. Deprecated legacy module usages.
    """
    p_dir = Path(plugin_dir).resolve()
    manifest_file = p_dir / "harness_plugin.toml" if p_dir.is_dir() else p_dir
    actual_dir = manifest_file.parent

    report = DoctorReport(plugin_name=actual_dir.name, plugin_path=actual_dir)

    # 1. Manifest Checks
    if not manifest_file.exists():
        report.checks.append(
            DiagnosticCheck(
                category="manifest",
                status="FAIL",
                message=f"Missing manifest file at {manifest_file}",
            )
        )
        return report

    doc: dict[str, Any] = {}
    try:
        with open(manifest_file, "rb") as f:
            doc = tomllib.load(f)
        report.checks.append(
            DiagnosticCheck(
                category="manifest",
                status="OK",
                message="Valid TOML format",
            )
        )
    except Exception as e:
        report.checks.append(
            DiagnosticCheck(
                category="manifest",
                status="FAIL",
                message=f"Failed to parse TOML manifest: {e}",
            )
        )
        return report

    plugin_table = doc.get("plugin", {})
    if not isinstance(plugin_table, dict) or not plugin_table:
        report.checks.append(
            DiagnosticCheck(
                category="manifest",
                status="FAIL",
                message="Manifest missing required [plugin] table",
            )
        )
        return report

    # Name check
    name = plugin_table.get("name", "")
    report.plugin_name = name or actual_dir.name
    if not name or not NAME_PATTERN.match(name):
        report.checks.append(
            DiagnosticCheck(
                category="manifest",
                status="FAIL",
                message=f"Invalid plugin name '{name}': must match pattern ^[a-z][a-z0-9-]{{1,40}}$",
            )
        )
    else:
        report.checks.append(
            DiagnosticCheck(category="manifest", status="OK", message=f"Valid name '{name}'")
        )

    # Version check
    version = plugin_table.get("version", "")
    if not version:
        report.checks.append(
            DiagnosticCheck(category="manifest", status="FAIL", message="Missing 'version' field")
        )
    else:
        report.checks.append(
            DiagnosticCheck(category="manifest", status="OK", message=f"Version '{version}'")
        )

    # API Version check
    api_ver = str(plugin_table.get("api_version", ""))
    if api_ver != SDK_API_VERSION:
        report.checks.append(
            DiagnosticCheck(
                category="manifest",
                status="FAIL",
                message=f"api_version '{api_ver}' incompatible with SDK version '{SDK_API_VERSION}'",
            )
        )
    else:
        report.checks.append(
            DiagnosticCheck(
                category="manifest",
                status="OK",
                message=f"api_version '{api_ver}' matches SDK",
            )
        )

    # Provides check
    provides = plugin_table.get("provides", [])
    if not isinstance(provides, list) or not provides:
        report.checks.append(
            DiagnosticCheck(
                category="manifest",
                status="FAIL",
                message="'provides' must be a non-empty list of component kinds",
            )
        )
    else:
        invalid_kinds = [k for k in provides if k not in VALID_PROVIDES]
        if invalid_kinds:
            report.checks.append(
                DiagnosticCheck(
                    category="manifest",
                    status="FAIL",
                    message=f"Invalid provides kinds: {invalid_kinds}. Allowed: {sorted(VALID_PROVIDES)}",
                )
            )
        else:
            report.checks.append(
                DiagnosticCheck(
                    category="manifest",
                    status="OK",
                    message=f"Provides kinds: {provides}",
                )
            )

    # Capabilities check
    caps = plugin_table.get("capabilities", [])
    if not isinstance(caps, list):
        report.checks.append(
            DiagnosticCheck(
                category="manifest",
                status="FAIL",
                message="'capabilities' must be a list of strings",
            )
        )
    else:
        invalid_caps = [c for c in caps if c not in VALID_CAPABILITIES]
        if invalid_caps:
            report.checks.append(
                DiagnosticCheck(
                    category="manifest",
                    status="FAIL",
                    message=f"Invalid capabilities: {invalid_caps}. Allowed: {sorted(VALID_CAPABILITIES)}",
                )
            )
        else:
            report.checks.append(
                DiagnosticCheck(
                    category="manifest",
                    status="OK",
                    message=f"Capabilities declared: {caps}",
                )
            )

    # Entry point check
    entry = plugin_table.get("entry", "")
    if not entry:
        report.checks.append(
            DiagnosticCheck(
                category="manifest",
                status="FAIL",
                message="Missing required 'entry' field",
            )
        )
    else:
        report.checks.append(
            DiagnosticCheck(category="manifest", status="OK", message=f"Entry: '{entry}'")
        )

    # 2. Settings Schema Check
    settings_schema_file = None
    if "settings" in doc and isinstance(doc["settings"], dict):
        settings_schema_file = doc["settings"].get("schema")

    if settings_schema_file:
        schema_path = actual_dir / settings_schema_file
        if not schema_path.exists():
            report.checks.append(
                DiagnosticCheck(
                    category="schema",
                    status="FAIL",
                    message=f"Declared settings schema '{settings_schema_file}' not found",
                )
            )
        else:
            try:
                schema_data = json.loads(schema_path.read_text(encoding="utf-8"))
                if not isinstance(schema_data, dict) or schema_data.get("type") != "object":
                    report.checks.append(
                        DiagnosticCheck(
                            category="schema",
                            status="WARN",
                            message="Schema root should declare type: 'object'",
                        )
                    )
                else:
                    report.checks.append(
                        DiagnosticCheck(
                            category="schema",
                            status="OK",
                            message="Valid JSON settings schema",
                        )
                    )
            except Exception as e:
                report.checks.append(
                    DiagnosticCheck(
                        category="schema",
                        status="FAIL",
                        message=f"Failed to parse settings schema JSON: {e}",
                    )
                )

    # 3. Source Code AST Inspection
    boundary_violations: list[str] = []
    subprocess_violations: list[str] = []
    deprecated_usages: list[str] = []
    found_secrets_usage = False
    found_exec_usage = False

    py_files = list(actual_dir.rglob("*.py"))
    # Filter out tests from AST boundary check
    src_files = [f for f in py_files if "tests" not in f.parts]

    for py_file in src_files:
        code_text = py_file.read_text(encoding="utf-8")
        if "ctx.secrets" in code_text:
            found_secrets_usage = True
        if "ctx.executor" in code_text:
            found_exec_usage = True

        for lineno, mod in _get_ast_imports(py_file):
            # Check harness import boundaries
            if mod == "harness" or (mod.startswith("harness.") and not mod.startswith("harness.sdk")):
                boundary_violations.append(f"{py_file.name}:{lineno} -> {mod}")

            # Check direct subprocess import
            if mod == "subprocess" or mod.startswith("subprocess."):
                subprocess_violations.append(f"{py_file.name}:{lineno} -> {mod}")

            # Check legacy modules
            if mod.startswith("harness.core.daemon") or mod.startswith("harness.web"):
                deprecated_usages.append(f"{py_file.name}:{lineno} -> {mod}")

    # Boundary reporting
    if boundary_violations:
        report.checks.append(
            DiagnosticCheck(
                category="boundaries",
                status="FAIL",
                message="Plugins must only import harness.sdk",
                details="\n".join(boundary_violations),
            )
        )
    else:
        report.checks.append(
            DiagnosticCheck(
                category="boundaries",
                status="OK",
                message="All imports restricted to harness.sdk & stdlib",
            )
        )

    # Subprocess check
    if subprocess_violations:
        report.checks.append(
            DiagnosticCheck(
                category="boundaries",
                status="FAIL",
                message="Direct subprocess import forbidden; must use ctx.executor",
                details="\n".join(subprocess_violations),
            )
        )
    else:
        report.checks.append(
            DiagnosticCheck(
                category="boundaries",
                status="OK",
                message="No direct subprocess imports",
            )
        )

    # Deprecation check
    if deprecated_usages:
        report.checks.append(
            DiagnosticCheck(
                category="deprecations",
                status="FAIL",
                message="Found deprecated legacy module references",
                details="\n".join(deprecated_usages),
            )
        )
    else:
        report.checks.append(
            DiagnosticCheck(
                category="deprecations",
                status="OK",
                message="No deprecated module usages",
            )
        )

    # Capability alignment check
    declared_caps = set(caps if isinstance(caps, list) else [])
    if found_secrets_usage and "secrets" not in declared_caps:
        report.checks.append(
            DiagnosticCheck(
                category="capabilities",
                status="FAIL",
                message="Plugin accesses ctx.secrets but lacks 'secrets' capability",
            )
        )
    if found_exec_usage and "exec" not in declared_caps:
        report.checks.append(
            DiagnosticCheck(
                category="capabilities",
                status="FAIL",
                message="Plugin accesses ctx.executor but lacks 'exec' capability",
            )
        )

    return report
