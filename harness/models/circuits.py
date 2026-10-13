"""
Adaptive Circuit Breakers & Pre-Flight Complexity Classifier.
Enables self-healing model failovers and cost-effective tier selection.
"""
from __future__ import annotations

from dataclasses import dataclass, field
import time
from typing import Dict, List, Optional

from harness.core.types import CircuitState, ModelTier


@dataclass
class ProviderEndpoint:
    provider: str
    model: str
    failure_count: int = 0
    state: CircuitState = CircuitState.CLOSED
    last_failure_time: float = 0.0
    cooldown_seconds: float = 60.0

    def record_success(self) -> None:
        self.failure_count = 0
        self.state = CircuitState.CLOSED

    def record_failure(self) -> None:
        self.failure_count += 1
        self.last_failure_time = time.time()
        if self.failure_count >= 3:
            self.state = CircuitState.OPEN

    def is_available(self) -> bool:
        if self.state == CircuitState.CLOSED:
            return True
        if self.state == CircuitState.OPEN:
            if (time.time() - self.last_failure_time) > self.cooldown_seconds:
                self.state = CircuitState.HALF_OPEN
                return True
            return False
        if self.state == CircuitState.HALF_OPEN:
            return True
        return False


class IntelligentModelRouter:
    def __init__(self):
        self.tiers: Dict[ModelTier, List[ProviderEndpoint]] = {
            ModelTier.FAST: [
                ProviderEndpoint("anthropic", "claude-3-5-haiku-latest"),
                ProviderEndpoint("openai", "gpt-4o-mini"),
                ProviderEndpoint("gemini", "gemini-2.0-flash"),
                ProviderEndpoint("deepseek", "deepseek-chat"),
                ProviderEndpoint("local", "qwen2.5-coder:7b"),
            ],
            ModelTier.BALANCED: [
                ProviderEndpoint("anthropic", "claude-3-5-sonnet-latest"),
                ProviderEndpoint("openai", "gpt-4o"),
                ProviderEndpoint("deepseek", "deepseek-chat"),
                ProviderEndpoint("gemini", "gemini-1.5-pro"),
                ProviderEndpoint("local", "qwen2.5-coder:32b"),
            ],
            ModelTier.REASONING: [
                ProviderEndpoint("openai", "o3-mini"),
                ProviderEndpoint("anthropic", "claude-3-7-sonnet"),
                ProviderEndpoint("deepseek", "deepseek-reasoner"),
                ProviderEndpoint("openai", "o1"),
                ProviderEndpoint("local", "deepseek-r1:14b"),
            ],
        }

    def classify_intent(
        self, prompt: str, code_context_length: int = 0
    ) -> ModelTier:
        lower = prompt.lower()
        reasoning_keywords = [
            "architect",
            "refactor whole",
            "concurrency bug",
            "deadlock",
            "race condition",
            "distributed",
            "prove",
            "deep reasoning",
            "migrate database",
        ]
        if any(k in lower for k in reasoning_keywords) or code_context_length > 30000:
            return ModelTier.REASONING

        fast_keywords = [
            "commit message",
            "format",
            "summarize",
            "explain snippet",
            "generate test name",
            "fix typo",
            "spellcheck",
        ]
        if any(k in lower for k in fast_keywords) or (
            code_context_length < 500 and len(prompt) < 150
        ):
            return ModelTier.FAST

        return ModelTier.BALANCED

    def select_active_endpoint(
        self,
        tier: ModelTier,
        preferred_provider: Optional[str] = None,
    ) -> ProviderEndpoint:
        endpoints = self.tiers[tier]

        # 1. Try preferred provider if specified
        if preferred_provider:
            for ep in endpoints:
                if ep.provider == preferred_provider and ep.is_available():
                    return ep

        # 2. Try any available in tier
        for ep in endpoints:
            if ep.is_available():
                return ep

        # 3. Failover to balanced tier
        for ep in self.tiers[ModelTier.BALANCED]:
            if ep.is_available():
                return ep

        # Fallback to first
        return endpoints[0]
