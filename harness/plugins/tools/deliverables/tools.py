"""`present` tool: mark a safe, existing workspace file as a user-facing deliverable."""
from __future__ import annotations

import hashlib
import logging
from pathlib import Path, PurePosixPath
from typing import Any

from harness.sdk.cards import Card
from harness.sdk.context import ToolContext
from harness.sdk.events import DeliverablePresented
from harness.sdk.types import RiskClass, TextBlock, ToolResult, ToolSpec

logger = logging.getLogger(__name__)

ARTIFACTS_REL = PurePosixPath(".harness/artifacts")

PRESENT_SPEC = ToolSpec(
    name="present",
    description=(
        "Mark an existing workspace file or artifact as a deliverable for the user and open it in the viewer. "
        "Use this when you finish producing the document, image, or result the user asked for."
    ),
    input_schema={
        "type": "object",
        "properties": {
            "path": {
                "type": "string",
                "minLength": 1,
                "description": "Existing file inside the workspace; artifact filenames may be given directly.",
            },
            "caption": {
                "type": "string",
                "maxLength": 200,
                "default": "",
                "description": "One-line description of why this file is the deliverable.",
            },
        },
        "required": ["path"],
        "additionalProperties": False,
    },
    risk_class=RiskClass.READ,
    capabilities=frozenset(["fs:read"]),
)


def _resolve_present_path(raw: str, workspace: Path) -> tuple[Path, str, str]:
    """Resolve an existing workspace file, refusing traversal, symlink escape, and Harness internals."""
    value = str(raw or "").strip()
    if not value or "\0" in value or "\\" in value:
        raise ValueError("Path must be a non-empty workspace-relative path (backslashes are not accepted).")

    root = Path(workspace).expanduser().resolve(strict=True)
    supplied = Path(value)
    if supplied.is_absolute():
        candidate_input = supplied
    else:
        parts = PurePosixPath(value).parts
        if not parts or any(part in {"", ".", ".."} for part in parts):
            raise ValueError("Path must not contain empty, '.' or '..' segments.")
        candidate_input = root.joinpath(*parts)

    harness_dir = root / ".harness"
    artifacts_root = root / Path(*ARTIFACTS_REL.parts)
    if supplied.is_absolute():
        try:
            relative_input = supplied.relative_to(root)
            supplied_parts = PurePosixPath(relative_input.as_posix()).parts
        except ValueError:
            supplied_parts = ()
    else:
        supplied_parts = PurePosixPath(value).parts
    is_artifact_fallback = False
    try:
        target = candidate_input.resolve(strict=True)
    except (FileNotFoundError, NotADirectoryError):
        # `create_artifact` returns a short filename. Let `present("report.md")` select that artifact if there
        # is no same-named workspace-root file, without broadening arbitrary relative path resolution.
        if len(supplied_parts) != 1:
            raise ValueError(f"File does not exist: {value}") from None
        if harness_dir.is_symlink() or artifacts_root.is_symlink():
            raise ValueError("Artifact paths may not pass through symlinked Harness directories.") from None
        candidate_input = artifacts_root.joinpath(*supplied_parts)
        is_artifact_fallback = True
        try:
            target = candidate_input.resolve(strict=True)
        except (FileNotFoundError, NotADirectoryError):
            raise ValueError(f"File does not exist: {value}") from None

    if not target.is_relative_to(root):
        raise ValueError("Path resolves outside the workspace.")
    if not target.is_file():
        raise ValueError("Path must name a regular file.")

    # `.harness` is private; only the canonical artifact directory is presentable. Reject aliases through
    # symlinked parents rather than treating another internal directory as an artifact merely because the
    # `<workspace>/.harness/artifacts` spelling resolves there.
    artifact_spelling = supplied_parts[:2] == (".harness", "artifacts")
    if (is_artifact_fallback or artifact_spelling) and (harness_dir.is_symlink() or artifacts_root.is_symlink()):
        raise ValueError("Artifact paths may not pass through symlinked Harness directories.")
    is_artifact = (
        not harness_dir.is_symlink()
        and not artifacts_root.is_symlink()
        and target.is_relative_to(artifacts_root)
    )

    relative = target.relative_to(root)
    private_roots = [harness_dir.resolve(strict=False), (root / ".git").resolve(strict=False)]
    if any(target.is_relative_to(private_root) for private_root in private_roots):
        if not is_artifact:
            raise ValueError("Harness or Git internal state cannot be presented as a deliverable.")
    if is_artifact and len(target.relative_to(artifacts_root).parts) != 1:
        raise ValueError("Artifacts must be files directly inside .harness/artifacts/.")

    return target, relative.as_posix(), "artifact" if is_artifact else "file"


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as file:
        for chunk in iter(lambda: file.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


async def handle_present(args: dict[str, Any], ctx: ToolContext) -> ToolResult:
    """Validate, fingerprint, and durably record one existing file; never upload or mutate it."""
    try:
        target, relative, kind = _resolve_present_path(str(args.get("path", "")), ctx.workspace)
    except (OSError, ValueError) as err:
        return ToolResult.error(str(err))

    caption = " ".join(str(args.get("caption", "")).split())
    if len(caption) > 200:
        return ToolResult.error("Caption must be no longer than 200 characters.")
    try:
        size = target.stat().st_size
        sha256 = _sha256(target)
    except OSError as err:
        return ToolResult.error(f"Could not read deliverable: {err}")

    name = PurePosixPath(relative).name
    caption_text = caption or name
    turn_id = str(ctx.turn_id or "unknown")
    payload = DeliverablePresented(
        turn_id=turn_id,
        path=relative,
        caption=caption_text,
        kind=kind,
        size=size,
        sha256=sha256,
    )
    content = f"Presented {name} — {caption_text}"
    metadata = {
        "turn_id": turn_id,
        "path": relative,
        "caption": caption_text,
        "kind": kind,
        "size": size,
        "sha256": sha256,
    }
    try:
        await ctx.emit_record(payload, content=content, metadata=metadata)
    except Exception as err:  # noqa: BLE001 - a presentation without its durable fact is not a successful present
        logger.warning("Could not persist deliverable.presented: %s", err)
        return ToolResult.error("Could not record the deliverable in the session transcript.")

    return ToolResult(
        content=[TextBlock(text=f"Presented '{relative}' ({size} bytes, sha256 {sha256}).")],
        cards=[Card.notice(content, level="success", title="Deliverable presented")],
        metadata={"path": relative, "kind": kind, "size": size, "sha256": sha256},
    )
