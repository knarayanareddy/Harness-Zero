"""Canonical SDK implementations and kernel compatibility re-exports."""
from __future__ import annotations

from pathlib import Path

import pytest

from harness.kernel import sanitizer as kernel_sanitizer
from harness.kernel import validators as kernel_validators
from harness.sdk import sanitizer as sdk_sanitizer
from harness.sdk import validators as sdk_validators


def test_kernel_sanitizer_reexports_canonical_sdk_api_by_identity():
    assert kernel_sanitizer.sanitize_output is sdk_sanitizer.sanitize_output
    assert kernel_sanitizer.SECRET_PATTERNS is sdk_sanitizer.SECRET_PATTERNS
    assert [(pattern.pattern, replacement) for pattern, replacement in sdk_sanitizer.SECRET_PATTERNS] == [
        (r"AKIA[0-9A-Z]{16}", "[REDACTED AWS KEY]"),
        (r"ghp_[a-zA-Z0-9]{36}", "[REDACTED GITHUB TOKEN]"),
        (r"gho_[a-zA-Z0-9]{36}", "[REDACTED GITHUB TOKEN]"),
        (r"sk-[a-zA-Z0-9]{20,64}", "[REDACTED API KEY]"),
        (
            r"-----BEGIN [A-Z ]*PRIVATE KEY-----[\s\S]*?-----END [A-Z ]*PRIVATE KEY-----",
            "[REDACTED PRIVATE KEY]",
        ),
        (
            r"eyJ[a-zA-Z0-9_-]{10,}\.eyJ[a-zA-Z0-9_-]{10,}\.[a-zA-Z0-9_-]{10,}",
            "[REDACTED JWT]",
        ),
    ]


def test_sdk_sanitizer_preserves_pattern_exact_secret_and_type_edges():
    configured_secret = "configured-secret"
    text = "AKIA1234567890ABCDEF configured-secret tiny"
    assert sdk_sanitizer.sanitize_output(text, [configured_secret, "tiny"]) == (
        "[REDACTED AWS KEY] [REDACTED SECRET] tiny"
    )
    assert sdk_sanitizer.sanitize_output("minlen", ["minlen"]) == "[REDACTED SECRET]"
    assert sdk_sanitizer.sanitize_output("", [configured_secret]) == ""
    assert sdk_sanitizer.sanitize_output(None) is None
    assert sdk_sanitizer.sanitize_output(12) == 12


def test_kernel_validator_reexports_canonical_sdk_api_by_identity():
    assert kernel_validators.validate_session_id is sdk_validators.validate_session_id
    assert kernel_validators.validate_safe_filename is sdk_validators.validate_safe_filename
    assert kernel_validators.SESSION_ID_RE is sdk_validators.SESSION_ID_RE
    assert sdk_validators.SESSION_ID_RE.pattern == r"^session_[0-9a-f]{12}$"


def test_sdk_session_validator_preserves_values_and_rejection_message():
    session_id = "session_0123456789ab"
    assert sdk_validators.validate_session_id(session_id) == session_id
    with pytest.raises(ValueError) as exc_info:
        sdk_validators.validate_session_id(None)
    assert str(exc_info.value) == (
        "Invalid session_id: None. Must strictly match format '^session_[0-9a-f]{12}$'."
    )
    for invalid in (None, 123, "SESSION_0123456789AB", "session_0123456789a", "../escape", "slash/path"):
        with pytest.raises(ValueError):
            sdk_validators.validate_session_id(invalid)


def test_sdk_filename_validation_preserves_simple_name_and_containment(tmp_path: Path):
    parent = tmp_path / "parent"
    parent.mkdir()
    assert sdk_validators.validate_safe_filename("report.txt", parent) == "report.txt"

    for invalid in (None, 123, "", ".", "..", "../report.txt", "nested/report.txt", r"nested\report.txt"):
        with pytest.raises(ValueError):
            sdk_validators.validate_safe_filename(invalid, parent)

    outside = tmp_path / "outside"
    outside.mkdir()
    (parent / "escape").symlink_to(outside, target_is_directory=True)
    with pytest.raises(ValueError, match="Path traversal detected"):
        sdk_validators.validate_safe_filename("escape", parent)
