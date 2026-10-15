"""
Canonical workspace-relative paths shared by the kernel and plugins.

The kernel hands every tool its own ``data_dir`` (``.harness/tools/<tool>``)
and every plugin its own (``.harness/plugins/<plugin>``). That is the right
default for private state, but a few artefacts are *shared* by design — the
session transcripts and the workspace memory database are written by one
component and read by others. Those must resolve to a single location
regardless of which plugin or context source asks, so they live here.
"""
from __future__ import annotations

from pathlib import Path

HARNESS_DIR_NAME = ".harness"
MEMORY_DB_NAME = "memory.db"
SESSIONS_DIR_NAME = "sessions"


def harness_dir(workspace: Path | str) -> Path:
    """``<workspace>/.harness`` — root of all per-workspace state."""
    return Path(workspace) / HARNESS_DIR_NAME


def memory_db_path(workspace: Path | str) -> Path:
    """
    ``<workspace>/.harness/memory.db`` — the single SQLite file holding the
    FTS5 transcript index *and* remembered facts.

    Written by the ``store-fts5-search`` plugin and the ``remember`` tool;
    read by the ``search_memory`` tool and the ``memories`` context source.
    """
    return harness_dir(workspace) / MEMORY_DB_NAME


def sessions_dir(workspace: Path | str) -> Path:
    """``<workspace>/.harness/sessions`` — one sub-directory per session."""
    return harness_dir(workspace) / SESSIONS_DIR_NAME
