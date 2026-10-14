"""
Tests for KRN-04: SettingsStore and Secrets.
Absorbs TOOL-06 and SEC-07.
"""
from __future__ import annotations

import json
import os
import stat
from pathlib import Path

import pytest

from harness.kernel.secrets import Secrets, migrate_legacy_config
from harness.kernel.settings import (
    CURRENT_SCHEMA_VERSION,
    KERNEL_SCHEMA,
    SettingsStore,
    validate_schema_value,
)
from harness.sdk.context import SecretsView, SettingsView
from harness.sdk.errors import CapabilityDenied, SettingsValidationError
from harness.sdk.types import Capability


def test_schema_validator_primitives():
    """Validates primitive type and constraint enforcement."""
    str_schema = {"type": "string"}
    validate_schema_value("hello", str_schema)
    with pytest.raises(SettingsValidationError):
        validate_schema_value(123, str_schema)

    int_schema = {"type": "integer", "minimum": 1, "maximum": 10}
    validate_schema_value(5, int_schema)
    with pytest.raises(SettingsValidationError):
        validate_schema_value("5", int_schema)
    with pytest.raises(SettingsValidationError):
        validate_schema_value(True, int_schema)  # booleans are not valid ints
    with pytest.raises(SettingsValidationError):
        validate_schema_value(0, int_schema)
    with pytest.raises(SettingsValidationError):
        validate_schema_value(11, int_schema)

    enum_schema = {"type": "string", "enum": ["allow", "ask", "plan"]}
    validate_schema_value("ask", enum_schema)
    with pytest.raises(SettingsValidationError):
        validate_schema_value("bypass", enum_schema)


def test_settings_store_defaults_and_get(tmp_path: Path):
    """Checks that registered kernel schema defaults are returned."""
    settings_file = tmp_path / "settings.json"
    store = SettingsStore(settings_file)

    assert store.get("kernel.strategy") == "single_loop"
    assert store.get("kernel.max_tool_iterations") == 20
    assert store.get("kernel.security.permission_mode") == "ask"
    assert store.get("kernel.security.destructive_blocking") is True
    assert store.get("kernel.limits.tool_result_bytes") == 65536
    assert store.get("kernel.plugins.enabled") == []

    # Non-existent returns provided default or None
    assert store.get("kernel.unknown_key", "default_val") == "default_val"
    assert store.get("kernel.unknown_key") is None


def test_settings_store_validation_on_write(tmp_path: Path):
    """Asserts that invalid writes are rejected and valid writes succeed."""
    settings_file = tmp_path / "settings.json"
    store = SettingsStore(settings_file)

    # Valid writes
    store.set("kernel.strategy", "architect")
    assert store.get("kernel.strategy") == "architect"

    store.set("kernel.max_tool_iterations", 50)
    assert store.get("kernel.max_tool_iterations") == 50

    # Invalid enum
    with pytest.raises(SettingsValidationError):
        store.set("kernel.strategy", "invalid_strategy")

    # Invalid minimum
    with pytest.raises(SettingsValidationError):
        store.set("kernel.max_tool_iterations", 0)

    # Invalid type
    with pytest.raises(SettingsValidationError):
        store.set("kernel.max_tool_iterations", "twenty")

    # Unknown property in kernel schema (additionalProperties: False)
    with pytest.raises(SettingsValidationError):
        store.set("kernel.unknown_prop", 123)


def test_settings_store_custom_plugin_schema(tmp_path: Path):
    """Verifies that plugins can register custom schemas with strict validation."""
    store = SettingsStore(tmp_path / "settings.json")
    plugin_schema = {
        "type": "object",
        "additionalProperties": False,
        "properties": {
            "timeout_s": {"type": "integer", "default": 30, "minimum": 5},
            "sandbox_name": {"type": "string", "default": "default_box"},
        },
    }
    store.register_schema("plugins.my_plugin", plugin_schema)

    assert store.get("plugins.my_plugin.timeout_s") == 30
    assert store.get("plugins.my_plugin.sandbox_name") == "default_box"

    store.set("plugins.my_plugin.timeout_s", 60)
    assert store.get("plugins.my_plugin.timeout_s") == 60

    with pytest.raises(SettingsValidationError):
        store.set("plugins.my_plugin.timeout_s", 2)  # below min 5

    with pytest.raises(SettingsValidationError):
        store.set("plugins.my_plugin.extra_field", "val")  # additionalProperties: False


