"""Bounded skill reads relative to pinned, no-follow directory descriptors.

Workspace identifiers select one child, never a resource root. Builtin resources
come from the installed package, with the explicit source-checkout tree retained
as a fallback. Platforms without descriptor-relative no-follow reads fail closed.
"""
from __future__ import annotations

import os
import stat
from collections.abc import Iterator
from contextlib import contextmanager
from pathlib import Path

MAX_SKILL_BYTES = 1024 * 1024
_open = os.open


def skill_roots(workspace: Path | None) -> list[Path]:
    package_root = Path(__file__).resolve().parents[1]
    builtin_root = package_root / "builtin" / "skills"
    if not builtin_root.exists():
        # Source-checkout fallback: the original resource tree is beside `harness/`.
        builtin_root = package_root.parent / "builtin" / "skills"
    roots = [builtin_root]
    if workspace is not None:
        roots.extend([workspace / ".harness" / "skills", workspace / "skills"])
    return roots


def _valid_name(name: str) -> bool:
    return bool(name) and name not in {".", ".."} and not any(c in name for c in "/\\\0:")


@contextmanager
def _directory(path: Path) -> Iterator[int]:
    if os.open not in os.supports_dir_fd or os.listdir not in os.supports_fd or not hasattr(os, "O_NOFOLLOW"):
        raise OSError("Descriptor-confined skill reads are unavailable on this platform")
    path = path.absolute()  # Do not resolve an untrusted workspace symlink.
    if ".." in path.parts:
        raise ValueError("Skill roots cannot traverse parents")
    flags = os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW
    fd = _open(path.anchor, flags)
    try:
        for part in path.parts[1:]:
            child = _open(part, flags, dir_fd=fd)
            os.close(fd)
            fd = child
        yield fd
    finally:
        os.close(fd)


def _read_at(root_fd: int, name: str) -> str:
    if not _valid_name(name):
        raise ValueError("Skill name must be a single identifier")
    directory_fd = _open(name, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW, dir_fd=root_fd)
    try:
        fd = _open("SKILL.md", os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK, dir_fd=directory_fd)
        try:
            info = os.fstat(fd)
            if not stat.S_ISREG(info.st_mode):
                raise ValueError("Skill content must be a regular file")
            if info.st_size > MAX_SKILL_BYTES:
                raise ValueError("Skill content exceeds the byte limit")
            content = bytearray()
            while len(content) <= MAX_SKILL_BYTES:
                chunk = os.read(fd, min(65536, MAX_SKILL_BYTES + 1 - len(content)))
                if not chunk:
                    return content.decode("utf-8")
                content.extend(chunk)
            raise ValueError("Skill content exceeds the byte limit")
        finally:
            os.close(fd)
    finally:
        os.close(directory_fd)


def read_skill(name: str, workspace: Path | None) -> str | None:
    """Read a single skill, preserving exact-name then case/underscore aliases."""
    if not _valid_name(name):
        return None
    normalized = name.lower().replace("_", "-")
    for root in skill_roots(workspace):
        try:
            with _directory(root) as fd:
                names = [name, normalized]
                names.extend(n for n in sorted(os.listdir(fd)) if n.lower().replace("_", "-") == normalized)
                for candidate in dict.fromkeys(names):
                    try:
                        return _read_at(fd, candidate)
                    except (OSError, ValueError):
                        continue
        except (OSError, ValueError):
            continue
    return None


def iter_skill_contents(workspace: Path | None) -> Iterator[tuple[Path, str]]:
    """Enumerate and read using the same pinned roots as explicit viewing.

    Paths are display metadata only; callers must not reopen them for content.
    """
    for root in skill_roots(workspace):
        try:
            with _directory(root) as fd:
                for name in sorted(os.listdir(fd)):
                    try:
                        content = _read_at(fd, name)
                    except (OSError, ValueError):
                        continue
                    yield root / name / "SKILL.md", content
        except (OSError, ValueError):
            continue
