"""``scenario.yaml`` loader and golden comparison."""
from __future__ import annotations

import difflib
import json
import os
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import yaml

REPLAY_MODE = os.environ.get("HARNESS_REPLAY", "replay")  # replay | update | record


@dataclass
class Turn:
    prompt: str
    model: str | None = None  # what the UI would put in the `model` field (web lane)


@dataclass
class Scenario:
    name: str
    dir: Path
    kind: str  # kernel | web
    summary: str = ""
    settings: dict[str, Any] = field(default_factory=dict)
    turns: list[Turn] = field(default_factory=list)
    approvals: list[str] = field(default_factory=list)  # "approve" | "deny" in order of approval.required
    question_answers: list[dict[str, Any]] = field(default_factory=list)  # answer maps in question.asked order
    expect: dict[str, Any] = field(default_factory=dict)
    workspace_files: dict[str, str] = field(default_factory=dict)  # relative path -> content, seeded before the run
    config_files: dict[str, str] = field(default_factory=dict)  # relative path -> content, seeded into the config dir before boot
    mcp_servers: dict[str, Any] = field(default_factory=dict)
    secrets: dict[str, str] = field(default_factory=dict)  # seeded into the kernel's Secrets store (config dir) before the run
    strict_exhaustion: bool = True
    xfail: str | None = None  # reason: the scenario documents a known bug; it must FAIL until fixed

    @property
    def provider_file(self) -> Path:
        return self.dir / "provider.jsonl"

    @property
    def golden_dir(self) -> Path:
        return self.dir / "golden"


def load_scenario(path: Path) -> Scenario:
    data = yaml.safe_load((path / "scenario.yaml").read_text(encoding="utf-8")) or {}
    turns = [Turn(**t) if isinstance(t, dict) else Turn(prompt=str(t)) for t in data.get("turns", [])]
    return Scenario(
        name=data.get("name", path.name),
        dir=path,
        kind=data.get("kind", "kernel"),
        summary=data.get("summary", ""),
        settings=data.get("settings", {}) or {},
        turns=turns,
        approvals=list(data.get("approvals", []) or []),
        question_answers=list(data.get("question_answers", []) or []),
        expect=data.get("expect", {}) or {},
        workspace_files=data.get("workspace_files", {}) or {},
        config_files=data.get("config_files", {}) or {},
        mcp_servers=data.get("mcp_servers", {}) or {},
        strict_exhaustion=bool(data.get("strict_exhaustion", True)),
        xfail=data.get("xfail"),
        secrets={str(k): str(v) for k, v in (data.get("secrets") or {}).items()},
    )


def kind_of(directory: Path) -> str:
    """The `kind` field of a scenario, read without loading it.

    Collection needs it to attach the `web` marker, and it must never raise: a half-written scenario should
    fail its own test, not take the whole file's collection down with it.
    """
    try:
        data = yaml.safe_load((directory / "scenario.yaml").read_text(encoding="utf-8")) or {}
        return str(data.get("kind") or "kernel")
    except Exception:  # noqa: BLE001 - deliberately total
        return "kernel"


def discover(root: Path) -> list[Path]:
    return sorted(p for p in root.iterdir() if p.is_dir() and (p / "scenario.yaml").exists())


class GoldenMismatch(AssertionError):
    pass


def _dump(obj: Any) -> str:
    if isinstance(obj, str):
        return obj if obj.endswith("\n") else obj + "\n"
    return json.dumps(obj, indent=2, sort_keys=True, ensure_ascii=False) + "\n"


def compare_golden(path: Path, actual: Any, *, update: bool = False) -> None:
    """Compares ``actual`` with the golden at ``path``.

    * ``HARNESS_REPLAY=update`` (or ``update=True``) rewrites the golden.
    * On mismatch the actual output is written next to the golden under
      ``_last_run/`` and a unified diff is raised.
    """
    text = _dump(actual)
    if update or REPLAY_MODE in ("update", "record") or not path.exists():
        path.parent.mkdir(parents=True, exist_ok=True)
        if not path.exists() and REPLAY_MODE == "replay":
            # First run of a brand-new scenario: create the golden but tell the author.
            path.write_text(text, encoding="utf-8")
            raise GoldenMismatch(f"golden {path} did not exist; created it — re-run to verify, then commit")
        path.write_text(text, encoding="utf-8")
        return
    expected = path.read_text(encoding="utf-8")
    if expected == text:
        return
    last = path.parent.parent / "_last_run" / path.name
    last.parent.mkdir(parents=True, exist_ok=True)
    last.write_text(text, encoding="utf-8")
    diff = "".join(
        difflib.unified_diff(
            expected.splitlines(keepends=True),
            text.splitlines(keepends=True),
            fromfile=f"golden/{path.name}",
            tofile=f"_last_run/{path.name}",
            n=3,
        )
    )
    raise GoldenMismatch(f"{path.name} differs from golden (actual saved to {last}):\n{diff[:6000]}")