def test_settings_store_atomic_file_permissions(tmp_path: Path):
    """Verifies atomic write with 0600 file permissions and 0700 dir permissions."""
    config_dir = tmp_path / "custom_config_dir"
    settings_file = config_dir / "settings.json"

    store = SettingsStore(settings_file)
    store.set("kernel.provider", "anthropic")

    assert settings_file.exists()
    file_mode = stat.S_IMODE(os.stat(settings_file).st_mode)
    assert file_mode == 0o600

    dir_mode = stat.S_IMODE(os.stat(config_dir).st_mode)
    assert dir_mode == 0o700


def test_settings_store_change_subscriptions(tmp_path: Path):
    """Tests on_change subscriptions and prefix filtering."""
    store = SettingsStore(tmp_path / "settings.json")
    received_all: list[list[str]] = []
    received_sec: list[list[str]] = []

    unsub_all = store.on_change(lambda paths: received_all.append(paths))
    unsub_sec = store.on_change(
        lambda paths: received_sec.append(paths), prefix="kernel.security"
    )

    store.set("kernel.provider", "gemini")
    assert len(received_all) == 1
    assert received_all[0] == ["kernel.provider"]
    assert len(received_sec) == 0  # Not matching prefix

    store.set("kernel.security.permission_mode", "allow")
    assert len(received_all) == 2
    assert len(received_sec) == 1
    assert received_sec[0] == ["kernel.security.permission_mode"]

    # Unsubscribe test
    unsub_all()
    store.set("kernel.security.spend_cap_usd", 10.0)
    assert len(received_all) == 2  # No new notifications
    assert len(received_sec) == 2  # Received update

    unsub_sec()
    store.set("kernel.security.spend_cap_usd", 15.0)
    assert len(received_sec) == 2  # No new notifications after unsubscribe


def test_settings_store_migrations(tmp_path: Path):
    """Tests schema migrations on load."""
    settings_file = tmp_path / "settings.json"
    # Seed older version 0 file
    old_data = {"schema_version": 0, "legacy_key": "legacy_val"}
    settings_file.write_text(json.dumps(old_data), encoding="utf-8")

    store = SettingsStore.__new__(SettingsStore)
    store.path = settings_file
    store._schemas = {"kernel": KERNEL_SCHEMA}
    store._subscribers = []
    store._migrations = {}

    def migrate_v0_to_v1(data: dict) -> dict:
        data["kernel"] = {"migrated_from_v0": True}
        return data

    store.register_migration(0, 1, migrate_v0_to_v1)
    store._load()

    assert store._data.get("schema_version") == CURRENT_SCHEMA_VERSION
    assert store.get("kernel.migrated_from_v0") is True


def test_settings_view_contract(tmp_path: Path):
    """Verifies PluginSettingsView conforms to SDK SettingsView protocol."""
    store = SettingsStore(tmp_path / "settings.json")
    store.register_schema(
        "plugins.git",
        {"type": "object", "properties": {"auto_commit": {"type": "boolean", "default": False}}},
    )

    view = store.get_view("plugins.git")
    assert isinstance(view, SettingsView)

    # Scoped get
    assert view.get("auto_commit") is False
    assert view.get("plugins.git.auto_commit") is False

    # Scoped on_change
    notifications: list[list[str]] = []
    view.on_change(lambda paths: notifications.append(paths))

    store.set("plugins.git.auto_commit", True)
    assert view.get("auto_commit") is True
    assert len(notifications) == 1


