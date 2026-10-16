"""Documents and version strings stay true to the code (consolidation P6, review §4.6).

* one version: ``pyproject.toml`` = ``harness.__version__`` (= what ``/health`` reports, see
  ``tests/frontends/test_web_server.py``);
* ``docs/ARCHITECTURE.md`` names every built-in command, every event type and every card kind — add one in
  code and this test tells you to document it;
* released CHANGELOG sections are immutable (``158c836`` rewrote ``[0.2.1]`` after the tag; restored in P6).
  Their SHA-256 is pinned here; new work goes under ``[Unreleased]``.
"""
from __future__ import annotations

import hashlib
import re
import tomllib
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
ARCH = ROOT / "docs" / "ARCHITECTURE.md"
CHANGELOG = ROOT / "CHANGELOG.md"


def test_one_version_string():
    import harness

    pyproject = tomllib.loads((ROOT / "pyproject.toml").read_text(encoding="utf-8"))
    assert harness.__version__ == pyproject["project"]["version"]
    assert ARCH.read_text(encoding="utf-8").splitlines()[0] == f"# Harness-Zero architecture (v{harness.__version__})"


def test_architecture_doc_lists_every_command_event_and_card():
    from harness.kernel.commands import BUILTIN_COMMANDS
    from harness.sdk.cards import CARD_FIELDS
    from harness.sdk.events import EVENT_PAYLOADS

    text = ARCH.read_text(encoding="utf-8")
    missing_commands = [c for c in BUILTIN_COMMANDS if f"`{c}`" not in text]
    missing_events = [e for e in EVENT_PAYLOADS if f"`{e}`" not in text]
    missing_cards = [k for k in CARD_FIELDS if f"| `{k}` |" not in text]
    assert not missing_commands, f"docs/ARCHITECTURE.md §3 lacks commands {missing_commands}"
    assert not missing_events, f"docs/ARCHITECTURE.md §4 lacks events {missing_events}"
    assert not missing_cards, f"docs/ARCHITECTURE.md §4 lacks card kinds {missing_cards}"
    # Payload keys are listed verbatim per event row.
    import dataclasses

    for event_type, cls in EVENT_PAYLOADS.items():
        row = next(line for line in text.splitlines() if line.startswith(f"| `{event_type}` |"))
        for f in dataclasses.fields(cls):
            assert f.name in row, f"{event_type}: key {f.name!r} missing from its row in docs/ARCHITECTURE.md"
    # And nothing is documented that does not exist.
    documented = set(re.findall(r"^\| `([a-z_]+(?:\.[a-z_]+)*)` \| `[A-Z]\w+` \|", text, re.M))
    assert documented == set(EVENT_PAYLOADS), documented ^ set(EVENT_PAYLOADS)


def test_research_docs_moved_and_linked():
    research = ROOT / "docs" / "research"
    assert len(list(research.glob("[0-2][0-9]_*.md"))) == 22
    assert not list((ROOT / "docs").glob("[0-2][0-9]_*.md")), "research syntheses belong in docs/research/"
    assert "docs/research" in (ROOT / "README.md").read_text(encoding="utf-8")


def _section(text: str, name: str) -> str:
    m = re.search(r"(## \[" + re.escape(name) + r"\][^\n]*\n)(.*?)(?=\n## \[|\Z)", text, re.S)
    assert m, f"CHANGELOG has no section [{name}]"
    return m.group(0).strip()


RELEASED = {
    # sha256 of each released section exactly as it was at its git tag (v0.2.1 / v0.2.0 / v0.1.0).
    "0.2.1": "c7c9651afc8daada",
    "0.2.0": "96ff99a64f4fd1c8",
    "0.1.0": "cdc7453a58e06c0d",
}


def test_released_changelog_sections_are_immutable():
    text = CHANGELOG.read_text(encoding="utf-8")
    assert text.index("## [Unreleased]") < text.index("## [0.2.1]"), "[Unreleased] must come first"
    for name, digest in RELEASED.items():
        actual = hashlib.sha256(_section(text, name).encode("utf-8")).hexdigest()[:16]
        assert actual == digest, (
            f"CHANGELOG section [{name}] was edited after its release; add new entries under [Unreleased] instead "
            f"(if a release note is genuinely wrong, add an erratum bullet to [Unreleased] and re-pin the digest here)"
        )
