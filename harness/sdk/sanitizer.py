"""
Secret leakage scanning and output sanitization utility ported from Tirith.
Absorbs TOOL-05 and satisfies KRN-06 Step 9.
"""
from __future__ import annotations

import re
from collections.abc import Sequence

# Regex patterns for well-known API keys, private keys, and tokens
SECRET_PATTERNS: list[tuple[re.Pattern[str], str]] = [
    (re.compile(r"AKIA[0-9A-Z]{16}"), "[REDACTED AWS KEY]"),
    (re.compile(r"ghp_[a-zA-Z0-9]{36}"), "[REDACTED GITHUB TOKEN]"),
    (re.compile(r"gho_[a-zA-Z0-9]{36}"), "[REDACTED GITHUB TOKEN]"),
    (re.compile(r"sk-[a-zA-Z0-9]{20,64}"), "[REDACTED API KEY]"),
    (
        re.compile(
            r"-----BEGIN [A-Z ]*PRIVATE KEY-----[\s\S]*?-----END [A-Z ]*PRIVATE KEY-----"
        ),
        "[REDACTED PRIVATE KEY]",
    ),
    (
        re.compile(r"eyJ[a-zA-Z0-9_-]{10,}\.eyJ[a-zA-Z0-9_-]{10,}\.[a-zA-Z0-9_-]{10,}"),
        "[REDACTED JWT]",
    ),
]


def sanitize_output(text: str, secret_values: Sequence[str] | None = None) -> str:
    """
    Sanitizes output text by redacting known secret token patterns
    and configured exact secret strings (with length >= 6).
    """
    if not text or not isinstance(text, str):
        return text

    sanitized = text
    # 1. Redact well-known secret patterns
    for pattern, replacement in SECRET_PATTERNS:
        sanitized = pattern.sub(replacement, sanitized)

    # 2. Redact exact secret values from Secrets store
    if secret_values:
        for val in secret_values:
            if isinstance(val, str) and len(val) >= 6 and val in sanitized:
                sanitized = sanitized.replace(val, "[REDACTED SECRET]")

    return sanitized
