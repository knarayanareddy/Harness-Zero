"""Helpers that turn registered JSON Schemas into the settings surface (WS-02).

``Commands.settings_schema`` (the UI's source of truth) and ``harness catalog`` both render from the
*registered* schemas instead of a hand-kept list, so the two can never disagree. A setting is
therefore only advertised when a schema declares it, and the loader rejects a schema file that does
not parse — see ``PluginRegistry._register_settings_schema``.
"""

from __future__ import annotations

from typing import Any

# Tab order of the settings UI. A group with no visible property is dropped.
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
