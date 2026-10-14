"""
Artifact Manager & Generative UI Bridge.
Manages markdown artifacts, persistent manifest, and sandboxed HTML webviews.
"""
from __future__ import annotations

import html
import json
from dataclasses import dataclass, field
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from harness.kernel.validators import validate_safe_filename


@dataclass
class ArtifactMetadata:
    summary: str
    user_facing: bool = True
    request_feedback: bool = False
    created_at: str = field(
        default_factory=lambda: datetime.now(UTC).isoformat()
    )


class ArtifactManager:
    def __init__(self, brain_dir: Path):
        self.brain_dir = Path(brain_dir)
        self.artifacts_dir = self.brain_dir / "artifacts"
        self.artifacts_dir.mkdir(parents=True, exist_ok=True)
        self.manifest_file = self.brain_dir / "artifacts_manifest.json"

    def _safe(self, filename: str) -> Path:
        validate_safe_filename(filename, self.artifacts_dir)
        target = (self.artifacts_dir / filename).resolve()
        if not target.is_relative_to(self.artifacts_dir.resolve()):
            raise ValueError(f"Path traversal detected: {filename!r}")
        return target

    def write_artifact(
        self,
        filename: str,
        content: str,
        metadata: ArtifactMetadata,
    ) -> Path:
        target_path = self._safe(filename)
        target_path.write_text(content, encoding="utf-8")

        manifest = self.load_manifest()
        manifest[filename] = {
            "filename": filename,
            "path": str(target_path),
            "summary": metadata.summary,
            "user_facing": metadata.user_facing,
            "request_feedback": metadata.request_feedback,
            "bytes": len(content.encode("utf-8")),
            "created_at": metadata.created_at,
        }
        self.manifest_file.write_text(
            json.dumps(manifest, indent=2, ensure_ascii=False), encoding="utf-8"
        )
        return target_path

    def load_manifest(self) -> dict[str, Any]:
        if self.manifest_file.exists():
            try:
                return json.loads(self.manifest_file.read_text(encoding="utf-8"))
            except Exception:
                return {}
        return {}

    def get_artifact(self, filename: str) -> str | None:
        target = self._safe(filename)
        if target.exists():
            return target.read_text(encoding="utf-8")
        return None

    def render_sandboxed_html(
        self,
        raw_html: str,
        title: str = "Artifact Preview",
        api_origin: str = "*",
    ) -> str:
        """
        Wraps generative UI widget inside a sandboxed HTML structure with a postMessage bridge.
        """
        safe_title = html.escape(title)
        safe_origin = api_origin if api_origin and api_origin != "*" else "'*'"
        bridge_script = f"""
        <script>
          window.addEventListener('message', (event) => {{
            console.log('[Harness-Zero Bridge] Message received:', event.data);
          }});
          function notifyHarness(type, payload) {{
            if (window.parent) {{
              window.parent.postMessage({{ source: 'harness_artifact_ui', type: type, payload: payload }}, {safe_origin});
            }}
          }}
        </script>
        """
        return f"""<!DOCTYPE html>
<html lang="en">
<head>
  <meta charset="utf-8"/>
  <meta name="viewport" content="width=device-width, initial-scale=1.0"/>
  <title>{safe_title}</title>
  <style>
    body {{
      font-family: -apple-system, BlinkMacSystemFont, "Segoe UI", Roboto, Helvetica, Arial, sans-serif;
      margin: 0;
      padding: 1rem;
      background-color: #0f172a;
      color: #f8fafc;
    }}
  </style>
  {bridge_script}
</head>
<body>
  {raw_html}
</body>
</html>"""
