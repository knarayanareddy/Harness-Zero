"""
Secure Secrets storage managing ~/.harness/secrets.json (mode 0600) and process environment.
Enforces capability gating via SecretsView, has() probing without leaking values,
env_precedence control, and one-time migration from legacy config.json.
Fulfills KRN-04 and absorbs SEC-07.
"""
from __future__ import annotations

import json
import logging
import os
import time
from pathlib import Path
from typing import Any

from harness.kernel.settings import SettingsStore
from harness.sdk.context import SecretsView
from harness.sdk.errors import CapabilityDenied
from harness.sdk.types import Capability

logger = logging.getLogger(__name__)


def _candidate_aliases(name: str) -> list[str]:
    """Generates candidate keys for alias matching (e.g. anthropic -> ANTHROPIC_API_KEY)."""
    candidates = [name]
    lower = name.lower()
    upper = name.upper()
    if lower not in candidates:
        candidates.append(lower)
    if upper not in candidates:
        candidates.append(upper)

    if lower.endswith("_api_key"):
        base = lower[:-8]
        for c in (base, base.upper()):
            if c not in candidates:
                candidates.append(c)
    else:
        for c in (f"{lower}_api_key", f"{upper}_API_KEY"):
            if c not in candidates:
                candidates.append(c)

    return candidates


