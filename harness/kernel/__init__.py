"""
Harness Kernel package.
Foundation primitives for sovereignty, sandboxing, policy enforcement, settings, and secrets.
"""
from __future__ import annotations

from harness.kernel.bus import EventBus
from harness.kernel.jail import WorkspaceJail
from harness.kernel.secrets import PluginSecretsView, Secrets, migrate_legacy_config
from harness.kernel.settings import (
    CURRENT_SCHEMA_VERSION,
    KERNEL_SCHEMA,
    PluginSettingsView,
    SettingsStore,
    validate_schema_value,
)
from harness.kernel.validators import validate_safe_filename, validate_session_id

__all__ = [
    "EventBus",
    "WorkspaceJail",
    "validate_session_id",
    "validate_safe_filename",
    "SettingsStore",
    "PluginSettingsView",
    "KERNEL_SCHEMA",
    "CURRENT_SCHEMA_VERSION",
    "validate_schema_value",
    "Secrets",
    "PluginSecretsView",
    "migrate_legacy_config",
]
