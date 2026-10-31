"""Redaction sources follow real Secrets inputs without masking ordinary paths."""
from __future__ import annotations

import os
from pathlib import Path

import pytest

from harness.kernel.sanitizer import sanitize_output
from harness.kernel.secrets import Secrets


def test_redaction_includes_credential_environment_but_not_ordinary_paths(tmp_path, monkeypatch):
    monkeypatch.setattr(os, "environ", {
        "ANTHROPIC_API_KEY": "synthetic-provider-credential-48152",
        "CUSTOM_TOKEN": "synthetic-custom-credential-58291",
        "HOME": "/synthetic/home",
        "PATH": "/synthetic/bin",
    })
    secrets = Secrets(tmp_path / "secrets.json")
    secrets.set("file_credential", "synthetic-file-credential-29821")
    values = secrets.redaction_values()
    assert set(values) == {
        "synthetic-provider-credential-48152",
        "synthetic-custom-credential-58291",
        "synthetic-file-credential-29821",
    }
    text = "synthetic-custom-credential-58291 /synthetic/home /synthetic/bin"
    assert sanitize_output(text, values) == "[REDACTED SECRET] /synthetic/home /synthetic/bin"


def test_redaction_includes_file_name_alias_environment_even_when_shadowed(tmp_path, monkeypatch):
    monkeypatch.setattr(os, "environ", {"VENDOR": "synthetic-shadowed-env-value-38482"})
    secrets = Secrets(tmp_path / "secrets.json")
    secrets.set("vendor_api_key", "synthetic-file-value-75624")
    assert secrets.get("vendor") == "synthetic-file-value-75624"
    assert set(secrets.redaction_values()) == {
        "synthetic-shadowed-env-value-38482", "synthetic-file-value-75624",
    }


def test_redaction_retains_arbitrary_resolved_environment_credentials_and_rotation(tmp_path, monkeypatch):
    monkeypatch.setattr(os, "environ", {"OPAQUE_CUSTOM_NAME": "synthetic-opaque-first-94372"})
    secrets = Secrets(tmp_path / "secrets.json")
    assert secrets.get("OPAQUE_CUSTOM_NAME") == "synthetic-opaque-first-94372"
    monkeypatch.setenv("OPAQUE_CUSTOM_NAME", "synthetic-opaque-second-48392")
    assert secrets.get("OPAQUE_CUSTOM_NAME") == "synthetic-opaque-second-48392"
    monkeypatch.delenv("OPAQUE_CUSTOM_NAME")
    assert set(secrets.redaction_values()) == {
        "synthetic-opaque-first-94372", "synthetic-opaque-second-48392",
    }


@pytest.mark.parametrize("env_precedence", [False, True])
def test_redaction_does_not_change_precedence_or_short_secret_policy(tmp_path, monkeypatch, env_precedence):
    monkeypatch.setattr(os, "environ", {
        "VENDOR_API_KEY": "synthetic-environment-value-63298", "SHORT_SECRET": "tiny",
        "UNREQUESTED_OPAQUE": "ordinary-unclassified-value-75482",
    })
    secrets = Secrets(tmp_path / "secrets.json", env_precedence=env_precedence)
    secrets.set("VENDOR_API_KEY", "synthetic-file-value-31928")
    expected = "synthetic-environment-value-63298" if env_precedence else "synthetic-file-value-31928"
    assert secrets.get("vendor") == expected
    assert secrets.get("missing", "fallback") == "fallback"
    assert set(secrets.redaction_values()) == {
        "synthetic-file-value-31928", "synthetic-environment-value-63298",
    }
    assert secrets.get("SHORT_SECRET") == "tiny"
    assert "tiny" not in secrets.redaction_values()
    assert secrets.get("vendor") == expected
    assert "synthetic-environment-value-63298" not in secrets.path.read_text()


@pytest.mark.parametrize("error_type", [PermissionError, OSError])
def test_redaction_discovery_raises_on_existing_file_read_failure(tmp_path, monkeypatch, error_type):
    secrets = Secrets(tmp_path / "secrets.json")
    secrets.set("VENDOR_API_KEY", "synthetic-file-credential-31928")
    read_text = Path.read_text

    def unavailable(path, *args, **kwargs):
        if path == secrets.path:
            raise error_type("injected secrets-file read failure")
        return read_text(path, *args, **kwargs)

    monkeypatch.setattr(Path, "read_text", unavailable)
    with pytest.raises(error_type, match="secrets-file read failure"):
        secrets.redaction_values()
    assert secrets.get("missing", "fallback") == "fallback"


@pytest.mark.parametrize("raw", ["{broken", "[]", '{"VENDOR_API_KEY": 123}'])
def test_redaction_discovery_rejects_malformed_secret_files(tmp_path, monkeypatch, raw):
    monkeypatch.setattr(os, "environ", {})
    secrets = Secrets(tmp_path / "secrets.json")
    secrets.path.write_text(raw)
    with pytest.raises(ValueError):
        secrets.redaction_values()
    assert secrets.get("missing", "fallback") == "fallback"


def test_redaction_retains_resolved_file_values_after_rotation_and_removal(tmp_path, monkeypatch):
    monkeypatch.setattr(os, "environ", {})
    secrets = Secrets(tmp_path / "secrets.json")
    secrets.set("vendor", "synthetic-file-first-51829")
    assert secrets.get("vendor") == "synthetic-file-first-51829"
    secrets.set("vendor", "synthetic-file-second-95172")
    assert secrets.get("vendor") == "synthetic-file-second-95172"
    secrets.path.unlink()
    assert set(secrets.redaction_values()) == {"synthetic-file-first-51829", "synthetic-file-second-95172"}
    assert secrets.get("vendor", "fallback") == "fallback"
