"""
macOS Screenshot Tool (§PLG-21).
Absorbs TOOL-08 and SIM-03.
Captures screen cleanly using macOS screencapture via ctx.executor and returns ImageBlock.
"""
from __future__ import annotations

import base64
from pathlib import Path
from typing import Any

from harness.sdk.context import ToolContext
from harness.sdk.types import ExecRequest, ImageBlock, RiskClass, TextBlock, ToolResult, ToolSpec

TAKE_SCREENSHOT_SPEC = ToolSpec(
    name="take_screenshot",
    description="Captures the active screen on macOS and returns the image as an ImageBlock.",
    input_schema={
        "type": "object",
        "properties": {
            "filename": {
                "type": "string",
                "description": "Optional filename to save the screenshot (default: 'screenshot.png').",
            },
        },
        "additionalProperties": False,
    },
    risk_class=RiskClass.READ,
    capabilities=frozenset(["fs:read", "exec"]),
)


def _safe_filename(name: str) -> str:
    cleaned = Path(name).name
    if not cleaned or cleaned in {".", ".."}:
        return "screenshot.png"
    if not cleaned.endswith((".png", ".jpg", ".jpeg")):
        cleaned = f"{cleaned}.png"
    return cleaned


async def handle_take_screenshot(args: dict[str, Any], ctx: ToolContext) -> ToolResult:
    """Captures screenshot using system screencapture and emits an ImageBlock."""
    raw_name = str(args.get("filename") or "screenshot.png")
    filename = _safe_filename(raw_name)

    screenshots_dir = ctx.data_dir / "screenshots"
    screenshots_dir.mkdir(parents=True, exist_ok=True)
    target_path = screenshots_dir / filename

    exec_req = ExecRequest(
        command=["screencapture", "-x", str(target_path)],
        cwd=ctx.workspace,
    )

    try:
        proc = await ctx.executor.start(exec_req)
        res = await proc.wait()
    except Exception as err:
        return ToolResult.error(f"Failed to invoke screencapture: {err}")

    if res.exit_code != 0:
        return ToolResult.error(f"screencapture exited with code {res.exit_code}: {res.stderr}")

    if not target_path.exists():
        return ToolResult.error("screencapture completed but screenshot file was not created.")

    try:
        img_bytes = target_path.read_bytes()
        b64_data = base64.b64encode(img_bytes).decode("utf-8")
        return ToolResult(
            content=[
                ImageBlock(media_type="image/png", data_b64=b64_data),
                TextBlock(text=f"Screenshot captured ({len(img_bytes)} bytes) and saved to {target_path.name}."),
            ],
            metadata={"path": str(target_path), "bytes": len(img_bytes)},
        )
    except Exception as err:
        return ToolResult.error(f"Failed to read screenshot image: {err}")
