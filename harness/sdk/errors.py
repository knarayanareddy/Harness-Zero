"""
SDK Error Hierarchy for Harness-Zero.
All SDK exceptions inherit from HarnessError.
"""
from __future__ import annotations

from typing import Literal

ProviderErrorKind = Literal[
    "auth", "rate_limit", "server", "timeout", "bad_request", "unavailable"
]


class HarnessError(Exception):
    """Base error for all Harness-Zero SDK errors."""
    pass


class ProviderError(HarnessError):
    """Raised when an LLM provider request fails."""

    def __init__(
        self,
        provider: str,
        model: str,
        status_code: int | None = None,
        retryable: bool = False,
        kind: ProviderErrorKind = "server",
        message: str = "",
    ):
        full_msg = f"[{provider}/{model}] {message}"
        if status_code:
            full_msg += f" (HTTP {status_code})"
        super().__init__(full_msg)
        self.provider = provider
        self.model = model
        self.status_code = status_code
        self.retryable = retryable
        self.kind = kind
        self.message = message

    def to_dict(self) -> dict[str, object]:
        return {
            "provider": self.provider,
            "model": self.model,
            "status_code": self.status_code,
            "retryable": self.retryable,
            "kind": self.kind,
            "message": self.message,
        }


class PathOutsideWorkspace(HarnessError, PermissionError):
    """Raised when an operation attempts to access or modify a path outside workspace boundaries."""
    pass


class CapabilityDenied(HarnessError, PermissionError):
    """Raised when a plugin attempts an action requiring a capability it was not granted."""

    def __init__(self, capability: str, plugin: str = "", message: str = ""):
        msg = f"Capability '{capability}' denied"
        if plugin:
            msg += f" for plugin '{plugin}'"
        if message:
            msg += f": {message}"
        super().__init__(msg)
        self.capability = capability
        self.plugin = plugin


class ToolInputError(HarnessError, ValueError):
    """Raised when tool arguments fail validation or are malformed."""
    pass


class ApprovalDenied(HarnessError, PermissionError):
    """Raised when a user or policy denies an approval request."""
    pass


class SpendCapExceeded(HarnessError, RuntimeError):
    """Raised when turn or session token spend exceeds the configured spend cap."""
    pass


class PluginLoadError(HarnessError):
    """Raised when a plugin manifest fails validation or plugin cannot be loaded."""
    pass


class IncompatiblePlugin(PluginLoadError):
    """Raised when a plugin requires an incompatible SDK or harness version."""
    pass


class SettingsValidationError(HarnessError, ValueError):
    """Raised when a setting value fails schema validation or format requirements."""
    pass


class TurnInProgress(HarnessError, RuntimeError):
    """Raised when submit_turn is called while another turn is already executing in the same session."""

    def __init__(self, session_id: str, message: str = ""):
        super().__init__(message or f"Turn already in progress for session '{session_id}'")
        self.session_id = session_id
        self.status_code = 409


