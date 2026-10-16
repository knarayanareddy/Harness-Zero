"""Scenario harness for Harness-Zero (WS-01).

Runs the *real* kernel (``bootstrap_kernel``) and the *real* web daemon
(``HarnessWebServer``) against recorded provider streams, with no API key, and
compares normalised transcripts / event sequences / rendered timelines against
committed goldens. Everything here imports only public ``harness.*`` modules;
nothing patches the kernel.

Lives in ``tests/scenarios`` since consolidation P5. The providers and the recording format are the SDK's
(``harness.sdk.testing.replay``); this package is the scenario-side view: ``scenario.yaml``, the lanes, the
normaliser and the goldens. How-to: ``docs/testing/SCENARIOS.md``; schema and inventory: ``README.md``.
"""
