"""
Progressive Skills & Plugin Registry.
Scans multi-tier skill roots, extracts YAML metadata from SKILL.md,
and provides progressive disclosure (catalog vs full playbook).
"""
from __future__ import annotations

from dataclasses import dataclass, field
import os
from pathlib import Path
import re
from typing import Dict, List, Optional
import yaml


@dataclass
class Skill:
    name: str
    description: str
    path: Path
    tier: str  # "workspace", "user", or "builtin"
    content: str = ""
    metadata: Dict[str, any] = field(default_factory=dict)


class SkillRegistry:
    def __init__(
        self,
        workspace_root: Optional[Path] = None,
        user_root: Optional[Path] = None,
        builtin_root: Optional[Path] = None,
    ):
        self.workspace_root = Path(workspace_root) if workspace_root else None
        self.user_root = (
            Path(user_root)
            if user_root
            else Path.home() / ".harness" / "skills"
        )
        self.builtin_root = (
            Path(builtin_root)
            if builtin_root
            else Path(__file__).resolve().parent.parent.parent / "builtin" / "skills"
        )
        self.skills: Dict[str, Skill] = {}
        self.discover_all()

    def discover_all(self) -> None:
        self.skills.clear()
        # Scan in increasing order of precedence:
        # 1. Built-in (lowest)
        if self.builtin_root and self.builtin_root.exists():
            self._scan_directory(self.builtin_root, "builtin")

        # 2. User Global
        if self.user_root and self.user_root.exists():
            self._scan_directory(self.user_root, "user")

        # 3. Workspace (highest)
        if self.workspace_root:
            ws_skills = self.workspace_root / ".harness" / "skills"
            if ws_skills.exists():
                self._scan_directory(ws_skills, "workspace")
            # Also allow directly checking <workspace>/skills
            alt_skills = self.workspace_root / "skills"
            if alt_skills.exists() and alt_skills != ws_skills:
                self._scan_directory(alt_skills, "workspace")

    def _scan_directory(self, base_dir: Path, tier: str) -> None:
        if not base_dir.exists():
            return

        for skill_file in sorted(base_dir.glob("**/SKILL.md")):
            try:
                raw_text = skill_file.read_text(encoding="utf-8")
                match = re.match(r"^---\n(.*?)\n---(?:\n|$)(.*)", raw_text, re.DOTALL)
                if match:
                    frontmatter_raw = match.group(1)
                    body = match.group(2)
                    frontmatter = yaml.safe_load(frontmatter_raw) or {}
                    name = frontmatter.get("name")
                    desc = frontmatter.get("description", "").strip()
                    if name:
                        self.skills[name] = Skill(
                            name=name,
                            description=desc,
                            path=skill_file,
                            tier=tier,
                            content=body.strip(),
                            metadata=frontmatter,
                        )
                else:
                    # Fallback: skill without frontmatter
                    name = skill_file.parent.name
                    desc = f"Skill located at {skill_file}"
                    self.skills[name] = Skill(
                        name=name,
                        description=desc,
                        path=skill_file,
                        tier=tier,
                        content=raw_text.strip(),
                    )
            except Exception as e:
                # Keep scanning other skills gracefully
                continue

    def render_catalog(self) -> str:
        """
        Tier 1: Minimal System Prompt Injection (~50 tokens per skill).
        """
        if not self.skills:
            return "## Available Skills:\n(No skills installed)"

        lines = ["## Available Skills:"]
        for s in sorted(self.skills.values(), key=lambda x: x.name):
            lines.append(
                f"- **{s.name}** ({s.tier}): {s.description} (Read full instructions via `view_skill('{s.name}')`)"
            )
        return "\n".join(lines)

    def get_skill(self, name: str) -> Optional[Skill]:
        return self.skills.get(name)

    def view_skill(self, name: str) -> Optional[str]:
        """
        Tier 2: On-demand load of full playbook.
        """
        skill = self.skills.get(name)
        if not skill:
            return None
        return skill.path.read_text(encoding="utf-8")

    def list_skills(self) -> List[Skill]:
        return list(self.skills.values())
