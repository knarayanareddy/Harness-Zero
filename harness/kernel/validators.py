"""
Validators for session IDs, filenames, and workspace path containment.
Absorbs SEC-06 and establishes STOP-03 invariants.
"""
from __future__ import annotations

import re
from pathlib import Path

# SEC-06: Strict session ID format prevents directory traversal
SESSION_ID_RE = re.compile(r"^session_[0-9a-f]{12}$")


def validate_session_id(session_id: str) -> str:
    """
    Validates that a session_id strictly conforms to session_[0-9a-f]{12}.
    Raises ValueError on traversal attempts, path separators, or malformed strings.
    """
    if not isinstance(session_id, str) or not SESSION_ID_RE.match(session_id):
        raise ValueError(
            f"Invalid session_id: {session_id!r}. "
            f"Must strictly match format '{SESSION_ID_RE.pattern}'."
        )
    return session_id


def validate_safe_filename(filename: str, parent_dir: Path | None = None) -> str:
    """
    Validates that a filename is a simple filename without path separators or traversal (.., .).
    If parent_dir is specified, ensures the resolved path strictly lies within parent_dir.
    Raises ValueError on traversal attempts or malformed names.
    """
    if not filename or not isinstance(filename, str):
        raise ValueError("Filename must be a non-empty string.")

    # Must be pure filename (no slashes, no backslashes, no traversal)
    if "/" in filename or "\\" in filename:
        raise ValueError(f"Path separators not permitted in filename: {filename!r}")

    p = Path(filename)
    if p.name != filename or filename in {".", ".."}:
        raise ValueError(f"Invalid filename or path traversal detected: {filename!r}")

    if parent_dir is not None:
        parent_resolved = Path(parent_dir).resolve()
        target_resolved = (parent_resolved / filename).resolve()
        if not target_resolved.is_relative_to(parent_resolved):
            raise ValueError(
                f"Path traversal detected: {filename!r} escapes parent {parent_dir}"
            )

    return filename
