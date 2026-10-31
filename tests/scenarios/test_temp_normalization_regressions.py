"""Focused regressions for active temporary-root path normalization."""
from __future__ import annotations

import tempfile
from pathlib import Path

import pytest
from _lib.normalize import Normalizer


def _normalizer_with_temp_alias(
    workspace: Path, monkeypatch, *, alias_root: str, canonical_root: str, extra_paths: dict[str, str] | None = None,
) -> Normalizer:
    real_resolve = Path.resolve
    monkeypatch.setattr(tempfile, "gettempdir", lambda: alias_root)

    def resolve_temp_alias(path: Path, *args, **kwargs) -> Path:
        if str(path) == alias_root:
            return Path(canonical_root)
        return real_resolve(path, *args, **kwargs)

    monkeypatch.setattr(Path, "resolve", resolve_temp_alias)
    return Normalizer(workspace=workspace, extra_paths=extra_paths)


@pytest.mark.parametrize("active_spelling", ["canonical", "alias"])
def test_active_nested_temp_root_replaces_before_generic_tmp_prefix(tmp_path: Path, monkeypatch, active_spelling: str):
    alias_root = "/var/folders/fixture/tmp-component/T/opencode/tmp"
    canonical_root = f"/private{alias_root}"
    normalizer = _normalizer_with_temp_alias(
        tmp_path / "workspace", monkeypatch, alias_root=alias_root, canonical_root=canonical_root,
    )
    active_root = canonical_root if active_spelling == "canonical" else alias_root

    assert normalizer.text(f"outside: {active_root}/etc/hostname") == "outside: <tmp>/etc/hostname"


def test_active_temp_root_preserves_suffix_and_detailed_text(tmp_path: Path, monkeypatch):
    alias_root = "/var/folders/fixture/T/opencode/tmp"
    canonical_root = f"/private{alias_root}"
    normalizer = _normalizer_with_temp_alias(
        tmp_path / "workspace", monkeypatch, alias_root=alias_root, canonical_root=canonical_root,
    )

    detail = f"denied {canonical_root}/nested/file.txt (reason: marker=tmp-not-root)"
    assert normalizer.text(detail) == "denied <tmp>/nested/file.txt (reason: marker=tmp-not-root)"


def test_unrelated_tmp_like_prefix_filename_and_detail_are_unchanged(tmp_path: Path, monkeypatch):
    alias_root = "/var/folders/fixture/T/opencode/tmp"
    canonical_root = f"/private{alias_root}"
    normalizer = _normalizer_with_temp_alias(
        tmp_path / "workspace", monkeypatch, alias_root=alias_root, canonical_root=canonical_root,
    )
    unrelated = "/other/var/folders/fixture/T/opencode/tmp-not-root/report.txt; detail=tmp stays literal"

    assert normalizer.text(unrelated) == unrelated


def test_workspace_parent_and_extra_path_keep_precedence_over_active_temp_root(tmp_path: Path, monkeypatch):
    alias_root = "/var/folders/fixture/T/opencode/tmp"
    canonical_root = f"/private{alias_root}"
    workspace = Path(canonical_root) / "case" / "workspace"
    extra_path = f"{canonical_root}/external-fixture"
    normalizer = _normalizer_with_temp_alias(
        workspace, monkeypatch, alias_root=alias_root, canonical_root=canonical_root,
        extra_paths={extra_path: "<fixture>"},
    )

    assert normalizer.text(f"{workspace}/inside.txt") == "<ws>/inside.txt"
    assert normalizer.text(f"{workspace.parent}/outside.txt") == "<ws-parent>/outside.txt"
    assert normalizer.text(f"{extra_path}/note.txt") == "<fixture>/note.txt"
