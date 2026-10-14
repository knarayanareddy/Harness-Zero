"""
Permission Modes Policy (§5.3, §9.3, PLG-06).
Absorbs TOOL-02.
Implements the 4 permission modes: bypass | plan | ask | allow.
"""
from __future__ import annotations

from typing import Any

from harness.sdk.context import PolicyContext
from harness.sdk.types import Action, Decision, RiskClass, ToolCall, ToolSpec


class PermissionModesPolicy:
    """
    Enforces permission mode configurations (order 900).
    Modes:
      - bypass: allows all tool executions directly
      - plan: denies any state-modifying tool (WRITE, EXEC, DESTRUCTIVE)
      - ask: requires interactive user approval for state-modifying actions
      - allow: allows executions unless blocked by destructive protection
    """

    name: str = "permission_modes"
    order: int = 900

    def __init__(self, settings: Any = None) -> None:
        self.settings = settings

    async def evaluate(self, call: ToolCall, spec: ToolSpec, ctx: PolicyContext) -> Decision:
        mode = "ask"
        destructive_blocking = True

        if self.settings is not None and hasattr(self.settings, "get"):
            mode = self.settings.get("kernel.security.permission_mode", "ask")
            destructive_blocking = self.settings.get(
                "kernel.security.destructive_blocking", True
            )

        # 1. Bypass mode: unconditionally allow
        if mode == "bypass":
            return Decision(action=Action.ALLOW, policy=self.name)

        # 2. Plan mode: deny all mutating actions
        if mode == "plan":
            if spec.risk_class in (RiskClass.WRITE, RiskClass.EXEC, RiskClass.DESTRUCTIVE):
                return Decision(
                    action=Action.DENY,
                    risk=spec.risk_class,
                    reason=f"Execution of '{call.name}' denied in plan mode.",
                    policy=self.name,
                )
            return Decision(action=Action.ALLOW, policy=self.name)

        # 3. Destructive command blocking rule
        if spec.risk_class == RiskClass.DESTRUCTIVE and destructive_blocking:
            return Decision(
                action=Action.DENY,
                risk=RiskClass.DESTRUCTIVE,
                reason=f"Destructive action '{call.name}' blocked by security policy.",
                policy=self.name,
            )

        # 4. Ask mode: require approval for WRITE, EXEC, DESTRUCTIVE
        if mode == "ask":
            if spec.risk_class in (RiskClass.WRITE, RiskClass.EXEC, RiskClass.DESTRUCTIVE):
                return Decision(
                    action=Action.ASK,
                    risk=spec.risk_class,
                    reason=f"Approval required for '{call.name}' ({spec.risk_class.value}).",
                    policy=self.name,
                )

        # 5. Allow mode (or safe read tools): allow
        return Decision(action=Action.ALLOW, policy=self.name)
