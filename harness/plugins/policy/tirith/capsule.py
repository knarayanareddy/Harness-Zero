"""
Tirith Security Capsule (§PLG-17).
Audits commands for secret leaks, RFC1918 / loopback / metadata SSRF targets, and command blast radius.
"""
from __future__ import annotations

import re
from dataclasses import dataclass
from enum import Enum
from pathlib import Path


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
    SECRET_PATTERNS: list[tuple[str, str]] = [
        (r"AKIA[0-9A-Z]{16}", "AWS Access Key ID"),
        (r"ghp_[a-zA-Z0-9]{36}", "GitHub Personal Access Token"),
        (r"gho_[a-zA-Z0-9]{36}", "GitHub OAuth Token"),
        (r"sk-[a-zA-Z0-9]{32,64}", "OpenAI / Anthropic Secret Key"),
        (r"-----BEGIN [A-Z ]*PRIVATE KEY-----", "Private RSA/SSH Key"),
        (r"eyJ[a-zA-Z0-9_-]{10,}\.eyJ[a-zA-Z0-9_-]{10,}\.[a-zA-Z0-9_-]{10,}", "JSON Web Token (JWT)"),
    ]

    # 2. Blocked Network Targets (Cloud Metadata, RFC1918, 127.0.0.0/8, 169.254/16 SSRF Shield)
    BLOCKED_NETWORK_TARGETS: list[str] = [
        # Link-local and cloud metadata (169.254/16)
        r"169\.254\.\d+\.\d+",
        r"metadata\.google\.internal",
        r"100\.100\.100\.200",  # Alibaba Cloud metadata
        # Loopback IPv4 (127.0.0.0/8) and localhost
        r"127\.\d+\.\d+\.\d+",
        r"\blocalhost\b",
        # RFC1918 Private Address Spaces
        r"\b10\.\d+\.\d+\.\d+",                                # 10.0.0.0/8
        r"\b172\.(1[6-9]|2[0-9]|3[0-1])\.\d+\.\d+",           # 172.16.0.0/12
        r"\b192\.168\.\d+\.\d+",                               # 192.168.0.0/16
    ]

    @classmethod
    def scan_secrets(cls, text: str) -> list[str]:
        leaks = []
        for pattern, name in cls.SECRET_PATTERNS:
            if re.search(pattern, text):
                leaks.append(f"Leaked {name}")
        return leaks

    @classmethod
    def validate_network_target(cls, target: str) -> tuple[bool, str]:
        """Validates that a URL or IP target does not target blocked SSRF ranges."""
        for net_pattern in cls.BLOCKED_NETWORK_TARGETS:
            if re.search(net_pattern, target):
                return False, f"Blocked private/metadata SSRF target: {net_pattern}"
        return True, "OK"

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

        # 2. Scan for metadata & RFC1918/loopback SSRF targets
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

        tokens = command.split()
        if not tokens:
            return BlastRadius([], False, 0, "Empty command")

        base_cmd = tokens[0]

        if base_cmd in ("rm", "unlink", "shred"):
            # Recursive only if -r or -R is specified
            is_rec = any("-r" in t or "-R" in t for t in tokens)
            for t in tokens[1:]:
                if not t.startswith("-"):
                    affected.append(t)
            is_catastrophic = any(
                t in ("/", "~", "/*", "..")
                or t.startswith(("/", "~", ".."))
                or t == str(Path.home())
                for t in affected
            ) or ("--no-preserve-root" in tokens)

            if is_catastrophic and is_rec:
                score = 95
            elif is_rec:
                score = 65  # Moderate blast radius (WARN -> DESTRUCTIVE approval)
            else:
                score = 50  # Single file deletion (WARN -> DESTRUCTIVE)
            desc = f"File deletion of {len(affected)} target(s) (Recursive={is_rec})"

        elif base_cmd == "git":
            subcmd = tokens[1] if len(tokens) > 1 else ""
            if subcmd == "reset" and "--hard" in tokens:
                score = 95
                desc = "Hard git reset (discards all uncommitted working state)"
            elif subcmd == "clean" and any("-f" in t for t in tokens):
                score = 85
                desc = "Git clean force (untracked file purge)"
            elif subcmd == "push" and any(t in ("-f", "--force") for t in tokens):
                score = 90
                desc = "Git force push (overwrites remote git history)"
            else:
                score = 20
                desc = f"Git standard command: {subcmd}"

        elif base_cmd in ("chmod", "chown"):
            is_rec = any("-R" in t for t in tokens)
            score = 70 if is_rec else 30
            desc = f"Permission change command (Recursive={is_rec})"

        elif base_cmd in ("sudo", "mkfs", "dd"):
            score = 95
            desc = f"Privileged/destructive system command '{base_cmd}'"

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
        """Strips secret tokens from tool output."""
        clean = output
        for pattern, name in cls.SECRET_PATTERNS:
            clean = re.sub(pattern, f"[REDACTED_{name.upper().replace(' ', '_')}]", clean)
        return clean
