"""
Namespaced SettingsStore with JSON Schema registry, validation on write,
atomic replace persistence, change subscriptions, and schema migrations.
Fulfills KRN-04 and absorbs TOOL-06.
"""
from __future__ import annotations

import copy
import json
import logging
import os
import time
from collections.abc import Callable
from pathlib import Path
from typing import Any

from harness.sdk.context import SettingsView
from harness.sdk.errors import SettingsValidationError

logger = logging.getLogger(__name__)

CURRENT_SCHEMA_VERSION = 1


class _Unset:
    """What a resolver returns when it has no opinion about a path.

    `None` cannot be that sentinel: a setting may legitimately be unset *as a value* (an empty `kernel.provider`
    reads as `None`), and a resolution layer that swallowed it would turn "no one set this" into "the user chose
    nothing". So abstention is its own object, and `SettingsStore.get` skips it.
    """

    _instance: _Unset | None = None

    def __new__(cls) -> _Unset:
        if cls._instance is None:
            cls._instance = super().__new__(cls)
        return cls._instance

    def __repr__(self) -> str:  # pragma: no cover - logging aid
        return "<unset>"

    def __bool__(self) -> bool:
        return False


UNSET = _Unset()

# Default kernel.* JSON Schema with default values and validation rules
KERNEL_SCHEMA: dict[str, Any] = {
    "type": "object",
    "additionalProperties": False,
    "properties": {
        "provider": {
            "type": "string",
            "default": "",
            "title": "Default Provider",
            "x-ui": {"group": "Models & Providers", "widget": "select"},
        },
        "model": {
            "type": "string",
            "default": "",
            "title": "Default Model",
            "x-ui": {"group": "Models & Providers", "widget": "text"},
        },
        "strategy": {
            "type": "string",
            "default": "single_loop",
            "enum": ["single_loop", "architect_editor", "architect"],
            "title": "Turn Strategy",
            "x-ui": {"group": "Execution & Strategies", "widget": "select"},
        },
        "max_tool_iterations": {
            "type": "integer",
            "default": 20,
            "minimum": 1,
            "title": "Max Tool Iterations",
            "x-ui": {"group": "Execution & Strategies", "widget": "number"},
        },
        "request_timeout_s": {
            "type": "number",
            "default": 60.0,
            "minimum": 1.0,
            "title": "Provider Request Timeout (s)",
            "description": "HTTP timeout the Anthropic and Gemini providers use; Ollama applies 120 s.",
            "x-ui": {"group": "Execution & Strategies", "widget": "number"},
        },
        "security": {
            "type": "object",
            "additionalProperties": False,
            "properties": {
                "preset": {
                    "type": "string",
                    "default": "careful",
                    "enum": ["locked", "careful", "standard", "open"],
                    "title": "Security Preset",
                    "description": (
                        "The combination this kernel runs with. Each preset stands for the settings below, so "
                        "changing one of those *after* choosing a preset is allowed and is reported as "
                        "customised; `bypass` is not offered here, only as an explicit Permission Mode. Locked "
                        "reads and plans but never mutates; careful asks before every write; standard is careful "
                        "plus sandboxed execution; open stops asking but still blocks destructive commands."
                    ),
                    "x-ui": {"group": "Security & Sandboxing", "widget": "select", "order": 0},
                },
                "permission_mode": {
                    "type": "string",
                    "default": "ask",
                    "enum": ["allow", "ask", "plan", "bypass"],
                    "title": "Permission Mode",
                    "description": "Left as-is, this follows the Security Preset. Set it to pin one value.",
                    "x-ui": {"group": "Security & Sandboxing", "widget": "select"},
                },
                "destructive_blocking": {
                    "type": "boolean",
                    "default": True,
                    "title": "Block Destructive Commands",
                    "x-ui": {"group": "Security & Sandboxing", "widget": "boolean"},
                },
                "approval_timeout_s": {
                    "type": "integer",
                    "default": 300,
                    "minimum": 0,
                    "title": "Approval Timeout (s)",
                    "x-ui": {"group": "Security & Sandboxing", "widget": "number"},
                },
                "exec_in_sandbox": {
                    "type": "string",
                    "default": "ask",
                    "enum": ["ask", "allow"],
                    "title": "Execution in an available sandbox",
                    "description": "`standard` allows executor calls only when the host reported an available sandbox; without it, they ask before running unconfined.",
                    "x-ui": {"group": "Security & Sandboxing", "widget": "select"},
                },
                "spend_cap_usd": {
                    "type": "number",
                    "default": 5.0,
                    "minimum": 0.0,
                    "title": "Spend Cap (USD)",
                    "x-ui": {"group": "Economics & Spend", "widget": "number"},
                },
            },
        },
        "sandbox": {
            "type": "object",
            "additionalProperties": False,
            "properties": {
                "mode": {
                    "type": "string",
                    "default": "preferred",
                    "enum": ["required", "preferred", "off"],
                    "title": "Process sandbox mode",
                    "description": "Required refuses executor processes if the host cannot confine them; preferred asks before falling back; off runs without OS confinement.",
                    "x-ui": {"group": "Security & Sandboxing", "widget": "select"},
                },
                "backend": {
                    "type": "string",
                    "default": "auto",
                    "enum": ["auto", "bwrap", "seatbelt", "none"],
                    "title": "Sandbox backend",
                    "description": "Auto selects bubblewrap on Linux or Seatbelt on macOS. `none` is an explicit no-backend choice and never satisfies `required`.",
                    "x-ui": {"group": "Security & Sandboxing", "widget": "select"},
                },
                "network": {
                    "type": "boolean",
                    "default": False,
                    "title": "Allow network from confined processes",
                    "description": "Off by default; the `open` preset runs without a sandbox and allows network.",
                    "x-ui": {"group": "Security & Sandboxing", "widget": "boolean"},
                },
                "extra_readable": {
                    "type": "array",
                    "items": {"type": "string"},
                    "default": [],
                    "title": "Extra readable paths",
                    "description": "Absolute host paths mounted read-only for confined processes. Any extra path is reported as partial enforcement; do not add credential directories unless you intend the agent to read them.",
                    "x-ui": {"group": "Security & Sandboxing", "widget": "path-list"},
                },
            },
        },
        "context": {
            "type": "object",
            "additionalProperties": False,
            "properties": {
                "budget_tokens": {
                    "type": "integer",
                    "default": 60000,
                    "minimum": 1000,
                    "title": "Context Budget (tokens)",
                    "x-ui": {"group": "Execution & Strategies", "widget": "number"},
                },
            },
        },
        "background": {
            "type": "object",
            "additionalProperties": False,
            "properties": {
                "wait_s": {"type": "integer", "default": 600, "minimum": 0},
            },
        },
        "limits": {
            "type": "object",
            "additionalProperties": False,
            "properties": {
                "tool_result_bytes": {"type": "integer", "default": 65536, "minimum": 1024},
                # WS-03 cooperative timeout: after the deadline the handler gets this much time to
                # return partial output (and to let a tracked process exit) before it is cancelled.
                "tool_grace_s": {"type": "number", "default": 5.0, "minimum": 0.0},
                "upload_bytes": {"type": "integer", "default": 26214400, "minimum": 1024},
            },
        },
        "plugins": {
            "type": "object",
            "additionalProperties": False,
            "properties": {
                "enabled": {"type": "array", "items": {"type": "string"}, "default": []},
                "disabled": {"type": "array", "items": {"type": "string"}, "default": []},
                "allow_inprocess_third_party": {"type": "boolean", "default": False},
                "trusted_workspace_plugins": {"type": "boolean", "default": False},
                # Capability overrides are read as f"kernel.plugins.capabilities.{name}"
                # (registry), so this container stays open and keyed by plugin name.
                "capabilities": {
                    "type": "object",
                    "default": {},
                    "additionalProperties": {"type": "array", "items": {"type": "string"}},
                },
            },
        },
        "secrets": {
            "type": "object",
            "additionalProperties": False,
            "properties": {
                "env_precedence": {"type": "boolean", "default": False},
            },
        },
        "migrated_from_legacy_config": {"type": "boolean", "default": False},
    },
}


