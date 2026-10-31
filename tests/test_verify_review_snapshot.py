"""Development review identity: canonical bytes and read-only verification."""
from __future__ import annotations

import hashlib
import importlib.util
import json
import subprocess
import sys
from pathlib import Path

import pytest


def load_verifier():
    path = Path(__file__).parents[1] / "scripts/verify_review_snapshot.py"
    assert path.is_file(), "The read-only review snapshot verifier is not implemented"
    spec = importlib.util.spec_from_file_location("verify_review_snapshot", path)
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_snapshot_digest_uses_sorted_paths_and_exact_utf8_json_bytes():
    verifier = load_verifier()
    first = {"path": "a.txt", "mode": "0o644", "sha256": "a" * 64}
    last = {"path": "z.txt", "mode": "0o755", "sha256": "b" * 64}
    expected = (b'[{"mode":"0o644","path":"a.txt","sha256":"' + b"a" * 64
                + b'"},{"mode":"0o755","path":"z.txt","sha256":"' + b"b" * 64 + b'"}]')
    assert verifier.canonical_snapshot_bytes([last, first]) == expected
    assert verifier.snapshot_digest([last, first]) == hashlib.sha256(expected).hexdigest()
    assert verifier.snapshot_digest([first, last]) == verifier.snapshot_digest([last, first])
    unicode_entry = {**first, "path": "café.md"}
    assert "café.md".encode() in verifier.canonical_snapshot_bytes([unicode_entry])
    assert not verifier.canonical_snapshot_bytes([first]).endswith(b"\n")


@pytest.mark.parametrize("mutation", [None, "content", "mode", "missing"])
def test_verification_is_read_only_and_detects_file_changes(tmp_path, mutation):
    verifier = load_verifier()
    assert callable(getattr(verifier, "verify_manifest", None)), "Snapshot file verification is missing"
    target = tmp_path / "candidate.txt"
    target.write_bytes(b"reviewed content")
    target.chmod(0o644)
    entries = [{"path": "candidate.txt", "mode": "0o644", "sha256": hashlib.sha256(target.read_bytes()).hexdigest()}]
    manifest = {"digest_format": "harness-zero.snapshot.v1", "files": entries,
                "candidate_sha256": verifier.snapshot_digest(entries)}
    if mutation == "content":
        target.write_bytes(b"unreviewed content")
    elif mutation == "mode":
        target.chmod(0o755)
    elif mutation == "missing":
        target.unlink()
    before = (target.read_bytes(), target.stat().st_mode) if target.exists() else None
    report = verifier.verify_manifest(manifest, tmp_path)
    assert report["valid"] is (mutation is None)
    assert bool(report["errors"]) is (mutation is not None)
    after = (target.read_bytes(), target.stat().st_mode) if target.exists() else None
    assert after == before, "Verification must never repair or recreate candidate files"


@pytest.mark.parametrize("bad", [
    {"path": "../escape", "mode": "0o644", "sha256": "a" * 64},
    {"path": "./alias", "mode": "0o644", "sha256": "a" * 64},
    {"path": ".", "mode": "0o644", "sha256": "a" * 64},
    {"path": "a", "mode": "644", "sha256": "a" * 64},
    {"path": "a", "mode": "0o644", "sha256": "not a digest"},
    {"path": "a", "mode": "0o644", "sha256": "a" * 64, "ignored": "must not be ignored"},
])
def test_canonical_identity_rejects_ambiguous_metadata(bad):
    with pytest.raises(ValueError):
        load_verifier().canonical_snapshot_bytes([bad])


def test_duplicate_paths_are_not_silently_hashed_twice():
    entry = {"path": "same", "mode": "0o644", "sha256": "a" * 64}
    with pytest.raises(ValueError, match="duplicate"):
        load_verifier().canonical_snapshot_bytes([entry, entry])


def test_file_verification_rejects_a_symlink_without_changing_its_target(tmp_path):
    verifier = load_verifier()
    target = tmp_path / "target.txt"
    target.write_bytes(b"unchanged")
    target.chmod(0o644)
    link = tmp_path / "alias.txt"
    link.symlink_to(target)
    entries = [{"path": "alias.txt", "mode": "0o644", "sha256": hashlib.sha256(target.read_bytes()).hexdigest()}]
    manifest = {"digest_format": "harness-zero.snapshot.v1", "files": entries,
                "candidate_sha256": verifier.snapshot_digest(entries)}
    assert verifier.verify_manifest(manifest, tmp_path)["valid"] is False
    assert target.read_bytes() == b"unchanged" and link.is_symlink()


@pytest.mark.parametrize("absolute", [False, True])
def test_file_verification_rejects_symlinked_ancestors(tmp_path, absolute):
    verifier = load_verifier()
    root = tmp_path / "repo"
    root.mkdir()
    external = tmp_path / "external"
    external.mkdir()
    target = external / "candidate.txt"
    target.write_bytes(b"outside alias")
    target.chmod(0o644)
    (root / "linked").symlink_to(external, target_is_directory=True)
    name = str(root / "linked/candidate.txt") if absolute else "linked/candidate.txt"
    entries = [{"path": name, "mode": "0o644", "sha256": hashlib.sha256(target.read_bytes()).hexdigest()}]
    manifest = {"digest_format": "harness-zero.snapshot.v1", "files": entries,
                "candidate_sha256": verifier.snapshot_digest(entries)}
    assert verifier.verify_manifest(manifest, root)["valid"] is False
    assert target.read_bytes() == b"outside alias"


@pytest.mark.parametrize("case", ["valid", "file", "aggregate", "review", "rehashed", "format"])
def test_cli_binds_read_only_verification_to_the_reviewed_digest(tmp_path, case):
    verifier = load_verifier()
    target = tmp_path / "candidate.txt"
    target.write_bytes(b"reviewed")
    target.chmod(0o644)
    entry = {"path": "candidate.txt", "mode": "0o644", "sha256": hashlib.sha256(target.read_bytes()).hexdigest()}
    reviewed = verifier.snapshot_digest([entry])
    manifest = {"digest_format": "harness-zero.snapshot.v1", "files": [entry], "candidate_sha256": reviewed}
    expected = "f" * 64 if case == "review" else reviewed
    if case in {"file", "rehashed"}:
        target.write_bytes(b"unreviewed")
    if case == "rehashed":
        entry["sha256"] = hashlib.sha256(target.read_bytes()).hexdigest()
        manifest["candidate_sha256"] = verifier.snapshot_digest([entry])
    if case == "aggregate":
        manifest["candidate_sha256"] = "0" * 64
    if case == "format":
        manifest["digest_format"] = "unknown"
    path = tmp_path / "manifest.json"
    path.write_text(json.dumps(manifest))
    before = path.read_bytes(), target.read_bytes(), target.stat().st_mode
    script = Path(__file__).parents[1] / "scripts/verify_review_snapshot.py"
    result = subprocess.run([sys.executable, str(script), str(path), "--root", str(tmp_path),
                             "--expected-digest", expected], capture_output=True, text=True, timeout=5)
    assert result.stdout.strip(), "Verifier CLI must return a machine-readable receipt"
    receipt = json.loads(result.stdout)
    assert receipt["valid"] is (case == "valid")
    assert result.returncode == (0 if case == "valid" else 1)
    assert (path.read_bytes(), target.read_bytes(), target.stat().st_mode) == before
