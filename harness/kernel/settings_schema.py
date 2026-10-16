"""Helpers that turn registered JSON Schemas into the settings surface (WS-02).

``Commands.settings_schema`` (the UI's source of truth) and ``harness catalog`` both render from the
*registered* schemas instead of a hand-kept list, so the two can never disagree. A setting is
therefore only advertised when a schema declares it, and the loader rejects a schema file that does
not parse — see ``PluginRegistry._register_settings_schema``.
"""

from __future__ import annotations

from collections.abc import Iterable
from typing import Any

# The built-in tabs, in the order the panel shows them. A plugin does not have to appear here: a group only
# known to a plugin's schema is placed after these (see `order_groups`). This is a default, not a gate.
SETTINGS_GROUPS: tuple[str, ...] = (
    "Models & Providers",
    "Security & Sandboxing",
    "Execution & Strategies",
    "Economics & Spend",
    "Plugins & Integrations",
    "Secrets",
)

_WIDGET_BY_TYPE = {
    "boolean": "boolean",
    "integer": "number",
    "number": "number",
    "array": "json",
    "object": "json",
}


def _group_rank(label: str, order: Any) -> tuple[int, int, str]:
    """Sort key for a settings group: an explicit `x-ui.order` wins, then the built-in sequence, then last.

    The two-element shape is what lets a plugin place its tab *before* the kernel's: `(0, -1, …)` beats
    `(1, 0, …)`. Unknown groups share one rank and are separated by label, so the order never depends on the
    order schemas happen to be registered in.
    """
    if isinstance(order, bool) or not isinstance(order, int):
        known = SETTINGS_GROUPS.index(label) if label in SETTINGS_GROUPS else len(SETTINGS_GROUPS)
        return (1, known, label)
    return (0, order, label)


def order_groups(present: Iterable[str], props: dict[str, Any]) -> list[str]:
    """The settings tabs a client should render, in the order the *schemas* ask for.

    Two rules, both about not lying: a group is shown only because some property declares it (so a tab with
    no control in it never appears), and a group the built-in list has never heard of still gets its place —
    after the built-ins, alphabetically. Dropping unknown groups was what made "disable a plugin, its tab
    disappears" impossible without editing kernel code: a plugin could declare `x-ui.group: Obsidian` and
    render nothing.
    """
    best: dict[str, tuple[int, int, str]] = {}
    wanted = {str(g) for g in present if g}
    for prop in props.values():
        ui = (prop or {}).get("x-ui") or {}
        label = ui.get("group")
        if not label or label not in wanted:
            continue
        rank = _group_rank(str(label), ui.get("order"))
        if label not in best or rank < best[str(label)]:
            best[str(label)] = rank
    return sorted(wanted, key=lambda g: (best.get(g, _group_rank(g, None)), g))


def flatten_schema_properties(props: dict[str, Any], prefix: str = "") -> dict[str, dict[str, Any]]:
    """Flattens nested object schemas into dot-notation leaf keys.

    An object is descended only when it is a *closed* shape (``additionalProperties: false``):
    an open mapping (e.g. ``kernel.plugins.capabilities``) is a leaf whose children are user
    keys, not schema keys.
    """
    out: dict[str, dict[str, Any]] = {}
    for key, prop in props.items():
        full = f"{prefix}{key}"
        if not isinstance(prop, dict):
            continue
        nested = prop.get("properties")
        if prop.get("type") == "object" and nested and prop.get("additionalProperties") is False:
            out.update(flatten_schema_properties(nested, prefix=f"{full}."))
            continue
        out[full] = prop
    return out


def with_ui_defaults(prop: dict[str, Any], namespace: str) -> dict[str, Any]:
    """Returns a copy of ``prop`` with ``x-ui`` filled in from its type and namespace."""
    out = dict(prop)
    ui = dict(out.get("x-ui") or {})
    type_ = out.get("type")
    if isinstance(type_, list):
        type_ = next((t for t in type_ if t != "null"), "string")
    ui.setdefault("widget", "secret" if namespace == "secrets" else _WIDGET_BY_TYPE.get(type_ or "", "text"))
    if namespace == "secrets":
        ui["group"] = "Secrets"
    else:
        ui.setdefault("group", "Plugins & Integrations" if namespace != "kernel" else "Execution & Strategies")
    ui.setdefault("source", namespace)
    out["x-ui"] = ui
    if "title" not in out:
        out["title"] = out.get("description") or ui["group"]
    return out
