"""Read-only development snapshot identity; never a product safety/approval gate."""
from __future__ import annotations

import argparse
import hashlib
import json
import re
import stat
from pathlib import Path, PurePosixPath
from typing import Any

DIGEST_FORMAT = "harness-zero.snapshot.v1"


def canonical_snapshot_bytes(entries: list[dict[str, str]]) -> bytes:
    """Canonical v1: exact metadata keys, path order, compact UTF-8 JSON, no newline."""
    if not isinstance(entries, list):
        raise ValueError("files must be a list")
    records = []
    seen = set()
    for item in entries:
        if not isinstance(item, dict) or set(item) != {"path", "mode", "sha256"}:
            raise ValueError("Each file must contain exactly path, mode and sha256")
        if not all(isinstance(value, str) for value in item.values()):
            raise ValueError("File identity values must be strings")
        path, mode, digest = item["path"], item["mode"], item["sha256"]
        parsed = PurePosixPath(path)
        if not path or path == "." or "\0" in path or "\\" in path or ".." in parsed.parts or parsed.as_posix() != path:
            raise ValueError("File paths must be normalized POSIX paths without dot segments")
        if path in seen:
            raise ValueError(f"duplicate file path: {path}")
        seen.add(path)
        if not re.fullmatch(r"0o[0-7]+", mode) or not 0 <= int(mode[2:], 8) <= 0o7777 or oct(int(mode[2:], 8)) != mode:
            raise ValueError("Mode must be canonical oct(stat.S_IMODE(...))")
        if not re.fullmatch(r"[0-9a-f]{64}", digest):
            raise ValueError("sha256 must be 64 lowercase hexadecimal characters")
        records.append({"path": path, "mode": mode, "sha256": digest})
    return json.dumps(sorted(records, key=lambda item: item["path"]), ensure_ascii=False,
                      sort_keys=True, separators=(",", ":")).encode("utf-8")


def snapshot_digest(entries: list[dict[str, str]]) -> str:
    return hashlib.sha256(canonical_snapshot_bytes(entries)).hexdigest()


def verify_manifest(manifest: dict[str, Any], root: Path, expected_digest: str | None = None) -> dict[str, Any]:
    if not isinstance(manifest, dict):
        raise ValueError("Manifest must be an object")
    if manifest.get("digest_format") != DIGEST_FORMAT:
        raise ValueError("Unknown or missing digest_format")
    entries = manifest["files"]
    declared = snapshot_digest(entries)
    expected = manifest.get("candidate_sha256")
    if not isinstance(expected, str) or not re.fullmatch(r"[0-9a-f]{64}", expected):
        raise ValueError("candidate_sha256 must be 64 lowercase hexadecimal characters")
    errors = []
    if expected_digest is not None:
        if not re.fullmatch(r"[0-9a-f]{64}", expected_digest):
            raise ValueError("Expected review digest must be 64 lowercase hexadecimal characters")
        if expected != expected_digest:
            errors.append("Manifest candidate differs from the expected review receipt digest")
    if declared != expected:
        errors.append("Aggregate metadata digest does not match candidate_sha256")
    root = root.resolve(strict=True)
    for entry in entries:
        path = Path(entry["path"])
        if not path.is_absolute():
            path = root / path
        try:
            # Inspect from the filesystem root down, before opening a file
            # through a parent alias. This is not a concurrent-writer sandbox.
            if any(component.is_symlink() for component in [*reversed(path.parents), path]):
                raise ValueError("Snapshot paths may not contain symlinked components")
            if not stat.S_ISREG(path.stat().st_mode):
                raise ValueError("Snapshot verifier supports regular files, not symlinks/directories")
            mode = oct(stat.S_IMODE(path.stat().st_mode))
            digest = hashlib.sha256(path.read_bytes()).hexdigest()
            if mode != entry["mode"]:
                errors.append(f"{entry['path']}: mode changed")
            if digest != entry["sha256"]:
                errors.append(f"{entry['path']}: content changed")
        except (OSError, ValueError) as error:
            errors.append(f"{entry['path']}: {type(error).__name__}")
    return {"valid": not errors, "digest_format": DIGEST_FORMAT,
            "review_receipt_checked": expected_digest is not None,
            "candidate_sha256": expected, "metadata_sha256": declared, "errors": errors}


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("manifest", type=Path)
    parser.add_argument("--root", type=Path, default=Path(__file__).resolve().parents[1])
    parser.add_argument("--expected-digest", required=True, help="Digest from the independent review receipt")
    args = parser.parse_args()
    try:
        manifest = json.loads(args.manifest.read_text(encoding="utf-8"))
        result = verify_manifest(manifest, args.root, args.expected_digest)
    except (OSError, ValueError, KeyError, TypeError) as error:
        result = {"valid": False, "errors": [str(error)]}
    print(json.dumps(result, indent=2))
    return 0 if result["valid"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