def test_secrets_file_permissions_and_has(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    """Tests Secrets creation with 0600 mode and has() probing."""
    secrets_file = tmp_path / "secrets.json"
    secrets = Secrets(secrets_file)

    assert not secrets.has("anthropic")
    assert secrets.get("anthropic") is None

    secrets.set("anthropic", "sk-ant-testkey123")
    assert secrets.has("anthropic")
    assert secrets.has("ANTHROPIC_API_KEY")
    assert secrets.has("anthropic_api_key")
    assert secrets.get("anthropic") == "sk-ant-testkey123"
    assert secrets.get("ANTHROPIC_API_KEY") == "sk-ant-testkey123"

    # Verify file permissions
    assert secrets_file.exists()
    assert stat.S_IMODE(os.stat(secrets_file).st_mode) == 0o600

    # Ensure repr never leaks keys or values
    r = repr(secrets)
    assert "sk-ant-testkey123" not in r
    assert "anthropic" not in r
    assert "count=1" in r

    # Delete secret
    assert secrets.delete("anthropic") is True
    assert not secrets.has("anthropic")
    assert secrets.count() == 0


def test_secrets_env_precedence(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    """
    Tests that env_precedence=False (default) prioritizes secrets file over process env,
    and env_precedence=True prioritizes process env.
    """
    secrets_file = tmp_path / "secrets.json"
    monkeypatch.setenv("OPENAI_API_KEY", "env-key-value")

    # Default env_precedence=False: file wins
    sec_file_wins = Secrets(secrets_file, env_precedence=False)
    sec_file_wins.set("openai", "file-key-value")
    assert sec_file_wins.get("openai") == "file-key-value"

    # env_precedence=True: env wins
    sec_env_wins = Secrets(secrets_file, env_precedence=True)
    assert sec_env_wins.get("openai") == "env-key-value"


def test_plugin_secrets_view_capability_gating(tmp_path: Path):
    """
    Ensures SecretsView permits has() for all plugins but gates get() behind Capability.SECRETS.
    """
    secrets_file = tmp_path / "secrets.json"
    secrets = Secrets(secrets_file)
    secrets.set("deepseek", "sk-deepseek-123")

    # Plugin without Capability.SECRETS
    unprivileged_view = secrets.get_view(
        capabilities=frozenset([Capability.FS_READ]), plugin_name="reader_plugin"
    )
    assert isinstance(unprivileged_view, SecretsView)

    # has() succeeds
    assert unprivileged_view.has("deepseek") is True

    # get() raises CapabilityDenied
    with pytest.raises(CapabilityDenied) as exc:
        unprivileged_view.get("deepseek")
    assert "reader_plugin" in str(exc.value)
    assert "Capability.SECRETS" in str(exc.value)

    # Plugin with Capability.SECRETS
    privileged_view = secrets.get_view(
        capabilities=frozenset([Capability.SECRETS]), plugin_name="auth_plugin"
    )
    assert privileged_view.get("deepseek") == "sk-deepseek-123"


def test_migrate_legacy_config_once(tmp_path: Path):
    """
    Tests one-time migration from legacy ~/.harness/config.json into SettingsStore and Secrets.
    """
    config_dir = tmp_path / ".harness"
    config_dir.mkdir(parents=True, exist_ok=True)
    legacy_file = config_dir / "config.json"
    legacy_data = {
        "default_provider": "deepseek",
        "default_model": "deepseek-reasoner",
        "api_keys": {
            "deepseek": "sk-legacy-deepseek-999",
            "gemini": "sk-legacy-gemini-888",
        },
    }
    legacy_file.write_text(json.dumps(legacy_data), encoding="utf-8")

    settings = SettingsStore(config_dir / "settings.json")
    secrets = Secrets(config_dir / "secrets.json")

    # First migration run: performs import
    migrated = migrate_legacy_config(config_dir, settings, secrets)
    assert migrated is True

    # Verify settings imported
    assert settings.get("kernel.provider") == "deepseek"
    assert settings.get("kernel.model") == "deepseek-reasoner"
    assert settings.get("kernel.migrated_from_legacy_config") is True

    # Verify secrets imported
    assert secrets.get("deepseek") == "sk-legacy-deepseek-999"
    assert secrets.get("gemini") == "sk-legacy-gemini-888"

    # User modifies settings and secrets
    settings.set("kernel.provider", "anthropic")
    secrets.set("deepseek", "new-user-secret")

    # Second migration run: must skip and NOT overwrite
    migrated_second = migrate_legacy_config(config_dir, settings, secrets)
    assert migrated_second is False
    assert settings.get("kernel.provider") == "anthropic"
    assert secrets.get("deepseek") == "new-user-secret"
