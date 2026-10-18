"""WS-02: the catalogues describe the kernel that is actually running — and stay that way.

Every review of this repository reported the same class of defect: a reference document, a UI list or a
test fixture kept its own copy of a fact the kernel owns, then drifted (the settings panel's hard-coded
schema, `docs/PLUGIN_CATALOG.md`'s prose list, `/health`'s literal version). These tests make the copy
impossible: the documents are rendered from a booted kernel, and this suite fails if the committed files
no longer match what a boot produces.
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

import pytest

from harness.kernel.catalog import (
    CATALOG_DIR,
    REPO_ROOT,
    _check_drift,
    build_catalog,
    render_configuration_table,
)
from harness.kernel.commands import BUILTIN_COMMANDS
from harness.sdk.cards import CARD_FIELDS
from harness.sdk.events import EVENT_PAYLOADS
from harness.sdk.manifest import PluginManifest

_cache: dict[str, dict] = {}


async def _catalog() -> dict:
    """One boot for the whole module (the catalog is plain data, safe to share)."""
    if "cat" not in _cache:
        _cache["cat"] = await build_catalog()
    return _cache["cat"]


def _manifests_on_disk() -> dict[str, Path]:
    out: dict[str, Path] = {}
    for toml in (REPO_ROOT / "harness" / "plugins").glob("*/*/harness_plugin.toml"):
        manifest = PluginManifest.load(toml)
        out[manifest.name] = toml.parent
    return out


@pytest.mark.asyncio
async def test_plugin_rows_are_the_plugin_tree():
    cat = await _catalog()
    rows = {p["name"] for p in cat["plugins"]}
    on_disk = set(_manifests_on_disk())
    assert rows == on_disk, f"catalog vs tree mismatch: only-catalog={rows - on_disk} only-tree={on_disk - rows}"
    assert len(rows) >= 30, "the plugin count collapsed — a boot that loads nothing proves nothing"
    for row in cat["plugins"]:
        assert row["loaded"] is True, f"{row['name']} did not load — its setup() raised"
        assert row["path"] == str(_manifests_on_disk()[row["name"]].relative_to(REPO_ROOT))


@pytest.mark.asyncio
async def test_command_rows_are_the_dispatcher_catalogue():
    cat = await _catalog()
    names = [row["name"] for row in cat["commands"]]
    assert names[: len(BUILTIN_COMMANDS)] == list(BUILTIN_COMMANDS)
    plugin_rows = {
        row["name"]: row
        for row in cat["commands"]
        if row["plugin"] == "hook-checkpoints"
    }
    assert {
        "plugins.hook-checkpoints.changes",
        "plugins.hook-checkpoints.diff",
        "plugins.hook-checkpoints.restore",
    } <= set(plugin_rows)
    assert plugin_rows["plugins.hook-checkpoints.changes"]["args"][0]["name"] == "session_id"
    assert all(row["exposed"] and row["handler"].startswith("harness/plugins/") for row in plugin_rows.values())
    for row in cat["commands"]:
        if row["plugin"]:
            assert row["name"].startswith(f"plugins.{row['plugin']}.")
            assert row["handler"].startswith("harness/plugins/"), row
        else:
            assert row["handler"].endswith(row["name"].replace(".", "_").replace("-", "_")), row
        if not row["alias"]:
            assert row["summary"], f"{row['name']} has no docstring — the command table would be blank"


@pytest.mark.asyncio
async def test_event_and_card_rows_are_the_sdk_catalogues():
    cat = await _catalog()
    assert {e["type"] for e in cat["events"]} == set(EVENT_PAYLOADS)
    assert set(cat["cards"]) == set(CARD_FIELDS)
    for row in cat["events"]:
        assert row["fields"] == sorted(EVENT_PAYLOADS[row["type"]].__dataclass_fields__), row["type"]


@pytest.mark.asyncio
async def test_tool_rows_name_the_owning_plugin_and_the_gate_sees_the_risk():
    cat = await _catalog()
    names = {t["name"] for t in cat["tools"]}
    assert {"read_resource", "remember", "search_memory", "run_command", "edit_file"} <= names
    for tool in cat["tools"]:
        assert tool["plugin"], f"{tool['name']} has no owning plugin in the registry's tool table"
        assert tool["risk_class"] in {"read", "write", "network", "exec", "destructive"}, tool


@pytest.mark.asyncio
async def test_settings_rows_are_the_aggregated_surface():
    cat = await _catalog()
    keys = {s["key"] for s in cat["settings"] if not s["alias"]}
    assert {"kernel.provider", "kernel.strategy", "secrets.OPENAI_API_KEY"} <= keys
    # the panel must not list a compatibility spelling next to the key it aliases
    assert "plugins.mcp-client.servers" in {s["key"] for s in cat["settings"] if s["alias"]}
    for row in cat["settings"]:
        assert row["owner"], f"{row['key']} has no owner"
        if row["alias"]:
            continue
        # A plugin's own settings are panel controls by definition; kernel keys opt in via `x-ui`
        # and the rest are documented as internals (nothing declared is simply dropped).
        if not row["key"].startswith("kernel."):
            assert row["in_ui"] is True, f"{row['key']} is declared but not rendered"
    settings_doc = (REPO_ROOT / CATALOG_DIR / "SETTINGS.md").read_text(encoding="utf-8")
    missing = [row["key"] for row in cat["settings"] if not row["alias"] and f"`{row['key']}`" not in settings_doc]
    assert missing == [], f"declared settings missing from docs/catalog/SETTINGS.md: {missing}"


@pytest.mark.asyncio
async def test_web_client_calls_only_registered_commands():
    cat = await _catalog()
    assert cat["ui"]["unknown_client_commands"] == []
    assert "submit_turn" in cat["ui"]["client_commands"]


@pytest.mark.asyncio
async def test_plugin_owned_panels_and_renderers_are_in_the_static_ui_contract():
    cat = await _catalog()
    panel = next(row for row in cat["ui"]["panels"] if row["id"] == "diffs")
    assert panel["plugin"] == "hook-checkpoints"
    assert panel["command"] == "plugins.hook-checkpoints.changes"

    renderers = {(row["plugin"], row["kind"]) for row in cat["ui"]["plugin_record_renderers"]}
    assert {
        ("hook-checkpoints", "workspace.changes"),
        ("hook-checkpoints", "checkpoint.restored"),
        ("tools-deliverables", "deliverable.presented"),
    } <= renderers
    module_owners = {row["plugin"] for row in cat["ui"]["client_modules"]}
    assert {"hook-checkpoints", "tools-deliverables"} <= module_owners

    contract = (REPO_ROOT / CATALOG_DIR / "UI_CONTRACT.md").read_text(encoding="utf-8")
    assert "### Plugin-owned record renderers" in contract
    assert "`deliverable.presented`" in contract
    assert "`workspace.changes`" in contract


@pytest.mark.skipif(
    sys.platform == "darwin",
    reason="the committed catalogue is generated on Linux; macOS-only tools add host-specific rows",
)
@pytest.mark.asyncio
async def test_committed_documents_match_a_fresh_boot():
    """The Linux freshness gate itself, so `pytest -q` catches drift before CI does."""
    result = _check_drift(await _catalog(), REPO_ROOT)
    assert result["status"] == "pass", result["detail"] + "\n" + "\n".join(result["lines"])


@pytest.mark.asyncio
async def test_readme_configuration_table_is_generated_not_handwritten():
    cat = await _catalog()
    table = render_configuration_table(cat, "bridge-mcp-client")
    assert "plugins.bridge-mcp-client.servers" in table
    assert "This plugin reads no settings" not in table
    empty = render_configuration_table(cat, "context-identity")
    assert "reads no settings" in empty


def test_events_schema_document_is_valid_json_and_current():
    schema = json.loads((REPO_ROOT / "docs" / "events.schema.json").read_text(encoding="utf-8"))
    assert set(schema["properties"]["type"]["enum"]) >= set(EVENT_PAYLOADS)


def test_catalog_directory_is_what_the_docs_link():
    for name in ("PLUGINS.md", "TOOLS.md", "SETTINGS.md", "EVENTS.md", "COMMANDS.md", "UI_CONTRACT.md", "catalog.json"):
        assert (REPO_ROOT / CATALOG_DIR / name).is_file(), name
    assert not (REPO_ROOT / "docs" / "PLUGIN_CATALOG.md").exists(), (
        "the hand-written catalogue is replaced by docs/catalog/PLUGINS.md (WS-02)"
    )
    for doc in (REPO_ROOT / "README.md", REPO_ROOT / "docs" / "ARCHITECTURE.md"):
        text = doc.read_text(encoding="utf-8")
        assert "PLUGIN_CATALOG" not in text, f"{doc.relative_to(REPO_ROOT)} still links the deleted file"


@pytest.mark.asyncio
async def test_catalogue_titles_agree_with_the_package_version():
    """The version pinned in `pyproject.toml` is the version the generated docs were built for."""
    import tomllib

    from harness import __version__

    pyproject = tomllib.loads((REPO_ROOT / "pyproject.toml").read_text(encoding="utf-8"))
    assert pyproject["project"]["version"] == __version__
    cat = await _catalog()
    assert cat["version"] == __version__
    for name in ("PLUGINS.md", "TOOLS.md"):
        text = (REPO_ROOT / CATALOG_DIR / name).read_text(encoding="utf-8")
        assert f"v{__version__}" in text, f"{name} was generated for another version — run `harness catalog gen`"
