"""WS-02: declared settings are the only settings.

Two directions, both mechanical, both cheap to keep:

* every literal settings read in `harness/` resolves to a key that some registered schema declares —
  otherwise the key has no default, no validation and no place in the UI, and its reader is quietly
  dead (that is how provider base URLs ended up unreachable, R36);
* every key a schema declares *for the UI* is read by someone — otherwise the settings panel advertises
  a control that changes nothing (R38: `kernel.max_tool_iterations` was in the panel and no code read
  it; the strategies' own literal won).

The resolution rules live in `harness.kernel.catalog.setting_reads`, which mirrors
`PluginSettingsView.get`: inside a plugin a bare key belongs to that plugin's namespace.
"""

from __future__ import annotations

from harness.kernel.catalog import settings_report


def test_every_setting_read_is_declared():
    report = settings_report()
    undeclared = report["undeclared"]
    assert not undeclared, "settings read with no schema declaration:\n" + "\n".join(
        f"  {u['where']} reads {u['key']} — declare it in that plugin's settings.schema.json (or KERNEL_SCHEMA)"
        for u in undeclared
    )


def test_no_ui_setting_is_unread():
    report = settings_report()
    assert report["never_read"] == [], (
        "settings declared for the UI that no code reads — either wire them up or drop them: "
        + ", ".join(report["never_read"])
    )


def test_the_surface_is_not_hollow():
    """Guards against satisfying the two checks above by deleting every declaration."""
    report = settings_report()
    assert report["declared"] >= 30, f"only {report['declared']} settings declared — the catalogue collapsed"
    assert report["reads"] >= 25, f"only {report['reads']} settings reads found — the scanner stopped working"
