"""Scenario harness for Harness-Zero (WS-01 implementation, review-side).

Runs the *real* kernel (``bootstrap_kernel``) and the *real* web daemon
(``HarnessWebServer``) against recorded provider streams, with no API key, and
compares normalised transcripts / event sequences / rendered timelines against
committed goldens. Everything here imports only public ``harness.*`` modules;
nothing patches the kernel.

Integration path: ``git mv additions/scenarios tests/scenarios``
(see README.md in this directory).
"""
