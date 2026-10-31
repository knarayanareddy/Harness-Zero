"""The readiness check diagnoses failures without installing or changing configuration."""
from __future__ import annotations

import importlib.util
import subprocess
from pathlib import Path

SPEC = importlib.util.spec_from_file_location("verify_environment", Path(__file__).parents[1] / "scripts/verify_environment.py")
assert SPEC and SPEC.loader
MODULE = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(MODULE)


def test_command_timeout_is_a_failure_not_success(monkeypatch):
    def timeout(*args, **kwargs):
        raise subprocess.TimeoutExpired(args[0], 10)
    monkeypatch.setattr(MODULE.subprocess, "run", timeout)
    assert MODULE.command(["unused"])["exit_code"] == -1


def test_missing_dependencies_and_overrides_are_reported(monkeypatch):
    def missing(name):
        raise MODULE.importlib.metadata.PackageNotFoundError(name)
    monkeypatch.setattr(MODULE.importlib.metadata, "version", missing)
    monkeypatch.setattr(MODULE.shutil, "which", lambda name: None)
    monkeypatch.setattr(MODULE, "command", lambda argv: {"exit_code": 0, "stdout": "baseline"})
    monkeypatch.setenv("PYTHONPATH", "SYNTHETIC_PRIVATE_VALUE")
    report = MODULE.inspect_environment()
    assert report["ready"] is False
    assert any("Missing pytest;" in e for e in report["errors"])
    assert any("PYTHONPATH" in e for e in report["errors"])
    assert "SYNTHETIC_PRIVATE_VALUE" not in str(report)


def test_child_python_must_share_virtualenv_not_only_binary(monkeypatch):
    monkeypatch.setattr(MODULE.shutil, "which", lambda name: "/synthetic/" + name)

    def result(argv):
        if "--version" in argv:
            return {"exit_code": 0, "stdout": "v22.23.3"}
        if "-c" in argv:
            return {"exit_code": 0, "stdout": "/synthetic/wrong-prefix"}
        return {"exit_code": 0, "stdout": "{}"}

    monkeypatch.setattr(MODULE, "command", result)
    report = MODULE.inspect_environment()
    assert not report["ready"]
    assert any("Prepend the active virtualenv" in message for message in report["errors"])
