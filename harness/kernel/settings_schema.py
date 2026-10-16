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


#: The tab a plugin's settings land on when its own schema does not name one. A family nobody has heard of (a
#: workspace plugin, a new directory under `harness/plugins/`) is tolerated here rather than dropped — but it is
#: the exception, and `tests/kernel/test_settings_groups.py` keeps it from becoming a dumping ground again.
PLUGIN_TAB_DEFAULT = "Plugins & Integrations"

#: Capability family → the tab its settings belong on. Families are the directories under
#: `harness/plugins/`, so this is *not* a list of plugins: adding a plugin needs no entry, and a plugin that wants
#: a tab of its own says `x-ui.group` in its own schema instead. Providers and strategies deliberately reuse the
#: kernel's existing tabs rather than inventing near-duplicates.
FAMILY_TABS: dict[str, str] = {
    "providers": "Models & Providers",
    "policy": "Policy & Safety",
    "hooks": "Hooks",
    "context": "Context",
    "tools": "Tools",
    "bridges": "Bridges & Integrations",
    "stores": "Storage & Records",
    "executors": "Execution & Strategies",
    "strategies": "Execution & Strategies",
}

#: Words whose correct capitalisation is not "Title Case", because a heading that reads `Mcp Client` or
#: `Openai Compat` makes a configured product look unfinished. Anything absent here is simply title-cased.
_ACRONYM_DISPLAY = {
    "mcp": "MCP",
    "acp": "ACP",
    "fs": "FS",
    "api": "API",
    "llm": "LLM",
    "ui": "UI",
    "sse": "SSE",
    "jsonl": "JSONL",
    "json": "JSON",
    "sql": "SQL",
    "openai": "OpenAI",
    "anthropic": "Anthropic",
    "http": "HTTP",
    "url": "URL",
    "fts5": "FTS5",
}


def _family_affixes(family: str | None) -> set[str]:
    """The words a plugin id uses for its family: `strategies`, `strategy`, `executor`, `executors`…

    Ids in this repository are inconsistent about the affix they carry and whether it is a prefix
    (`hook-read-before-edit`) or a suffix (`fs-tools`, `local-executor`), and a heading repeating the tab's own
    word is noise — so both forms and both numbers are stripped when either is present.
    """
    if not family:
        return set()
    out = {family}
    if family.endswith("ies"):
        out.add(family[:-3] + "y")
    if family.endswith("s"):
        out.add(family[:-1])
    return out


def plugin_owner(namespace: str) -> str:
    """`plugins.hook-quiet` → `hook-quiet`: the id the client can point a settings jump at."""
    return namespace[len("plugins.") :] if namespace.startswith("plugins.") else namespace


def plugin_display_name(name: str, family: str | None = None) -> str:
    """`hook-read-before-edit` → `Read Before Edit` — a heading, not a package name.

    The family affix is dropped because it is already the tab the row sits under (`Hooks`), and the id's separator
    would otherwise be shouted at the user. Names that carry the family as a suffix (`fs-tools`, `local-executor`)
    are handled the same way; a name with nothing to strip is just prettified.
    """
    base = plugin_owner(name)
    for stem in _family_affixes(family):
        for cand in (f"{stem}-", f"{stem}_", f"-{stem}", f"_{stem}"):
            if len(base) > len(cand) and (base.startswith(cand) or base.endswith(cand)):
                base = base[: -len(cand)] if base.endswith(cand) else base[len(cand) :]
                break
    words = [w for w in base.replace("_", "-").split("-") if w]
    out = " ".join(_ACRONYM_DISPLAY.get(w.lower(), w[:1].upper() + w[1:]) for w in words)
    return out or plugin_owner(name).replace("-", " ").title()


def tab_for(namespace: str, family: str | None = None) -> str:
    """The settings tab a plugin's properties default to: its family's tab, else the catch-all."""
    return FAMILY_TABS.get(family or "", PLUGIN_TAB_DEFAULT)


def settings_owner_index(registry: object) -> dict[str, dict[str, object]]:
    """`{schema-namespace: {"family": …, "owner_summary": …}}` for the plugins that registered a schema.

    Both `Commands.settings_schema` (what the client renders) and `harness catalog` (what the generated document
    claims) pass this into `with_ui_defaults`, so the two cannot disagree about which tab a plugin's settings are
    on — the whole reason the catalogue is worth reading.
    """
    from harness.kernel.plugin_paths import plugin_dir

    index: dict[str, dict[str, object]] = {}
    for name, discovered in (getattr(registry, "discovered", {}) or {}).items():
        namespace = f"plugins.{name}"
        path = plugin_dir(discovered)
        index[namespace] = {
            "family": path.parent.name,
            "owner_summary": (getattr(discovered.manifest, "summary", "") or "").strip() or None,
        }
    return index


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


def with_ui_defaults(
    prop: dict[str, Any],
    namespace: str,
    *,
    family: str | None = None,
    owner_summary: str | None = None,
) -> dict[str, Any]:
    """Returns a copy of ``prop`` with ``x-ui`` filled in from its type and namespace.

    A plugin's property also gets its **owner** (`x-ui.owner`, `owner_title`, `owner_summary`): the client renders
    those as the section heading a settings tab is organised by, so the tab groups like *behaviour* (family) while
    the heading says which integration a control belongs to. Kernel and secret properties own nothing and are
    rendered without a heading. An explicit ``x-ui.group`` on the property always wins over the family tab —
    a plugin is allowed to say where it belongs.
    """
    out = dict(prop)
    ui = dict(out.get("x-ui") or {})
    type_ = out.get("type")
    if isinstance(type_, list):
        type_ = next((t for t in type_ if t != "null"), "string")
    ui.setdefault("widget", "secret" if namespace == "secrets" else _WIDGET_BY_TYPE.get(type_ or "", "text"))
    if namespace == "secrets":
        ui["group"] = "Secrets"
    elif namespace == "kernel":
        ui.setdefault("group", "Execution & Strategies")
    else:
        ui.setdefault("group", tab_for(namespace, family))
        ui["owner"] = plugin_owner(namespace)
        ui["owner_title"] = plugin_display_name(namespace, family)
        if owner_summary:
            ui["owner_summary"] = owner_summary
    ui.setdefault("source", namespace)
    out["x-ui"] = ui
    if "title" not in out:
        out["title"] = out.get("description") or ui["group"]
    return out
