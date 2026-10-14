"""
Filesystem Artifact Store implementation (§PLG-16).
Path traversal validated, manifest persistence, sandboxed artifact storage.
"""
from __future__ import annotations

import json
from datetime import UTC, datetime
from pathlib import Path
from typing import Any


def validate_artifact_name(filename: str, artifacts_dir: Path) -> Path:
    """Validates filename is a single safe component inside artifacts_dir."""
    if not filename or filename in {".", ".."} or Path(filename).name != filename:
        raise ValueError(f"Invalid artifact filename: {filename!r}")
    if "/" in filename or "\\" in filename:
        raise ValueError(f"Path traversal detected in artifact filename: {filename!r}")
    target = (artifacts_dir / filename).resolve()
    if not target.is_relative_to(artifacts_dir.resolve()):
        raise ValueError(f"Path traversal detected in artifact filename: {filename!r}")
    return target


class FSArtifactStore:
    """Filesystem-backed artifact store tracking manifest metadata."""

    def __init__(self, data_dir: Path) -> None:
        self.data_dir = Path(data_dir)
        self.artifacts_dir = self.data_dir / "artifacts"
        self.artifacts_dir.mkdir(parents=True, exist_ok=True)
        self.manifest_file = self.data_dir / "artifacts_manifest.json"

    def _load_manifest(self) -> dict[str, dict[str, Any]]:
        if not self.manifest_file.exists():
            return {}
        try:
            data = json.loads(self.manifest_file.read_text(encoding="utf-8"))
            if isinstance(data, dict):
                return data
            return {}
        except Exception:
            return {}

    def _save_manifest(self, manifest: dict[str, dict[str, Any]]) -> None:
        self.manifest_file.write_text(
            json.dumps(manifest, indent=2, ensure_ascii=False),
            encoding="utf-8",
        )

    async def write(
        self,
        name: str,
        content: bytes | str,
        *,
        media_type: str = "text/plain",
        meta: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        """Writes an artifact to disk and updates the manifest."""
        target = validate_artifact_name(name, self.artifacts_dir)
        raw_bytes = content.encode("utf-8") if isinstance(content, str) else content
        target.write_bytes(raw_bytes)

        manifest = self._load_manifest()
        entry: dict[str, Any] = {
            "name": name,
            "filename": name,
            "path": str(target),
            "media_type": media_type,
            "summary": (meta or {}).get("summary", ""),
            "user_facing": (meta or {}).get("user_facing", True),
            "request_feedback": (meta or {}).get("request_feedback", False),
            "bytes": len(raw_bytes),
            "created_at": (meta or {}).get("created_at") or datetime.now(UTC).isoformat(),
        }
        if meta:
            for k, v in meta.items():
                if k not in entry:
                    entry[k] = v

        manifest[name] = entry
        self._save_manifest(manifest)
        return entry

    async def read(self, name: str) -> tuple[bytes, dict[str, Any]]:
        """Reads an artifact's binary content and metadata."""
        target = validate_artifact_name(name, self.artifacts_dir)
        if not target.exists() or not target.is_file():
            raise FileNotFoundError(f"Artifact not found: {name}")
        raw = target.read_bytes()
        manifest = self._load_manifest()
        meta = manifest.get(name)
        if meta is None:
            meta = {
                "name": name,
                "filename": name,
                "path": str(target),
                "media_type": "application/octet-stream",
                "summary": "",
                "user_facing": True,
                "request_feedback": False,
                "bytes": len(raw),
                "created_at": datetime.now(UTC).isoformat(),
            }
        return raw, meta

    async def list(self) -> list[dict[str, Any]]:
        """Lists all registered artifacts from the manifest."""
        manifest = self._load_manifest()
        return list(manifest.values())