class Secrets:
    """
    Manages secrets at rest with 0600 file permissions and environment fallbacks.
    Exposes existence checks without leaking secret values.
    """

    def __init__(self, path: Path | str | None = None, env_precedence: bool = False):
        if path is None:
            config_dir = Path(os.environ.get("HARNESS_CONFIG_DIR", Path.home() / ".harness"))
            self.path = config_dir / "secrets.json"
        else:
            p = Path(path)
            if p.is_dir():
                self.path = p / "secrets.json"
            else:
                self.path = p

        self.env_precedence = env_precedence

    def _load(self) -> dict[str, str]:
        """Loads raw secrets dictionary from disk."""
        if not self.path.exists():
            return {}
        try:
            raw = self.path.read_text(encoding="utf-8")
            data = json.loads(raw)
            if isinstance(data, dict):
                return {str(k): str(v) for k, v in data.items() if isinstance(v, str)}
            return {}
        except Exception as e:
            logger.warning(f"Failed to read secrets from {self.path}: {e}")
            return {}

    def _save(self, data: dict[str, str]) -> None:
        """Atomically saves secrets dictionary to disk with mode 0600."""
        parent = self.path.parent
        parent.mkdir(parents=True, exist_ok=True)
        try:
            os.chmod(parent, 0o700)
        except Exception:
            pass

        content = json.dumps(data, indent=2).encode("utf-8")
        temp_path = parent / f".{self.path.name}.tmp.{os.getpid()}_{time.time_ns()}"
        fd = os.open(temp_path, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
        try:
            with open(fd, "wb") as f:
                f.write(content)
            os.replace(temp_path, self.path)
            os.chmod(self.path, 0o600)
        except Exception:
            if temp_path.exists():
                try:
                    temp_path.unlink()
                except Exception:
                    pass
            raise

    def count(self) -> int:
        """Returns the count of secrets stored at rest."""
        return len(self._load())

    def has(self, name: str) -> bool:
        """
        Checks whether a secret exists without revealing its value.
        Honors env_precedence setting.
        """
        return self.get(name) is not None

    def get(self, name: str) -> str | None:
        """
        Retrieves a secret by name or alias, resolving between file and env.
        """
        aliases = _candidate_aliases(name)

        def _from_env() -> str | None:
            for alias in aliases:
                val = os.environ.get(alias)
                if val:
                    return val
            return None

        def _from_file() -> str | None:
            stored = self._load()
            for alias in aliases:
                if alias in stored:
                    return stored[alias]
            return None

        if self.env_precedence:
            val = _from_env()
            if val is not None:
                return val
            return _from_file()
        else:
            val = _from_file()
            if val is not None:
                return val
            return _from_env()

    def set(self, name: str, value: str) -> None:
        """Stores a secret to disk with atomic 0600 permissions."""
        if not name or not isinstance(name, str):
            raise ValueError("Secret name must be a non-empty string.")
        if not isinstance(value, str):
            raise ValueError("Secret value must be a string.")
        data = self._load()
        data[name] = value
        self._save(data)

    def set_many(self, secrets: dict[str, str]) -> None:
        """Stores multiple secrets to disk atomically."""
        data = self._load()
        for k, v in secrets.items():
            if not isinstance(k, str) or not isinstance(v, str):
                raise ValueError("All keys and values must be strings.")
            data[k] = v
        self._save(data)

    def delete(self, name: str) -> bool:
        """Deletes a secret from disk. Returns True if removed, False if not found."""
        data = self._load()
        aliases = _candidate_aliases(name)
        deleted = False
        for alias in aliases:
            if alias in data:
                del data[alias]
                deleted = True
        if deleted:
            self._save(data)
        return deleted

    def get_view(
        self,
        capabilities: frozenset[Capability] = frozenset(),
        plugin_name: str = "",
    ) -> SecretsView:
        """Returns a capability-gated SecretsView for plugins."""
        return PluginSecretsView(self, capabilities, plugin_name)

    def __repr__(self) -> str:
        # Never leak secret keys or values
        return f"<Secrets path={self.path!r} count={self.count()}>"


class PluginSecretsView:
    """Capability-gated view of secrets provided to plugins."""

    def __init__(
        self,
        secrets: Secrets,
        capabilities: frozenset[Capability] = frozenset(),
        plugin_name: str = "",
    ):
        self._secrets = secrets
        self._capabilities = capabilities
        self._plugin_name = plugin_name

    def has(self, name: str) -> bool:
        """Checks existence of secret without returning value. Allowed for all."""
        return self._secrets.has(name)

    def get(self, name: str) -> str | None:
        """
        Retrieves secret value. Requires Capability.SECRETS.
        Raises CapabilityDenied if caller lacks SECRETS capability.
        """
        if Capability.SECRETS not in self._capabilities:
            raise CapabilityDenied(
                capability="secrets",
                plugin=self._plugin_name,
                message=f"Plugin '{self._plugin_name}' lacks Capability.SECRETS to access '{name}'",
            )
        return self._secrets.get(name)


def migrate_legacy_config(
    config_dir: Path | str | None = None,
    settings_store: SettingsStore | None = None,
    secrets_store: Secrets | None = None,
) -> bool:
    """
    One-time migration from legacy ~/.harness/config.json into SettingsStore and Secrets.
    Imports provider/model into kernel settings and api_keys into Secrets.
    Returns True if migration was performed, False if skipped or already migrated.
    """
    if config_dir is None:
        c_dir = Path(os.environ.get("HARNESS_CONFIG_DIR", Path.home() / ".harness"))
    else:
        c_dir = Path(config_dir)

    legacy_file = c_dir / "config.json"
    if not legacy_file.exists():
        return False

    if settings_store is None:
        settings_store = SettingsStore(c_dir / "settings.json")
    if secrets_store is None:
        secrets_store = Secrets(c_dir / "secrets.json")

    # Check if already migrated
    if settings_store.get("kernel.migrated_from_legacy_config", False):
        return False

    try:
        raw = legacy_file.read_text(encoding="utf-8")
        data = json.loads(raw)
        if not isinstance(data, dict):
            return False
    except Exception as e:
        logger.warning(f"Could not parse legacy config at {legacy_file}: {e}")
        return False

    # 1. Migrate provider & model
    updates: dict[str, Any] = {}
    default_provider = data.get("default_provider")
    if default_provider and isinstance(default_provider, str):
        updates["kernel.provider"] = default_provider

    default_model = data.get("default_model")
    if default_model and isinstance(default_model, str):
        updates["kernel.model"] = default_model

    updates["kernel.migrated_from_legacy_config"] = True
    settings_store.set_many(updates)

    # 2. Migrate api_keys into secrets
    api_keys = data.get("api_keys")
    if isinstance(api_keys, dict):
        keys_to_set = {
            k: v for k, v in api_keys.items() if isinstance(k, str) and isinstance(v, str) and v
        }
        if keys_to_set:
            secrets_store.set_many(keys_to_set)

    return True
