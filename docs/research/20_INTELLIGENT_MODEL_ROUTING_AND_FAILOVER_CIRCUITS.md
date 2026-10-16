# 20: Intelligent Model Routing & Failover Circuits
*Autonomous Tier Classification, Multi-Provider Fallbacks, and Adaptive Circuit Breakers*

---

## 1. The Cost and Latency Bottleneck

Routing every single tool call, formatting edit, or commit message to a frontier reasoning model (o1, Claude 3.7 Sonnet thinking) causes:
* **Severe Latency**: 10–30 second turn times for trivial tasks.
* **Prohibitive Cost**: 10x to 50x higher API expenditures.
* **Fragile Reliability**: Single-provider outages or rate limits (HTTP 429) bring the agent to an immediate halt.

By incorporating patterns from **LiteLLM Auto-Routing**, **OpenRouter**, and **Inworld Router**, your harness achieves self-healing resilience and cost optimization:

```mermaid
flowchart TD
    PROMPT["Incoming User Turn / Subagent Task"] --> CLASSIFIER["Pre-Flight Complexity Classifier<br/>(Token length, code depth, reasoning need)"]

    CLASSIFIER -->|Tier 1: Trivial / Formatting| TIER1["Tier 1: Fast & Cheap Pool<br/>(Gemini Flash / GPT-4o-mini / Haiku)"]
    CLASSIFIER -->|Tier 2: Standard Coding| TIER2["Tier 2: Balanced Coding Pool<br/>(Claude 3.5 Sonnet / DeepSeek-V3 / GPT-4o)"]
    CLASSIFIER -->|Tier 3: Complex Architecture| TIER3["Tier 3: Frontier Reasoning Pool<br/>(OpenAI o1 / o3-mini / Sonnet Thinking)"]

    TIER2 --> CIRCUIT{"Adaptive Circuit Breaker"}
    CIRCUIT -->|Healthy (HTTP 200)| PRIMARY["Primary: Anthropic API"]
    CIRCUIT -->|Tripped (HTTP 429 / 503)| FALLBACK["Fallback: OpenAI / OpenRouter"]
```

---

## 2. Reverse-Engineered Routing & Failover Patterns

### A. Pre-Flight Complexity Classification
Evaluates query intent before making heavy LLM calls:
1. **Tier 1 (Fast & Economical)**:
   * Heuristics: Single-line instructions, git commit message drafting, docstring formatting, terminal output summarization.
   * Target Models: `gpt-4o-mini`, `claude-3-5-haiku`, `gemini-2.0-flash`.
2. **Tier 2 (General Coding)**:
   * Heuristics: Single-file bug fix, adding unit tests, standard API route creation.
   * Target Models: `claude-3-5-sonnet`, `deepseek-chat`, `gpt-4o`.
3. **Tier 3 (Deep Architectural Reasoning)**:
   * Heuristics: Multi-file refactors, distributed concurrency bugs, database schema migrations, cryptic compiler traces.
   * Target Models: `o1`, `o3-mini`, `claude-3-7-sonnet` (with thinking budget enabled).

### B. Adaptive Circuit Breaker (LiteLLM Pattern)
* Automatically monitors API response codes:
  * If a provider returns `429 Too Many Requests` or `503 Service Unavailable`, the circuit breaker trips to `OPEN`.
  * Traffic is immediately rerouted to an identical fallback provider without failing the user's turn or surfacing error stack traces.
  * After a 60-second cooldown, the breaker transitions to `HALF_OPEN` to probe provider recovery.

---

## 3. Custom Harness Implementation

```python
"""
intelligent_model_router.py - Multi-Tier Classifier and Resilient Failover Router
"""
from dataclasses import dataclass
from enum import Enum
import time

class ModelTier(Enum):
    FAST = "fast"
    BALANCED = "balanced"
    REASONING = "reasoning"

class CircuitState(Enum):
    CLOSED = "closed"
    OPEN = "open"
    HALF_OPEN = "half_open"

@dataclass
class ProviderEndpoint:
    name: str
    model: str
    failure_count: int = 0
    state: CircuitState = CircuitState.CLOSED
    last_failure_time: float = 0.0

class IntelligentModelRouter:
    def __init__(self):
        self.tiers = {
            ModelTier.FAST: [
                ProviderEndpoint("anthropic", "claude-3-5-haiku"),
                ProviderEndpoint("openai", "gpt-4o-mini"),
                ProviderEndpoint("google", "gemini-2.0-flash")
            ],
            ModelTier.BALANCED: [
                ProviderEndpoint("anthropic", "claude-3-5-sonnet"),
                ProviderEndpoint("deepseek", "deepseek-chat"),
                ProviderEndpoint("openai", "gpt-4o")
            ],
            ModelTier.REASONING: [
                ProviderEndpoint("openai", "o3-mini"),
                ProviderEndpoint("anthropic", "claude-3-7-sonnet"),
                ProviderEndpoint("openai", "o1")
            ]
        }

    def classify_intent(self, prompt: str, code_context_length: int) -> ModelTier:
        lower = prompt.lower()
        if any(k in lower for k in ["architect", "refactor whole", "concurrency bug", "deadlock", "race condition"]):
            return ModelTier.REASONING
        if any(k in lower for k in ["commit message", "format", "summarize", "explain snippet"]) or code_context_length < 500:
            return ModelTier.FAST
        return ModelTier.BALANCED

    def select_active_endpoint(self, tier: ModelTier) -> ProviderEndpoint:
        endpoints = self.tiers[tier]
        now = time.time()

        for ep in endpoints:
            # Check if open circuit cooled down
            if ep.state == CircuitState.OPEN and (now - ep.last_failure_time > 60.0):
                ep.state = CircuitState.HALF_OPEN

            if ep.state in (CircuitState.CLOSED, CircuitState.HALF_OPEN):
                return ep

        # If all tripped, fallback to secondary tier
        return self.tiers[ModelTier.BALANCED][0]

    def record_result(self, endpoint: ProviderEndpoint, success: bool):
        if success:
            endpoint.failure_count = 0
            endpoint.state = CircuitState.CLOSED
        else:
            endpoint.failure_count += 1
            endpoint.last_failure_time = time.time()
            if endpoint.failure_count >= 3:
                endpoint.state = CircuitState.OPEN
                print(f"[Circuit Breaker] Provider {endpoint.name} TRIPPED to OPEN. Rerouting traffic...")
```
