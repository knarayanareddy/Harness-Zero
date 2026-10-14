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
    ) -> None:
        self.responses: list[list[ModelEvent] | Exception] = list(responses or [])
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

    async def list_models(self) -> list[ModelInfo]:
        return list(self._models)

    async def generate(self, req: ModelRequest) -> AsyncIterator[ModelEvent]:
        self.requests.append(req)
        if self._index >= len(self.responses):
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

