"""Required shipped-handler checks in jsdom; not real-browser qualification."""
from __future__ import annotations

import re
import shutil
import subprocess
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]


def test_session_event_scope_on_baseline_node():
    node = shutil.which("node")
    assert node is not None, "Node22 is required for the frontend acceptance lane"
    version = subprocess.run([node, "--version"], capture_output=True, text=True, timeout=10, check=False)
    assert version.returncode == 0 and version.stdout.startswith("v22."), version.stdout + version.stderr
    result = subprocess.run(
        [node, "--test", str(ROOT / "tests/frontends/test_session_event_scope.mjs")],
        cwd=ROOT, capture_output=True, text=True, timeout=60, check=False,
    )
    assert result.returncode == 0, result.stdout + result.stderr
    count = re.search(r"^# tests (\d+)$", result.stdout, re.MULTILINE)
    assert count is not None and int(count.group(1)) >= 4, result.stdout
