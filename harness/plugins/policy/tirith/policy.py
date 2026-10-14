"""
Tirith Policy Plugin (§PLG-17).
Audits commands at order 60: BLOCK -> DENY, WARN -> raise risk to DESTRUCTIVE.
Enforces SSRF metadata & RFC1918 shielding and credential leak prevention.
"""
from __future__ import annotations

from pathlib import Path
from typing import Any

from harness.sdk.context import PolicyContext
from harness.sdk.types import Action, Decision, RiskClass, ToolCall, ToolSpec

from .capsule import SecurityVerdict, TirithSecurityCapsule


class TirithPolicy:
    """Tirith AST & security shield policy (order 60)."""

    name: str = "tirith"
    order: int = 60

    async def evaluate(self, call: ToolCall, spec: ToolSpec, ctx: PolicyContext) -> Decision:
        ws = ctx.workspace if hasattr(ctx, "workspace") and ctx.workspace else Path.cwd()
        args: dict[str, Any] = call.arguments if isinstance(call.arguments, dict) else {}

        # 1. Check for command execution arguments
        cmd = args.get("command") or args.get("CommandLine") or args.get("cmd")
        if isinstance(cmd, str) and cmd.strip():
            audit = TirithSecurityCapsule.audit_command(cmd, Path(ws))
            if audit.verdict == SecurityVerdict.BLOCK:
                return Decision(
                    action=Action.DENY,
                    risk=RiskClass.DESTRUCTIVE,
                    reason="; ".join(audit.reasons),
                    policy=self.name,
                )
            if audit.verdict == SecurityVerdict.WARN:
                return Decision(
                    action=Action.ALLOW,
                    risk=RiskClass.DESTRUCTIVE,
                    reason="; ".join(audit.reasons),
                    policy=self.name,
                )

        # 2. Check for network target arguments (SSRF protection)
        for key in ("url", "target", "host", "endpoint"):
            target_val = args.get(key)
            if isinstance(target_val, str) and target_val.strip():
                ok, reason = TirithSecurityCapsule.validate_network_target(target_val)
                if not ok:
                    return Decision(
                        action=Action.DENY,
                        risk=RiskClass.DESTRUCTIVE,
                        reason=reason,
                        policy=self.name,
                    )

        return Decision(action=Action.ALLOW, policy=self.name)
