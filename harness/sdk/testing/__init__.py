"""
Harness SDK Testing utilities and conformance test kits (§11).
"""
from __future__ import annotations

from harness.sdk.testing.kits import (
    ContextKit,
    ExecutorKit,
    FrontendKit,
    HookKit,
    PolicyKit,
    ProviderKit,
    StoreKit,
    StrategyKit,
    ToolKit,
)
from harness.sdk.testing.replay import (
    REPLAY_MODEL,
    REPLAY_PROVIDER_NAME,
    RecordingProvider,
    ReplayProvider,
    decode_event,
    dump_streams,
    encode_event,
    load_streams,
    request_summary,
    write_streams,
)
from harness.sdk.testing.scripted import ReplayExhausted, ScriptedProvider

__all__ = [
    "ScriptedProvider",
    "ReplayProvider",
    "RecordingProvider",
    "ReplayExhausted",
    "REPLAY_MODEL",
    "REPLAY_PROVIDER_NAME",
    # the recording format, so a suite can write fixtures without importing internals
    "encode_event",
    "decode_event",
    "load_streams",
    "dump_streams",
    "write_streams",
    "request_summary",
    "ProviderKit",
    "ToolKit",
    "PolicyKit",
    "ExecutorKit",
    "ContextKit",
    "StoreKit",
    "StrategyKit",
    "HookKit",
    "FrontendKit",
]
