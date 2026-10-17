"""WS-06a: the preset is one setting; the other security settings resolve from it until explicitly pinned.

This is a settings resolver, not a macro that writes three keys. These tests pin the information flow that makes
that distinction useful: choose a preset → `SettingsStore.get` answers its effective values → the permission policy
(and the Studio/CLI surface) consume that same answer, while the settings file keeps only choices the user made.
"""
from __future__ import annotations

import json
from pathlib import Path

import pytest

from harness.kernel import presets
from harness.kernel.settings import SettingsStore
from harness.sdk.errors import SettingsValidationError


def store(tmp_path: Path) -> SettingsStore:
    return SettingsStore(tmp_path / "settings.json")


def test_fresh_store_uses_careful_without_materialising_its_expansion(tmp_path: Path):
    settings = store(tmp_path)

    assert settings.get("kernel.security.preset") == "careful"
    assert settings.get("kernel.security.permission_mode") == "ask"
    assert settings.get("kernel.security.destructive_blocking") is True
    assert settings.get("kernel.security.approval_timeout_s") == 300
    assert not settings.path.exists(), "reading the preset defaults must not create a settings file"


def test_each_preset_resolves_the_effective_security_settings(tmp_path: Path):
    settings = store(tmp_path)
    expected = {
        "locked": ("plan", True, 300),
        "careful": ("ask", True, 300),
        "standard": ("ask", True, 300),
        "open": ("allow", True, 300),
    }

    for name, (mode, destructive, approval_timeout) in expected.items():
        settings.set("kernel.security.preset", name)
        assert settings.get("kernel.security.permission_mode") == mode
        assert settings.get("kernel.security.destructive_blocking") is destructive
        assert settings.get("kernel.security.approval_timeout_s") == approval_timeout
        expected_exec = "allow" if name == "standard" else "ask"
        assert settings.get("kernel.security.exec_in_sandbox") == expected_exec
        assert presets.summary(settings)["preset"] == name

    assert settings.get("kernel.security.preset") != "bypass", "bypass is an explicit mode, never a preset"


def test_explicit_values_win_but_the_unpinned_values_follow_the_new_preset(tmp_path: Path):
    settings = store(tmp_path)
    settings.set("kernel.security.permission_mode", "allow")
    settings.set("kernel.security.preset", "locked")

    # Explicit values are not defaulted away. Other keys still follow `locked`.
    assert settings.get("kernel.security.permission_mode") == "allow"
    assert settings.get("kernel.security.destructive_blocking") is True
    assert presets.customised(settings) == ["kernel.security.permission_mode"]
    assert presets.summary(settings)["pinned"] == ["kernel.security.permission_mode", "kernel.security.preset"]

    # Switching the preset cannot overwrite the explicit choice or materialise derived ones.
    settings.set("kernel.security.preset", "open")
    assert settings.get("kernel.security.permission_mode") == "allow"
    assert settings.get("kernel.security.approval_timeout_s") == 300
    saved = json.loads(settings.path.read_text(encoding="utf-8"))
    assert saved["kernel"]["security"] == {"permission_mode": "allow", "preset": "open"}
    assert "destructive_blocking" not in saved["kernel"]["security"]
    assert "approval_timeout_s" not in saved["kernel"]["security"]


def test_preset_is_schema_validated_and_cannot_offer_bypass(tmp_path: Path):
    settings = store(tmp_path)

    with pytest.raises(SettingsValidationError, match="allowed enum"):
        settings.set("kernel.security.preset", "bypass")
    with pytest.raises(SettingsValidationError, match="allowed enum"):
        settings.set("kernel.security.preset", "trust-me")

    # The old explicit permission mode remains available for automation and tests, but not by choosing a preset.
    settings.set("kernel.security.permission_mode", "bypass")
    assert settings.get("kernel.security.permission_mode") == "bypass"


def test_resolver_preserves_in_memory_overrides_and_call_site_defaults(tmp_path: Path):
    settings = store(tmp_path)
    settings.set("kernel.security.preset", "open")
    settings.set_override("kernel.security.permission_mode", "plan")

    assert settings.get("kernel.security.permission_mode") == "plan"
    # A controlled key resolves from the preset before this call's fallback; an unrelated key keeps its fallback.
    assert settings.get("kernel.security.destructive_blocking", False) is True
    assert settings.get("kernel.provider", "test-provider") == "test-provider"


def test_resolver_install_is_idempotent_and_can_be_removed(tmp_path: Path):
    settings = store(tmp_path)
    settings.set("kernel.security.preset", "open")

    remove = presets.install(settings)
    assert len(settings._resolvers) == 1
    assert settings.get("kernel.security.permission_mode") == "allow"

    remove()
    assert settings._resolvers == []
    assert settings.get("kernel.security.permission_mode") == "ask"  # schema default after the layer is gone
