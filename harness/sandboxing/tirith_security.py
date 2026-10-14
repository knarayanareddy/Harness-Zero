"""
Tirith Security Capsule (Hermes Agent Pattern).
Dissected from ~/.hermes/bin/tirith.
Provides blast-radius command preview, secret leak scanning, and metadata/CIDR network shielding.
"""
from __future__ import annotations

import re
from dataclasses import dataclass
from enum import Enum
from pathlib import Path
from typing import Any


class SecurityVerdict(str, Enum):
    ALLOW = "ALLOW"
    WARN = "WARN"
    BLOCK = "BLOCK"


@dataclass
class BlastRadius:
    affected_paths: list[str]
    is_recursive: bool
    risk_score: int  # 0 to 100
    description: str


@dataclass
class SecurityAuditResult:
    verdict: SecurityVerdict
    reasons: list[str]
    leaked_secrets: list[str]
    blast_radius: BlastRadius


class TirithSecurityCapsule:
    # 1. Secret Leak Signatures
    SECRET_PATTERNS = [
        (r"AKIA[0-9A-Z]{16}", "AWS Access Key ID"),
        (r"ghp_[a-zA-Z0-9]{36}", "GitHub Personal Access Token"),
        (r"gho_[a-zA-Z0-9]{36}", "GitHub OAuth Token"),
        (r"sk-[a-zA-Z0-9]{32,64}", "OpenAI / Anthropic Secret Key"),
        (r"-----BEGIN [A-Z ]*PRIVATE KEY-----", "Private RSA/SSH Key"),
        (r"eyJ[a-zA-Z0-9_-]{10,}\.eyJ[a-zA-Z0-9_-]{10,}\.[a-zA-Z0-9_-]{10,}", "JSON Web Token (JWT)"),
    ]

    # 2. Blocked Network Targets (Cloud Metadata & SSRF Shield)
    BLOCKED_NETWORK_TARGETS = [
        r"169\.254\.169\.254",  # AWS/GCP/Azure link-local metadata
        r"metadata\.google\.internal",
        r"100\.100\.100\.200",  # Alibaba Cloud metadata
        r"127\.0\.0\.1:(22|23|25|3389)", # Internal sensitive ports
    ]

    @classmethod
    def scan_secrets(cls, text: str) -> list[str]:
        leaks = []
        for pattern, name in cls.SECRET_PATTERNS:
            if re.search(pattern, text):
                leaks.append(f"Leaked {name}")
        return leaks

    @classmethod
    def validate_network_target(cls, target: str) -> Any:
        for net_pattern in cls.BLOCKED_NETWORK_TARGETS:
            if re.search(net_pattern, target):
                return type("NetResult", (), {"allowed": False, "reason": f"Blocked cloud metadata / sensitive target: {net_pattern}"})()
        return type("NetResult", (), {"allowed": True, "reason": "OK"})()

    @classmethod
    def audit_command(cls, command: str, cwd: Path) -> SecurityAuditResult:
        reasons: list[str] = []
        leaked_secrets: list[str] = []
        verdict = SecurityVerdict.ALLOW

        # 1. Scan for leaked credentials inside command string
        for pattern, name in cls.SECRET_PATTERNS:
            if re.search(pattern, command):
                verdict = SecurityVerdict.BLOCK
                reasons.append(f"Command contains leaked {name}")
                leaked_secrets.append(name)

        # 2. Scan for metadata SSRF targets
        for net_pattern in cls.BLOCKED_NETWORK_TARGETS:
            if re.search(net_pattern, command):
                verdict = SecurityVerdict.BLOCK
                reasons.append(f"Command attempts SSRF access to blocked target: {net_pattern}")

        # 3. Calculate Blast-Radius
        blast = cls.calculate_blast_radius(command, cwd)
        if blast.risk_score >= 80:
            verdict = SecurityVerdict.BLOCK
            reasons.append(f"Excessive blast-radius risk ({blast.risk_score}/100): {blast.description}")
        elif blast.risk_score >= 50 and verdict == SecurityVerdict.ALLOW:
            verdict = SecurityVerdict.WARN
            reasons.append(f"Moderate blast-radius risk ({blast.risk_score}/100): {blast.description}")

        return SecurityAuditResult(
            verdict=verdict,
            reasons=reasons,
            leaked_secrets=leaked_secrets,
            blast_radius=blast,
        )

    @classmethod
    def calculate_blast_radius(cls, command: str, cwd: Path) -> BlastRadius:
        affected = []
        is_rec = False
        score = 10

        # Heuristic command parsing
        tokens = command.split()
        if not tokens:
            return BlastRadius([], False, 0, "Empty command")

        base_cmd = tokens[0]

        if base_cmd in ("rm", "unlink", "shred"):
            is_rec = any("-r" in t for t in tokens) or any("-f" in t for t in tokens)
            score = 90 if is_rec else 50
            for t in tokens[1:]:
                if not t.startswith("-"):
                    affected.append(t)
            desc = f"File deletion of {len(affected)} target(s) (Recursive={is_rec})"

        elif base_cmd == "git":
            subcmd = tokens[1] if len(tokens) > 1 else ""
            if subcmd == "reset" and "--hard" in tokens:
                score = 95
                desc = "Hard git reset (discards all uncommitted working state)"
            elif subcmd == "clean" and "-f" in command:
                score = 85
                desc = "Git clean force (untracked file purge)"
            elif subcmd == "push" and "--force" in command:
                score = 90
                desc = "Git force push (overwrites remote git history)"
            else:
                score = 20
                desc = f"Git standard command: {subcmd}"

        elif base_cmd in ("chmod", "chown"):
            is_rec = "-R" in command
            score = 70 if is_rec else 30
            desc = f"Permission change command (Recursive={is_rec})"

        else:
            desc = f"Standard execution of '{base_cmd}'"

        return BlastRadius(
            affected_paths=affected,
            is_recursive=is_rec,
            risk_score=score,
            description=desc,
        )

    @classmethod
    def sanitize_output(cls, output: str) -> str:
        """Strips secret tokens from tool output before returning to model."""
        clean = output
        for pattern, name in cls.SECRET_PATTERNS:
            clean = re.sub(pattern, f"[REDACTED_{name.upper().replace(' ', '_')}]", clean)
        return clean
