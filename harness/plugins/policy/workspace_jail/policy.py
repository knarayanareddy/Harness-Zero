"""
Workspace Jail Policy Plugin (§5.3, PLG-06).
Enforces containment within active workspace boundaries.
"""
from __future__ import annotations

from pathlib import Path

from harness.sdk.context import PolicyContext
from harness.sdk.types import Action, Decision, RiskClass, ToolCall, ToolSpec


class WorkspaceJailPolicy:
    """Policy ensuring arguments with format 'workspace-path' stay within workspace (order 10)."""

    name: str = "workspace_jail"
    order: int = 10

    def __init__(self, workspace: Path | None = None) -> None:
        self._workspace = workspace

    async def evaluate(self, call: ToolCall, spec: ToolSpec, ctx: PolicyContext) -> Decision:
        ws = ctx.workspace if hasattr(ctx, "workspace") and ctx.workspace else self._workspace
        if not ws:
            return Decision(action=Action.ALLOW, policy=self.name)

        resolved_ws = Path(ws).resolve()
        props = spec.input_schema.get("properties", {}) if spec.input_schema else {}

        for arg_name, schema in props.items():
            if schema.get("format") == "workspace-path" and arg_name in call.arguments:
                val = call.arguments[arg_name]
                if isinstance(val, str):
                    try:
                        resolved_path = (resolved_ws / val).resolve()
                        if not resolved_path.is_relative_to(resolved_ws):
                            return Decision(
                                action=Action.DENY,
                                risk=RiskClass.DESTRUCTIVE,
                                reason=f"Path traversal outside workspace: '{val}' resolves to '{resolved_path}'",
                                policy=self.name,
                            )
                    except Exception as err:
                        return Decision(
                            action=Action.DENY,
                            risk=RiskClass.DESTRUCTIVE,
                            reason=f"Invalid path argument '{val}': {err}",
                            policy=self.name,
                        )

        return Decision(action=Action.ALLOW, policy=self.name)
