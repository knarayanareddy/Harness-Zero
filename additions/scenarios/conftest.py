"""Hermetic environment for scenario runs.

Every test gets a private ``HOME`` / ``HARNESS_CONFIG_DIR`` so the user's real
``~/.harness`` (settings, secrets, plugins) can never leak into a golden.
"""
from __future__ import annotations

import os
import sys
from pathlib import Path

import pytest

HERE = Path(__file__).parent
if str(HERE) not in sys.path:  # allow `from _lib import …` when run from the repo root
    sys.path.insert(0, str(HERE))


@pytest.fixture
def hermetic_env(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    home = tmp_path / "home"
    conf = home / ".harness"
    conf.mkdir(parents=True)
    monkeypatch.setenv("HOME", str(home))
    monkeypatch.setenv("HARNESS_CONFIG_DIR", str(conf))
    # Never let a developer's real keys turn a replay into a live call.
    for k in list(os.environ):
        if k.endswith("_API_KEY") or k == "HARNESS_AUTH_TOKEN":
            monkeypatch.delenv(k, raising=False)
    return conf
