"""
Workspace Jail & Containment Boundary.
Enforces that file reads, writes, and working directories are strictly
contained within the workspace boundary and protects .git internals.
"""
from __future__ import annotations

import os
from pathlib import Path

from harness.sdk.errors import PathOutsideWorkspace


class WorkspaceJail:
    def __init__(self, root: str | os.PathLike):
        self.root = Path(root).expanduser().resolve()

    def _is_git_internal(self, rp: Path) -> bool:
        """
        Checks if a resolved path is inside the .git directory.
        Allows writing to .git/info/exclude only.
        """
        git_dir = (self.root / ".git").resolve()
        if rp == git_dir or git_dir in rp.parents:
            allowed_exclude = (git_dir / "info" / "exclude").resolve()
            if rp == allowed_exclude:
                return False
            return True
        return False

    def resolve(self, raw: str | os.PathLike, *, for_write: bool = False) -> Path:
        """
        Resolves raw path relative to self.root, ensuring strict containment.
        Follows symlinks (rp must be within self.root).
        If for_write=True, prevents modifying .git internals except .git/info/exclude.
        Raises PathOutsideWorkspace if outside jail or forbidden.
        """
        p = Path(raw).expanduser()
        p = p if p.is_absolute() else (self.root / p)
        try:
            rp = p.resolve(strict=False)
        except Exception as e:
            raise PathOutsideWorkspace(f"Cannot resolve path '{raw}': {e}") from e

        # Containment check: rp must equal root or have root as a parent
        if rp != self.root and self.root not in rp.parents:
            raise PathOutsideWorkspace(f"Access blocked: path '{raw}' ({rp}) is outside workspace root '{self.root}'")

        if for_write and self._is_git_internal(rp):
            raise PathOutsideWorkspace(f"Access blocked: path '{raw}' ({rp}) is inside .git internal directory")

        return rp