def validate_schema_value(val: Any, schema: dict[str, Any], path: str = "") -> None:
    """
    Validates a value against a JSON Schema fragment.
    Raises SettingsValidationError on schema violations.
    """
    expected_type = schema.get("type")
    if expected_type:
        if expected_type == "string":
            if not isinstance(val, str):
                raise SettingsValidationError(
                    f"Setting at '{path}' must be a string, got {type(val).__name__}"
                )
        elif expected_type == "integer":
            if not isinstance(val, int) or isinstance(val, bool):
                raise SettingsValidationError(
                    f"Setting at '{path}' must be an integer, got {type(val).__name__}"
                )
        elif expected_type == "number":
            if not isinstance(val, (int, float)) or isinstance(val, bool):
                raise SettingsValidationError(
                    f"Setting at '{path}' must be a number, got {type(val).__name__}"
                )
        elif expected_type == "boolean":
            if not isinstance(val, bool):
                raise SettingsValidationError(
                    f"Setting at '{path}' must be a boolean, got {type(val).__name__}"
                )
        elif expected_type == "array":
            if not isinstance(val, list):
                raise SettingsValidationError(
                    f"Setting at '{path}' must be a list/array, got {type(val).__name__}"
                )
            item_schema = schema.get("items")
            if item_schema and isinstance(item_schema, dict):
                for idx, item in enumerate(val):
                    validate_schema_value(item, item_schema, f"{path}[{idx}]")
        elif expected_type == "object":
            if not isinstance(val, dict):
                raise SettingsValidationError(
                    f"Setting at '{path}' must be an object/dict, got {type(val).__name__}"
                )
            props = schema.get("properties", {})
            if schema.get("additionalProperties") is False:
                for k in val:
                    if k not in props:
                        raise SettingsValidationError(
                            f"Unknown setting property '{k}' at '{path}'"
                        )
            for k, sub_val in val.items():
                if k in props:
                    sub_path = f"{path}.{k}" if path else k
                    validate_schema_value(sub_val, props[k], sub_path)

    if "enum" in schema:
        if val not in schema["enum"]:
            raise SettingsValidationError(
                f"Setting at '{path}' value {val!r} not in allowed enum options: {schema['enum']}"
            )

    if "minimum" in schema and isinstance(val, (int, float)) and not isinstance(val, bool):
        if val < schema["minimum"]:
            raise SettingsValidationError(
                f"Setting at '{path}' value {val} is below minimum {schema['minimum']}"
            )

    if "maximum" in schema and isinstance(val, (int, float)) and not isinstance(val, bool):
        if val > schema["maximum"]:
            raise SettingsValidationError(
                f"Setting at '{path}' value {val} exceeds maximum {schema['maximum']}"
            )

    if "required" in schema and isinstance(val, dict):
        for req in schema["required"]:
            if req not in val:
                raise SettingsValidationError(
                    f"Missing required setting property '{req}' at '{path}'"
                )


