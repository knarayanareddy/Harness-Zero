import sys
from pathlib import Path

import pytest

_TESTS_DIR = str(Path(__file__).parent.resolve())


@pytest.fixture(autouse=True)
def event_contract_guard(monkeypatch: pytest.MonkeyPatch):
    """Every event the kernel or a plugin publishes must satisfy its payload contract.

    Wraps ``EventBus.publish`` and runs ``harness.sdk.events.validate_event`` on each event whose
    publisher lives outside ``tests/`` (tests publish synthetic envelopes to exercise bus mechanics).
    Any violation fails the test that caused it, so a payload drift is caught by whichever test first
    drives that code path — not by a user in the browser.
    """
    from harness.kernel.bus import EventBus
    from harness.sdk.events import validate_event

    original = EventBus.publish
    violations: list[str] = []

    def guarded_publish(self, event):
        caller = sys._getframe(1).f_code.co_filename
        if not caller.startswith(_TESTS_DIR):
            errors = validate_event(event)
            if errors:
                violations.append(f"{event.type} from {Path(caller).name}: {errors}")
        return original(self, event)

    monkeypatch.setattr(EventBus, "publish", guarded_publish)
    yield
    assert not violations, "event payload contract violations:\n" + "\n".join(violations)


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
