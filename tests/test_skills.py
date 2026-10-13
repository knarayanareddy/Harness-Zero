from pathlib import Path
import pytest
from harness.skills.registry import SkillRegistry


def test_progressive_skill_discovery(tmp_path: Path):
    ws = tmp_path / "workspace"
    skills_dir = ws / ".harness" / "skills" / "my-test-skill"
    skills_dir.mkdir(parents=True)

    skill_file = skills_dir / "SKILL.md"
    skill_file.write_text(
        """---
name: my-test-skill
description: A test skill for testing progressive discovery.
version: 1.0.0
---

# Test Playbook
Follow these instructions carefully.
""",
        encoding="utf-8",
    )

    registry = SkillRegistry(workspace_root=ws)
    assert "my-test-skill" in registry.skills

    skill = registry.get_skill("my-test-skill")
    assert skill is not None
    assert skill.tier == "workspace"
    assert skill.description == "A test skill for testing progressive discovery."

    # Catalog rendering (Tier 1)
    catalog = registry.render_catalog()
    assert "my-test-skill" in catalog
    assert "view_skill('my-test-skill')" in catalog

    # Full view (Tier 2)
    full = registry.view_skill("my-test-skill")
    assert "# Test Playbook" in full
