"""
Harness Kernel package.
Foundation primitives for sovereignty, sandboxing, policy enforcement, settings, secrets, context assembly, and compaction.
"""
from __future__ import annotations

from harness.kernel.approvals import ApprovalBroker, ApprovalRequest
from harness.kernel.breaker import CircuitBreaker, CircuitState
from harness.kernel.bus import EventBus
from harness.kernel.commands import Commands
from harness.kernel.compaction import (
    COMPACTION_SYSTEM_PROMPT,
    Compactor,
    estimate_request_tokens,
    estimate_tokens,
)
from harness.kernel.context import (
    ContextAssembler,
    HookContextImpl,
    PolicyContextImpl,
    TurnContextImpl,
    records_to_messages,
)
from harness.kernel.gate import (
    PermissionModesPolicy,
    PolicyGate,
    ToolContextImpl,
    WorkspaceJailPolicy,
)
from harness.kernel.inbox import BackgroundInbox, WakeupEvent
from harness.kernel.jail import WorkspaceJail
from harness.kernel.registry import (
    PluginRegistry,
    TrustTier,
    clamp_capabilities,
)
from harness.kernel.runner import KernelServicesImpl, SingleLoopStrategy, TurnRunner
from harness.kernel.sanitizer import sanitize_output
from harness.kernel.secrets import PluginSecretsView, Secrets, migrate_legacy_config
from harness.kernel.session import Session, SessionManager
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
    "PluginRegistry",
    "TrustTier",
    "clamp_capabilities",
    "ApprovalBroker",
    "ApprovalRequest",
    "PolicyGate",
    "WorkspaceJailPolicy",
    "PermissionModesPolicy",
    "ToolContextImpl",
    "HookContextImpl",
    "PolicyContextImpl",
    "TurnContextImpl",
    "sanitize_output",
    "ContextAssembler",
    "Compactor",
    "estimate_tokens",
    "estimate_request_tokens",
    "records_to_messages",
    "COMPACTION_SYSTEM_PROMPT",
    "CircuitBreaker",
    "CircuitState",
    "BackgroundInbox",
    "WakeupEvent",
    "TurnRunner",
    "KernelServicesImpl",
    "SingleLoopStrategy",
    "Commands",
    "Session",
    "SessionManager",
]
