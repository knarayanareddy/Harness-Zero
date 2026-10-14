"""
Risk Classifier Policy (§5.3, §9.3, PLG-06).
Absorbs TOOL-04.
Best-effort classifier; containment is provided by WorkspaceJail and approvals.
"""
from __future__ import annotations

import re
import shlex
from pathlib import Path

from harness.sdk.context import PolicyContext
from harness.sdk.types import Action, Decision, RiskClass, ToolCall, ToolSpec

# Destructive shell regex patterns: (regex_pattern, reason_description)
DESTRUCTIVE_SHELL_PATTERNS: list[tuple[re.Pattern[str], str]] = [
    # Recursive file / directory deletion
    (re.compile(r"\brm\s+.*-[a-zA-Z]*[rR]"), "Destructive recursive file deletion (rm -r)"),
    (re.compile(r"\bshred\b"), "File shredding (shred)"),
    (re.compile(r"\bfind\b.*-delete\b"), "Find with -delete action"),
    (re.compile(r"\btruncate\s+.*-s\s*0\b"), "File truncation to zero bytes (truncate -s0)"),
    # Git destructive state and history actions
    (re.compile(r"\bgit\s+reset\s+--hard\b"), "Destructive git hard reset"),
    (re.compile(r"\bgit\s+push\s+.*(--force\b|-f\b|\+[\w/]+)"), "Destructive git force push"),
    (re.compile(r"\bgit\s+clean\s+.*-[a-zA-Z]*f"), "Destructive git clean"),
    (re.compile(r"\bgit\s+checkout\s+--\s+\."), "Discard unstaged git changes (git checkout -- .)"),
    (re.compile(r"\bgit\s+restore\s+(\.|--staged|\*)"), "Discard git worktree changes (git restore)"),
    (re.compile(r"\bgit\s+stash\s+(drop|clear)\b"), "Discards git stash (drop/clear)"),
    (re.compile(r"\bgit\s+branch\s+-D\b"), "Force delete git branch (git branch -D)"),
    # System and permission mutations
    (re.compile(r"\bsudo\b"), "Elevated root privileges via sudo"),
    (re.compile(r"\bchmod\s+.*-R\b"), "Recursive permission modification (chmod -R)"),
    (re.compile(r"\bchown\s+.*-R\b"), "Recursive ownership modification (chown -R)"),
    # Device and filesystem formatting
    (re.compile(r"\bmkfs(\.[a-zA-Z0-9]+)?\b"), "Filesystem format command (mkfs)"),
    (re.compile(r"\bdd\s+if="), "Raw block device write (dd)"),
    (re.compile(r">\s*/dev/(sd[a-z]|nvme[0-9]n[0-9]|disk[0-9])"), "Direct disk device redirection"),
    # Process and system sabotage
    (re.compile(r"\b(pkill|killall)\s+-9\b"), "Force mass process termination (pkill/killall -9)"),
    (re.compile(r":\(\)\s*\{\s*:\s*\|\s*:\s*&\s*\}\s*;\s*:"), "Bash fork bomb"),
    (re.compile(r"(curl|wget)\s+.*\|\s*(sh|bash)\b"), "Piped remote script execution (curl/wget | sh)"),
    # Shell obfuscation / variable expansion with mutating verbs (TOOL-04)
    (re.compile(r"\b[A-Za-z_][A-Za-z0-9_]*=\s*['\"]?(rm|shred|unlink)['\"]?\s*;.*\$"), "Obfuscated shell variable command execution"),
    (re.compile(r"\b(eval|exec)\s+.*(\$|rm|shred|mkfs|dd)"), "Dynamic shell evaluation with hazardous operations"),
    (re.compile(r"\b(bash|sh|python|perl|ruby)\s+-c\s+.*(rm|shred|unlink|truncate)"), "Inline interpreter execution with file-mutating verbs"),
    # Truncation operator on root/home/system targets
    (re.compile(r">\s*(~|/etc/|/System|/Library|/usr/|/bin/|/sbin/|\$HOME/)"), "File truncation in protected location"),
]

PROTECTED_PATHS: list[str] = [
    str(Path.home() / ".ssh"),
    str(Path.home() / ".aws"),
    str(Path.home() / ".gnupg"),
    str(Path.home() / ".zshrc"),
    str(Path.home() / ".bashrc"),
    "/etc",
    "/System",
    "/Library",
    "/usr/bin",
    "/bin",
    "/sbin",
]


class RiskClassifierPolicy:
    """
    Policy analyzing tool calls (primarily shell commands) for destructive
    patterns, protected path references, or unsafe shell expansions (order 50).
    """

    name: str = "risk_classifier"
    order: int = 50

    async def evaluate(self, call: ToolCall, spec: ToolSpec, ctx: PolicyContext) -> Decision:
        if call.name != "run_command":
            return Decision(action=Action.ALLOW, policy=self.name)

        cmd = str(call.arguments.get("command") or call.arguments.get("CommandLine", ""))
        if not cmd:
            return Decision(action=Action.ALLOW, policy=self.name)

        # 1. Regex pattern check for known destructive verbs and patterns
        for pattern, reason in DESTRUCTIVE_SHELL_PATTERNS:
            if pattern.search(cmd):
                return Decision(
                    action=Action.ASK,
                    risk=RiskClass.DESTRUCTIVE,
                    reason=f"Destructive command detected: {reason}",
                    policy=self.name,
                )

        # 2. Token-based protected path verification
        try:
            tokens = shlex.split(cmd)
        except Exception:
            tokens = cmd.split()

        for token in tokens:
            # Strip quotes and standard flags
            cleaned = token.strip("'\"")
            if cleaned.startswith("-"):
                continue
            for p in PROTECTED_PATHS:
                if cleaned == p or cleaned.startswith(p + "/"):
                    return Decision(
                        action=Action.ASK,
                        risk=RiskClass.DESTRUCTIVE,
                        reason=f"Command references protected system location: '{cleaned}'",
                        policy=self.name,
                    )

        # 3. Check for shell variable expansion
        if re.search(r"\$[A-Za-z_][A-Za-z0-9_]*", cmd) or re.search(r"\$\{[A-Za-z_][A-Za-z0-9_]*\}", cmd):
            # If variable expansion is used alongside deletion/mutation keywords
            if any(k in cmd for k in ("rm", "delete", "clean", "drop", "rf", "-f")):
                return Decision(
                    action=Action.ASK,
                    risk=RiskClass.DESTRUCTIVE,
                    reason="Shell variable expansion combined with file modification",
                    policy=self.name,
                )

        return Decision(action=Action.ALLOW, policy=self.name)
