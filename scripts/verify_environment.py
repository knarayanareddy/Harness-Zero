"""Read-only CI readiness report. No installs, credential reads, or environment repair."""
from __future__ import annotations

import importlib
import importlib.metadata
import json
import os
import platform
import shutil
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
PACKAGES = ("pytest", "pytest-asyncio", "ruff", "pydantic", "httpx", "aiohttp", "harness-zero")


def command(argv: list[str]) -> dict:
    try:
        result = subprocess.run(argv, cwd=ROOT, capture_output=True, text=True, timeout=10, check=False)
        return {"exit_code": result.returncode, "stdout": result.stdout.strip(), "stderr": result.stderr.strip()}
    except (OSError, subprocess.TimeoutExpired) as exc:
        return {"exit_code": -1, "error": type(exc).__name__}


def inspect_environment() -> dict:
    errors = []
    versions = {}
    for name in PACKAGES:
        try:
            versions[name] = importlib.metadata.version(name)
        except importlib.metadata.PackageNotFoundError:
            versions[name] = None
            errors.append(f"Missing {name}; install this checkout with its dev extra.")
    for name in ("ssl", "xml.parsers.expat", "harness.kernel.bootstrap"):
        try:
            importlib.import_module(name)
        except (ImportError, OSError) as exc:
            errors.append(f"Cannot import {name}: {type(exc).__name__}.")
    try:
        harness = importlib.import_module("harness")
        if Path(harness.__file__).resolve().parent != ROOT / "harness":
            errors.append("Imported harness does not belong to this checkout; reinstall its editable dev extra.")
    except (ImportError, OSError):
        pass  # The failed bootstrap import above already reports a missing harness.
    if sys.version_info[:2] not in ((3, 11), (3, 12)):
        errors.append("The baseline CI matrix requires Python 3.11 or 3.12; other versions are unqualified.")
    python = shutil.which("python")
    child = command([python, "-c", "import sys; print(sys.prefix)"]) if python else {"exit_code": -1}
    if child["exit_code"] or Path(child.get("stdout", "")).resolve() != Path(sys.prefix).resolve():
        errors.append("Prepend the active virtualenv bin directory to PATH; child jobs invoke python.")
    node = shutil.which("node")
    node_result = command([node, "--version"]) if node else {"exit_code": -1}
    if node_result["exit_code"] or not node_result.get("stdout", "").startswith("v22."):
        errors.append("Node 22 is required to reproduce the checked-in CI baseline.")
    npm = shutil.which("npm")
    npm_result = command([npm, "ls", "--depth=0", "--json", "jsdom", "tailwindcss"]) if npm else {"exit_code": -1}
    if npm_result["exit_code"]:
        errors.append("Install locked Node dev dependencies with npm ci --include=dev.")
    contaminated = [name for name in ("PYTHONPATH", "DYLD_LIBRARY_PATH", "PYTHONHOME") if os.environ.get(name)]
    if contaminated:
        errors.append("Remove review-only interpreter overrides: " + ", ".join(contaminated) + ".")
    return {
        "schema_version": 1,
        "ready": not errors,
        "python": {"version": platform.python_version(), "executable": sys.executable, "path_python": python},
        "host": {"system": platform.system(), "machine": platform.machine(), "macos": platform.mac_ver()[0]},
        "packages": versions,
        "node": node_result,
        "node_packages": npm_result,
        "checkout": command(["git", "rev-parse", "HEAD"]),
        "errors": errors,
        "limits": ["Readiness is not a test pass or a native sandbox availability claim.",
                   "Run tests with a disposable HOME and HARNESS_CONFIG_DIR; no credentials are needed."],
    }


if __name__ == "__main__":
    report = inspect_environment()
    print(json.dumps(report, indent=2))
    raise SystemExit(0 if report["ready"] else 1)
