"""Where a discovered plugin lives on disk.

`DiscoveredPlugin` carries the manifest and its path; the plugin *directory* is what README,
`settings.schema.json` and the conformance test are resolved against. Kept in one place because the
catalog, `plugins.info` and the loader all need it (and `dp.path` does not exist).
"""

from __future__ import annotations

from pathlib import Path
from typing import Any


def plugin_dir(discovered: Any) -> Path:
    """The directory holding ``harness_plugin.toml`` for a discovered plugin."""
    manifest = getattr(discovered, "manifest", None)
    dir_path = getattr(manifest, "dir_path", None)
    if dir_path:
        return Path(dir_path)
    manifest_path = getattr(discovered, "manifest_path", None)
    if manifest_path:
        return Path(manifest_path).parent
    origin = getattr(discovered, "origin", "")
    return Path(origin).parent if origin else Path.cwd()
