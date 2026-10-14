import os
from pathlib import Path
import pytest


@pytest.fixture(autouse=True)
def hermetic_env(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    """Hermetic test isolation: isolates HOME, HARNESS_CONFIG_DIR, and clears real credentials."""
    home_dir = tmp_path / "home"
    home_dir.mkdir(parents=True, exist_ok=True)
    cfg_dir = tmp_path / "cfg"
    cfg_dir.mkdir(parents=True, exist_ok=True)

    monkeypatch.setenv("HOME", str(home_dir))
    monkeypatch.setenv("HARNESS_CONFIG_DIR", str(cfg_dir))

    # Strip environment keys so tests don't pick up host credentials
    for key in [
        "OPENAI_API_KEY",
        "ANTHROPIC_API_KEY",
        "GEMINI_API_KEY",
        "DEEPSEEK_API_KEY",
        "OPENROUTER_API_KEY",
        "HARNESS_AUTH_TOKEN",
        "HARNESS_IMPORT_HERMES_KEYS",
    ]:
        monkeypatch.delenv(key, raising=False)