class SettingsStore:
    """
    Manages namespaced settings with schema registry, validation on write,
    atomic 0600 file replacement, and change subscriptions.
    """

    def __init__(self, path: Path | str | None = None):
        if path is None:
            config_dir = Path(os.environ.get("HARNESS_CONFIG_DIR", Path.home() / ".harness"))
            self.path = config_dir / "settings.json"
        else:
            p = Path(path)
            if p.is_dir():
                self.path = p / "settings.json"
            else:
                self.path = p

        self._schemas: dict[str, dict[str, Any]] = {}
        self._subscribers: list[tuple[str, Callable[[list[str]], None]]] = []
        self._migrations: dict[tuple[int, int], Callable[[dict[str, Any]], dict[str, Any]]] = {}
        self._data: dict[str, Any] = {}
        self._overrides: dict[str, Any] = {}
        # Resolution layers consulted for a path nothing stored (see `add_resolver`). A list, not one slot,
        # because presets and a later profile feature must be able to coexist without either owning the store.
        self._resolvers: list[Callable[[str], Any]] = []

        # Register standard kernel schema
        self.register_schema("kernel", KERNEL_SCHEMA)
        # The secrets namespace is a kernel surface: values live in the Secrets store, but the
        # *key list* is contributed by plugins declaring `x-secrets` (WS-02). Seeded open so the
        # settings panel always has the tab, and so a bare kernel is not missing a namespace.
        self.register_schema(
            "secrets", {"type": "object", "additionalProperties": True, "properties": {}}
        )

        # Load existing or initialize
        self._load()

        # WS-06a: permission presets belong to *reading a kernel setting*, not to one boot path, so the layer is
        # installed here — `harness settings`, a unit test that builds a store, and the daemon all resolve the same
        # answer. That is safe precisely because `careful`'s expansion equals the schema defaults (`ask`, `True`,
        # `300`): installing the resolver changes nothing until someone switches preset, which
        # `tests/kernel/test_presets.py` pins so this can never become a hidden behavioural diff for a store built
        # outside `bootstrap_kernel`.
        from harness.kernel.presets import install as _install_presets

        _install_presets(self)

    def register_schema(self, namespace: str, schema: dict[str, Any]) -> None:
        """Registers a JSON Schema fragment for a namespace (e.g. 'kernel' or 'plugins.git')."""
        if not namespace or not isinstance(schema, dict):
            raise ValueError("Namespace and schema dictionary required.")
        self._schemas[namespace] = schema

    def register_migration(
        self,
        from_version: int,
        to_version: int,
        migration_fn: Callable[[dict[str, Any]], dict[str, Any]],
    ) -> None:
        """Registers a schema migration step from from_version to to_version."""
        self._migrations[(from_version, to_version)] = migration_fn

    def get_registered_schemas(self) -> dict[str, dict[str, Any]]:
        """Returns deep copy of all registered schemas."""
        return copy.deepcopy(self._schemas)

    def _resolve_schema_default(self, path: str) -> Any:
        """Resolves default value from registered schemas for a dot-notated path."""
        for ns, schema in self._schemas.items():
            if path == ns:
                return schema.get("default")
            if path.startswith(ns + "."):
                remainder = path[len(ns) + 1 :].split(".")
                curr = schema
                found = True
                for part in remainder:
                    props = curr.get("properties", {})
                    if part in props:
                        curr = props[part]
                    else:
                        found = False
                        break
                if found and "default" in curr:
                    return copy.deepcopy(curr["default"])
        return None

    def _find_sub_schema(self, path: str) -> tuple[dict[str, Any] | None, bool]:
        """
        Finds the sub-schema governing the dot-notated path.
        Returns (sub_schema, schema_covers_namespace).
        """
        # Find longest matching namespace
        best_ns = ""
        for ns in self._schemas:
            if path == ns or path.startswith(ns + "."):
                if len(ns) > len(best_ns):
                    best_ns = ns

        if not best_ns:
            return None, False

        schema = self._schemas[best_ns]
        if path == best_ns:
            return schema, True

        remainder = path[len(best_ns) + 1 :].split(".")
        curr = schema
        for part in remainder:
            props = curr.get("properties", {})
            if part in props:
                curr = props[part]
            else:
                if curr.get("additionalProperties") is False:
                    raise SettingsValidationError(
                        f"Unknown setting property '{part}' along path '{path}'"
                    )
                return None, True
        return curr, True

    def add_resolver(self, resolver: Callable[[str], Any]) -> None:
        """Adds a layer that may supply a value for a path nothing stored.

        Order inside `get`: in-memory override, the stored document, resolvers in registration order, the
        caller's `default`, the schema default. A resolver takes the dotted path and returns `UNSET` to abstain.
        It must not call `get()` for a path it owns — that is the recursion every resolution layer invites. The
        preset resolver reads only `kernel.security.preset`, a key it does not own, then returns the expansion for
        the other keys.
        """
        if resolver not in self._resolvers:
            self._resolvers.append(resolver)

    def remove_resolver(self, resolver: Callable[[str], Any]) -> None:
        """Drops a resolver by identity (`harness/kernel/presets.py:install` keeps the object to undo itself)."""
        if resolver in self._resolvers:
            self._resolvers.remove(resolver)

    def has_explicit(self, path: str) -> bool:
        """Whether this path was *chosen* — an override or a stored value — rather than defaulted."""
        if path in self._overrides:
            return True
        curr: Any = self._data
        for part in path.split("."):
            if not isinstance(curr, dict) or part not in curr:
                return False
            curr = curr[part]
        return True

    def set_override(self, path: str, value: Any) -> None:
        """Sets an in-memory override that takes precedence and does not persist to disk."""
        self._overrides[path] = copy.deepcopy(value)

    def get(self, path: str, default: Any = None) -> Any:
        """
        Reads a namespaced setting by dot-notation (e.g. 'kernel.max_tool_iterations').
        Returns schema default or user default if unset.
        """
        if hasattr(self, "_overrides") and path in self._overrides:
            return copy.deepcopy(self._overrides[path])

        parts = path.split(".")
        curr = self._data
        found = True
        for part in parts:
            if isinstance(curr, dict) and part in curr:
                curr = curr[part]
            else:
                found = False
                break

        if found:
            return copy.deepcopy(curr)

        # No one stored this. Before falling back to a default, ask the resolution layers — this is where a
        # preset (or a future profile) speaks. Note the position: a caller's `default` argument is what *this
        # call site* believes is safe, and a resolver that had been consulted afterwards would be decorative.
        for resolver in tuple(getattr(self, "_resolvers", ())):
            # A layer that cannot resolve its key is a boot/configuration error, not permission to fall through
            # to a different default. In particular, swallowing a broken security resolver could make the
            # visible posture disagree with the policy that is meant to enforce it.
            value = resolver(path)
            if value is not UNSET:
                return copy.deepcopy(value)

        if default is not None:
            return default

        schema_default = self._resolve_schema_default(path)
        if schema_default is not None:
            return schema_default

        return None

    def set(self, path: str, value: Any) -> None:
        """
        Validates value against registered schema, writes setting, and triggers change listeners.
        """
        self.set_many({path: value})

    def set_many(self, updates: dict[str, Any]) -> None:
        """
        Atomically updates multiple settings with schema validation.
        """
        # 1. Validate all updates first before applying
        for path, val in updates.items():
            sub_schema, covered = self._find_sub_schema(path)
            if sub_schema is not None:
                validate_schema_value(val, sub_schema, path)

        # 2. Apply to internal dictionary
        changed_paths: list[str] = []
        for path, val in updates.items():
            parts = path.split(".")
            curr = self._data
            for part in parts[:-1]:
                if part not in curr or not isinstance(curr[part], dict):
                    curr[part] = {}
                curr = curr[part]
            old_val = curr.get(parts[-1])
            if old_val != val:
                curr[parts[-1]] = copy.deepcopy(val)
                changed_paths.append(path)

        # 3. Persist atomically
        if changed_paths:
            self._save()
            self._notify_subscribers(changed_paths)

    def on_change(
        self, callback: Callable[[list[str]], None], prefix: str = ""
    ) -> Callable[[], None]:
        """
        Subscribes a callback to setting changes. Returns an unsubscribe callable.
        If prefix is provided, only changes matching the prefix invoke the callback.
        """
        entry = (prefix, callback)
        self._subscribers.append(entry)

        def unsubscribe() -> None:
            if entry in self._subscribers:
                self._subscribers.remove(entry)

        return unsubscribe

    def _notify_subscribers(self, changed_paths: list[str]) -> None:
        """Notifies change subscribers matching path prefixes."""
        for prefix, cb in list(self._subscribers):
            if not prefix or any(
                p == prefix or p.startswith(prefix + ".") or prefix.startswith(p + ".")
                for p in changed_paths
            ):
                try:
                    cb(changed_paths)
                except Exception:
                    logger.exception("Error in settings change subscriber callback")

    def _load(self) -> None:
        """Loads settings from disk, applying migrations if necessary."""
        if not self.path.exists():
            self._data = {"schema_version": CURRENT_SCHEMA_VERSION}
            return

        try:
            raw = self.path.read_text(encoding="utf-8")
            data = json.loads(raw)
            if not isinstance(data, dict):
                data = {}
        except Exception as e:
            logger.warning(f"Failed to read settings from {self.path}: {e}")
            data = {}

        # Handle migrations
        version = data.get("schema_version", 1)
        migrated = False
        while version < CURRENT_SCHEMA_VERSION:
            next_ver = version + 1
            key = (version, next_ver)
            if key in self._migrations:
                data = self._migrations[key](data)
                data["schema_version"] = next_ver
                version = next_ver
                migrated = True
            else:
                data["schema_version"] = CURRENT_SCHEMA_VERSION
                migrated = True
                break

        self._data = data
        if migrated:
            self._save()

    def _save(self) -> None:
        """Atomically saves settings to file with mode 0600."""
        parent = self.path.parent
        parent.mkdir(parents=True, exist_ok=True)
        try:
            os.chmod(parent, 0o700)
        except Exception:
            pass

        self._data["schema_version"] = CURRENT_SCHEMA_VERSION
        content = json.dumps(self._data, indent=2).encode("utf-8")

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

    def get_view(self, namespace: str = "") -> SettingsView:
        """Returns a namespaced SettingsView implementation."""
        return PluginSettingsView(self, namespace)


class PluginSettingsView:
    """Read-only namespaced view of settings for a plugin."""

    def __init__(self, store: SettingsStore, namespace: str = ""):
        self._store = store
        self._namespace = namespace

    def get(self, path: str, default: Any = None) -> Any:
        full_path = path
        if self._namespace and not (
            path.startswith(self._namespace + ".")
            or path.startswith("kernel.")
            or path.startswith("plugins.")
            or path.startswith("extensions.")
        ):
            full_path = f"{self._namespace}.{path}"
        return self._store.get(full_path, default=default)

    def on_change(self, callback: Callable[[list[str]], None]) -> None:
        self._store.on_change(callback, prefix=self._namespace)
