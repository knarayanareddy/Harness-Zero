"""
Tool Safety Guardrails & Permission Policy.
Classifies commands into safe execution vs interactive human approval.
"""
from __future__ import annotations

from dataclasses import dataclass
from enum import Enum
from pathlib import Path
import re
from typing import Callable, Optional, Tuple


class RiskLevel(str, Enum):
    SAFE = "SAFE"
    ELEVATED = "ELEVATED"
    DESTRUCTIVE = "DESTRUCTIVE"


@dataclass
class RiskEvaluation:
    level: RiskLevel
    requires_approval: bool
    reason: str


class SecurityPolicy:
    DESTRUCTIVE_SHELL_PATTERNS = [
        (r"\brm\s+(-[a-zA-Z]*r[a-zA-Z]*f?|-f[a-zA-Z]*r[a-zA-Z]*)\b", "Destructive recursive file deletion (rm -rf)"),
        (r"\bgit\s+reset\s+--hard\b", "Destructive git hard reset (discards uncommitted work)"),
        (r"\bgit\s+push\s+.*--force\b", "Destructive git force push"),
        (r"\bgit\s+clean\s+-[a-zA-Z]*f\b", "Destructive git clean"),
        (r"\bsudo\b", "Elevated root privileges via sudo"),
        (r"\bmkfs(\.[a-zA-Z0-9]+)?\b", "Filesystem format command"),
        (r"\bdd\s+if=", "Raw block device write (dd)"),
        (r"\b(pkill|killall)\s+-9\b", "Force mass process termination"),
        (r">\s*/dev/(sd[a-z]|nvme[0-9]n[0-9]|disk[0-9])", "Direct disk write redirection"),
    ]

    PROTECTED_SYSTEM_PATHS = [
        Path.home() / ".ssh",
        Path.home() / ".aws",
        Path.home() / ".gnupg",
        Path.home() / ".zshrc",
        Path.home() / ".bashrc",
        Path("/etc"),
        Path("/System"),
        Path("/Library"),
        Path("/usr/bin"),
        Path("/bin"),
        Path("/sbin"),
    ]

    @classmethod
    def evaluate_command_risk(cls, command: str, cwd: Path) -> RiskEvaluation:
        # 1. Check destructive patterns
        for pattern, reason in cls.DESTRUCTIVE_SHELL_PATTERNS:
            if re.search(pattern, command):
                return RiskEvaluation(
                    level=RiskLevel.DESTRUCTIVE,
                    requires_approval=True,
                    reason=reason,
                )

        # 2. Check protected paths
        for p in cls.PROTECTED_SYSTEM_PATHS:
            if str(p) in command:
                return RiskEvaluation(
                    level=RiskLevel.ELEVATED,
                    requires_approval=True,
                    reason=f"Command references protected system path: {p}",
                )

        return RiskEvaluation(
            level=RiskLevel.SAFE,
            requires_approval=False,
            reason="Safe execution",
        )

    @classmethod
    def evaluate_file_write_risk(
        cls, target_file: Path, workspace_root: Path
    ) -> RiskEvaluation:
        resolved = target_file.resolve()
        ws_resolved = workspace_root.resolve()

        # Check protected paths
        for p in cls.PROTECTED_SYSTEM_PATHS:
            try:
                resolved.relative_to(p.resolve())
                return RiskEvaluation(
                    level=RiskLevel.DESTRUCTIVE,
                    requires_approval=True,
                    reason=f"Attempting to write to protected system path: {resolved}",
                )
            except ValueError:
                pass

        try:
            resolved.relative_to(ws_resolved)
            return RiskEvaluation(
                level=RiskLevel.SAFE,
                requires_approval=False,
                reason="Safe workspace write",
            )
        except ValueError:
            return RiskEvaluation(
                level=RiskLevel.ELEVATED,
                requires_approval=True,
                reason=f"File write is outside active workspace: {resolved}",
            )
