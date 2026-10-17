"""
Permission Modes Policy (§5.3, §9.3, PLG-06).
Absorbs TOOL-02. Implements the 4 permission modes: bypass | plan | ask | allow, with WS-06's sandbox availability
as an enforcement fact rather than an optimistic boolean setting.
"""
from __future__ import annotations

from typing import Any

from harness.sdk.context import PolicyContext
from harness.sdk.types import Action, Capability, Decision, RiskClass, ToolCall, ToolSpec


class PermissionModesPolicy:
    """Enforces permission mode configurations (order 900) and fails closed around unavailable confinement."""

    name: str = "permission_modes"
    order: int = 900

    def __init__(self, settings: Any = None, sandbox: Any = None) -> None:
        self.settings = settings
        self.sandbox = sandbox

    async def evaluate(self, call: ToolCall, spec: ToolSpec, ctx: PolicyContext) -> Decision:
        mode = "ask"
        destructive_blocking = True
        sandbox_mode = "off"
        exec_in_sandbox = "ask"
        if self.settings is not None and hasattr(self.settings, "get"):
            mode = self.settings.get("kernel.security.permission_mode", "ask")
            destructive_blocking = self.settings.get("kernel.security.destructive_blocking", True)
            sandbox_mode = self.settings.get("kernel.sandbox.mode", "off")
            exec_in_sandbox = self.settings.get("kernel.security.exec_in_sandbox", "ask")

        sandbox_status: dict[str, Any] = self.sandbox.status() if self.sandbox is not None else {
            "mode": sandbox_mode,
            "backend": "none",
            "available": False,
            "enforcement": "none",
            "reason": "No sandbox service is registered.",
            "network": False,
        }
        uses_executor = spec.risk_class == RiskClass.EXEC or "exec" in spec.capabilities
        decision_metadata = {"sandbox": sandbox_status} if uses_executor else {}
        capability_bypass = Capability.APPROVAL_BYPASS in getattr(ctx, "capabilities", frozenset())

        # 1. Plan is a deliberate policy choice, so it reports the ordinary plan denial even on a host that also
        # lacks bubblewrap. An explicit, non-grantable-to-plugins approval:bypass principal remains distinct.
        if mode == "plan" and not capability_bypass and spec.risk_class in (RiskClass.WRITE, RiskClass.EXEC, RiskClass.DESTRUCTIVE):
            return Decision(
                action=Action.DENY,
                risk=spec.risk_class,
                reason=f"Execution of '{call.name}' denied in plan mode.",
                policy=self.name,
                metadata=decision_metadata,
            )

        # 2. A malformed sandbox mode is never interpreted as `off`; a required backend that cannot enforce
        # confinement is a hard deny even if the caller explicitly selected `bypass`.
        if uses_executor and sandbox_mode not in ("required", "preferred", "off"):
            return Decision(
                action=Action.DENY,
                risk=spec.risk_class,
                reason=f"Invalid sandbox mode {sandbox_mode!r}; refusing executor call.",
                policy=self.name,
                metadata=decision_metadata,
            )
        if uses_executor and sandbox_mode == "required" and not sandbox_status.get("available"):
            detail = sandbox_status.get("reason") or "no OS sandbox backend is available"
            return Decision(
                action=Action.DENY,
                risk=spec.risk_class,
                reason=f"sandbox unavailable on this host: {detail}",
                policy=self.name,
                metadata=decision_metadata,
            )

        # 3. `bypass` is still an explicit automation/test override (never a preset); only the required OS
        # boundary above is non-bypassable. `open` is not `bypass`: its destructive protection remains on.
        if mode == "bypass" or capability_bypass:
            return Decision(action=Action.ALLOW, policy=self.name, metadata=decision_metadata)

        # 4. Destructive command blocking remains independent of presets and optional confinement.
        if spec.risk_class == RiskClass.DESTRUCTIVE and destructive_blocking:
            return Decision(
                action=Action.DENY,
                risk=RiskClass.DESTRUCTIVE,
                reason=f"Destructive action '{call.name}' blocked by security policy.",
                policy=self.name,
                metadata=decision_metadata,
            )

        # 4. An unavailable preferred backend turns executor calls into an approval, even when `permission_mode`
        # is `allow`. `bypass` is the explicit escape hatch used by tests/automation; the executor result will
        # still truthfully report `enforcement: none`. `required` has already denied above.
        if uses_executor and sandbox_mode == "preferred" and not sandbox_status.get("available"):
            if mode != "bypass" and not capability_bypass:
                detail = sandbox_status.get("reason") or "no OS sandbox backend is available"
                return Decision(
                    action=Action.ASK,
                    risk=spec.risk_class,
                    reason=f"Sandbox unavailable; approve unconfined execution for '{call.name}': {detail}",
                    policy=self.name,
                    metadata=decision_metadata,
                )

        # 5. `standard` (or an explicit per-setting choice) may allow EXEC only if a backend was actually probed
        # available and the executor seam will wrap the process. Missing backend took the ASK path above.
        if (
            uses_executor
            and spec.risk_class == RiskClass.EXEC
            and mode == "ask"
            and sandbox_mode != "off"
            and sandbox_status.get("available") is True
            and exec_in_sandbox == "allow"
        ):
            return Decision(action=Action.ALLOW, policy=self.name, metadata=decision_metadata)

        # 7. Ask mode: require interactive user approval for state-modifying actions.
        if mode == "ask" and spec.risk_class in (RiskClass.WRITE, RiskClass.EXEC, RiskClass.DESTRUCTIVE):
            return Decision(
                action=Action.ASK,
                risk=spec.risk_class,
                reason=f"Approval required for '{call.name}' ({spec.risk_class.value}).",
                policy=self.name,
                metadata=decision_metadata,
            )

        # 8. Allow mode (or safe read tools): allow, unless the fail-closed sandbox checks above said otherwise.
        return Decision(action=Action.ALLOW, policy=self.name, metadata=decision_metadata)
