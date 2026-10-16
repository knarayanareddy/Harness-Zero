"""
ScriptedProvider: deterministic replay provider for tests and conformance validation.
Fulfills §11 of MODULAR_ARCHITECTURE.md.
"""
from __future__ import annotations

from collections.abc import AsyncIterator

from harness.sdk.types import (
    Done,
    ModelEvent,
    ModelInfo,
    ModelRequest,
    ProviderCaps,
)


class ReplayExhausted(RuntimeError):
    """The kernel asked for one more response than the provider holds.

    Off by default: a script that runs out answers `Done(end_turn)`, which is what makes small unit tests
    convenient. Turn it on (`strict_exhaustion=True`) wherever a runaway loop has to fail loudly instead of
    looping politely — `harness.sdk.testing.replay.ReplayProvider` turns it on, because a recording knows
    exactly how many requests the flow made.
    """


class ScriptedProvider:
    """
    Deterministic replay provider for test suites.
    Yields pre-scripted ModelEvent sequences or raises scripted Exceptions.
    Records all received ModelRequests for assertion.
    """

    name: str = "scripted"
    caps: ProviderCaps = ProviderCaps(
        native_tools=True,
        streaming=True,
        vision=True,
        cache_hints=False,
        thinking=True,
        json_output=True,
    )

    def __init__(
        self,
        responses: list[list[ModelEvent] | Exception] | None = None,
        models: list[ModelInfo] | None = None,
        *,
        strict_exhaustion: bool = False,
    ) -> None:
        self.responses: list[list[ModelEvent] | Exception] = list(responses or [])
        self.strict_exhaustion = strict_exhaustion
        self.requests: list[ModelRequest] = []
        self._models = list(models) if models is not None else [
            ModelInfo(
                id="scripted-v1",
                display_name="Scripted V1",
                context_tokens=128000,
                input_usd_per_mtok=3.0,
                output_usd_per_mtok=15.0,
            )
        ]
        self._index = 0

    def add_response(self, events: list[ModelEvent]) -> None:
        self.responses.append(events)

    def add_error(self, err: Exception) -> None:
        self.responses.append(err)

    # Readiness knobs for tests that exercise `models.list` grouping.
    configured: bool = True
    reachable: bool | None = None

    def is_configured(self) -> bool:
        return self.configured

    async def is_reachable(self) -> bool | None:
        return self.reachable

    async def list_models(self) -> list[ModelInfo]:
        return list(self._models)

    @property
    def remaining(self) -> int:
        """Responses still unreplayed — the way a test asserts the script was fully consumed."""
        return len(self.responses) - self._index

    async def generate(self, req: ModelRequest) -> AsyncIterator[ModelEvent]:
        self.requests.append(req)
        if self._index >= len(self.responses):
            if self.strict_exhaustion:
                raise ReplayExhausted(
                    f"model request #{self._index + 1} has no scripted response ({len(self.responses)} scripted)"
                )
            yield Done(stop_reason="end_turn")
            return

        resp = self.responses[self._index]
        self._index += 1

        if isinstance(resp, Exception):
            raise resp

        for event in resp:
            yield event

    async def aclose(self) -> None:
        pass

    def assert_requested(
        self,
        count: int | None = None,
        model: str | None = None,
        contains_text: str | None = None,
    ) -> None:
        """Asserts conditions on recorded requests."""
        if count is not None:
            assert len(self.requests) == count, f"Expected {count} requests, got {len(self.requests)}"
        if model is not None:
            assert any(r.model == model for r in self.requests), f"No request found for model '{model}'"
        if contains_text is not None:
            found = False
            for r in self.requests:
                for m in r.messages:
                    if contains_text in m.text():
                        found = True
                        break
            assert found, f"Expected text {contains_text!r} not found in received requests"

    def reset(self) -> None:
        """Resets replay state and recorded requests."""
        self.requests.clear()
        self._index = 0

