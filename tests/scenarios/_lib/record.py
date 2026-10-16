"""``HARNESS_REPLAY=record`` — write a scenario's ``provider.jsonl`` from a live model (WS-01).

Recording is deliberately *not* a product feature: it is how this suite's fixtures are made, so it lives
with the suite and is driven by the environment, exactly as ``HARNESS_REPLAY=replay`` is. The shape is:

    HARNESS_REPLAY=record HARNESS_RECORD_PROVIDER=anthropic HARNESS_RECORD_MODEL=claude-... \\
        python -m pytest tests/scenarios -k my_scenario

The scenario then runs for real: the same kernel, the same gate, the same client lane — with the provider
named by ``HARNESS_RECORD_PROVIDER`` wrapped in a tee (`RecordingProvider`) that writes the stream to the
scenario dir. Because goldens are rewritten in this mode too, the run ends with a scenario whose recording
and whose expected output both describe what the real model did; `git diff` is the review.

`HARNESS_RECORD_PROVIDER` defaults to **`mock`**, which makes the plumbing itself testable without a key or
a network: record a scenario against the built-in mock provider and replay it, and the two must agree. A
`RecordingProvider` refuses to write if a value of any `*_API_KEY` / `*_TOKEN` in the environment appears in
the stream, so a secret cannot end up in a committed fixture by accident.
"""

from __future__ import annotations

import os
from dataclasses import dataclass
from typing import Any

from .replay_provider import RecordingProvider, ReplayProvider, scenario_model
from .scenario import Scenario

DEFAULT_RECORD_PROVIDER = "mock"
DEFAULT_RECORD_MODEL = "mock-v1"


def mode() -> str:
    """The replay mode, read per call so a test can flip it (`scenario.REPLAY_MODE` is the module copy)."""
    return os.environ.get("HARNESS_REPLAY", "replay")


def is_recording() -> bool:
    return mode() == "record"


def record_model_id() -> str:
    """The model a recording boot asks for (the real provider's id, not the scripted fixture's)."""
    return os.environ.get("HARNESS_RECORD_MODEL") or DEFAULT_RECORD_MODEL


def record_provider_name() -> str:
    return os.environ.get("HARNESS_RECORD_PROVIDER") or DEFAULT_RECORD_PROVIDER


def _replay_provider(sc: Scenario) -> ReplayProvider:
    """The fixture provider, answering with the model id its recording was made against."""
    return ReplayProvider.from_file(sc.provider_file, model=scenario_model(sc), strict=sc.strict_exhaustion)


@dataclass
class ProviderHandle:
    """Whatever the scenario should register as its provider, plus how to finish recording.

    Runners stay agnostic: `active` answers `requests` / `remaining`, `finish()` flushes the tee when there
    is one. Callers must invoke `finish()` *before* any second boot of the kernel, so a restart lane replays
    the recording that was just written rather than discovering a missing file.
    """

    active: Any
    recorder: RecordingProvider | None = None

    @property
    def requests(self) -> list[Any]:
        return list(getattr(self.active, "requests", []) or [])

    @property
    def remaining(self) -> int:
        return 0 if self.recorder is not None else int(getattr(self.active, "remaining", 0) or 0)

    def finish(self) -> None:
        if self.recorder is not None:
            self.recorder.flush()
            self.recorder = None


def install_replay(sc: Scenario, runtime: Any) -> ReplayProvider:
    """Register the recorded stream for a boot that must *not* record — a restart lane, a second kernel.

    Those boots run no turns; they exist to read the same workspace back. Registering the replay provider
    anyway (rather than nothing) keeps the boot honest: it fails on a missing recording exactly as the
    primary lane would, so a scenario cannot pass by having no fixture at all.
    """
    provider = _replay_provider(sc)
    runtime.registry.register_provider(provider, plugin_name="scenario")
    return provider


def install_provider(sc: Scenario, runtime: Any) -> ProviderHandle:
    """Register the provider the scenario will talk to and hand back the handle.

    Both modes key the provider `replay` — the name every scenario already selects — so nothing in a scenario
    has to know which mode it ran in. Record mode additionally steps the wrapped provider aside (so no request
    can resolve past the tee) and swaps the model id, because only the real provider knows its models.
    """
    if not is_recording():
        provider = _replay_provider(sc)
        runtime.registry.register_provider(provider, plugin_name="scenario")
        return ProviderHandle(active=provider)

    name = record_provider_name()
    entry = runtime.registry.providers.get(name)
    if entry is None:
        available = ", ".join(sorted(runtime.registry.providers)) or "none loaded"
        raise RuntimeError(
            f"HARNESS_REPLAY=record: provider {name!r} is not loaded for scenario {sc.name!r}. "
            f"Available providers: {available}. Set HARNESS_RECORD_PROVIDER to one of them and make sure its "
            "credentials are in the environment (the recorder refuses to write a stream that contains one)."
        )
    # The registry hands out (instance, plugin_name) tuples, like every other `get`.
    inner = entry[0] if isinstance(entry, tuple) else entry
    recorder = RecordingProvider(inner, sc.dir)
    # The wrapped provider steps aside for the run (see `RecordingProvider` for why the tee keeps `replay`).
    runtime.registry.providers.pop(name, None)
    runtime.registry.register_provider(recorder, plugin_name="scenario")
    return ProviderHandle(active=recorder, recorder=recorder)
