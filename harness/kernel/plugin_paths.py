"""Where a discovered plugin lives on disk — the kernel's view of `harness/sdk/plugin_static.py`.

The facts themselves (the plugin directory, the client-module URL, and what may be served at it) live one layer
down, because the web frontend needs them too and `tests/kernel/test_import_boundaries.py` restricts frontends to
`harness.sdk`, `harness.kernel.commands` and `harness.kernel.bus`. This module stays as the kernel-side name the
loader, the catalogue and `settings_schema.py` already import, and re-exports rather than copying — a second
definition of "where a plugin lives" is how a catalogue and a browser end up disagreeing.
"""

from __future__ import annotations

from harness.sdk.plugin_static import (
    CLIENT_MODULE_ROOT,
    CLIENT_MODULE_SUFFIXES,
    client_module_file,
    client_module_trusted,
    client_module_url,
    plugin_dir,
)

__all__ = [
    "CLIENT_MODULE_ROOT",
    "CLIENT_MODULE_SUFFIXES",
    "client_module_file",
    "client_module_trusted",
    "client_module_url",
    "plugin_dir",
]
