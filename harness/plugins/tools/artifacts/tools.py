"""
Artifacts tools: create_artifact (§4, PLG-16).
Provides secure artifact creation with metadata manifest and Card emission.
"""
from __future__ import annotations

import json
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from harness.sdk.cards import Card
from harness.sdk.context import ToolContext
from harness.sdk.types import RiskClass, TextBlock, ToolResult, ToolSpec

CREATE_ARTIFACT_SPEC = ToolSpec(
    name="create_artifact",
    description="Creates a rich document or generative UI artifact with user-facing metadata.",
    input_schema={
        "type": "object",
        "properties": {
            "name": {
                "type": "string",
                "description": "Artifact file name (e.g., 'report.md', 'diagram.html')",
            },
            "content": {
                "type": "string",
                "description": "Text or HTML content of the artifact",
            },
            "media_type": {
                "type": "string",
                "default": "text/markdown",
                "description": "MIME type (e.g. 'text/markdown', 'text/html')",
            },
            "user_facing": {
                "type": "boolean",
                "default": True,
                "description": "Whether to surface this artifact prominently to the user",
            },
            "request_feedback": {
                "type": "boolean",
                "default": False,
                "description": "Whether to request user review/feedback on this artifact",
            },
            "summary": {
                "type": "string",
                "default": "",
                "description": "Brief summary of the artifact content",
            },
        },
        "required": ["name", "content"],
        "additionalProperties": False,
    },
    risk_class=RiskClass.WRITE,
    capabilities=frozenset(["fs:write"]),
)


def _safe_artifact_path(name: str, artifacts_dir: Path) -> Path:
    if not name or name in {".", ".."} or Path(name).name != name:
        raise ValueError(f"Invalid artifact filename: {name!r}")
    if "/" in name or "\\" in name:
        raise ValueError(f"Path traversal detected in artifact filename: {name!r}")
    target = (artifacts_dir / name).resolve()
    if not target.is_relative_to(artifacts_dir.resolve()):
        raise ValueError(f"Path traversal detected in artifact filename: {name!r}")
    return target


async def handle_create_artifact(args: dict[str, Any], ctx: ToolContext) -> ToolResult:
    """Creates an artifact, persists it to disk and manifest, and emits an artifact Card."""
    name = str(args.get("name", "")).strip()
    content = str(args.get("content", ""))
    media_type = str(args.get("media_type", "text/markdown"))
    user_facing = bool(args.get("user_facing", True))
    request_feedback = bool(args.get("request_feedback", False))
    summary = str(args.get("summary", "")).strip()

    artifacts_dir = ctx.data_dir / "artifacts"
    artifacts_dir.mkdir(parents=True, exist_ok=True)

    try:
        target = _safe_artifact_path(name, artifacts_dir)
    except ValueError as err:
        return ToolResult.error(str(err))

    raw_bytes = content.encode("utf-8")
    target.write_bytes(raw_bytes)

    # Update manifest
    manifest_file = ctx.data_dir / "artifacts_manifest.json"
    manifest: dict[str, Any] = {}
    if manifest_file.exists():
        try:
            loaded = json.loads(manifest_file.read_text(encoding="utf-8"))
            if isinstance(loaded, dict):
                manifest = loaded
        except Exception:
            manifest = {}

    entry = {
        "name": name,
        "filename": name,
        "path": str(target),
        "media_type": media_type,
        "summary": summary,
        "user_facing": user_facing,
        "request_feedback": request_feedback,
        "bytes": len(raw_bytes),
        "created_at": datetime.now(UTC).isoformat(),
    }
    manifest[name] = entry
    manifest_file.write_text(
        json.dumps(manifest, indent=2, ensure_ascii=False),
        encoding="utf-8",
    )

    card = Card.artifact(
        name=name,
        media_type=media_type,
        url=f"/artifacts/{name}",
        request_feedback=request_feedback,
        title=summary or name,
    )

    return ToolResult(
        content=[TextBlock(text=f"Artifact '{name}' created successfully ({len(raw_bytes)} bytes).")],
        cards=[card],
        metadata={"artifact": entry},
    )
