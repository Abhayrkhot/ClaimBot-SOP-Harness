"""Resilient LLM client: shared turn deadline, retries with jitter, breakers, fallback chain.

Every call in a turn draws from one deadline, so a slow provider cannot stretch a turn past
its budget. Retryable failures are retried with exponential backoff while budget remains,
and count toward a per-provider circuit breaker; an open breaker skips straight to the next
provider in the chain. Each attempt is recorded for metrics and for the eval's cost accounting.
"""

from __future__ import annotations

import asyncio
import random
import time
from dataclasses import dataclass
from typing import Any

from ..obs import metrics
from .base import LLMOutputError, LLMRequest, LLMResponse, LLMUnavailable, Provider, ProviderError, parse_json


@dataclass(frozen=True)
class CallRecord:
    stage: str
    provider: str
    model: str
    outcome: str
    latency_ms: float
    input_tokens: int = 0
    output_tokens: int = 0


class CircuitBreaker:
    """Opens after ``threshold`` consecutive retryable failures; one probe after ``reset_after``."""

    def __init__(self, threshold: int = 5, reset_after: float = 30.0, clock: Any = time.monotonic) -> None:
        self.threshold = threshold
        self.reset_after = reset_after
        self.clock = clock
        self.failures = 0
        self.opened_at: float | None = None
        self.probing = False

    @property
    def state(self) -> str:
        if self.opened_at is None:
            return "closed"
        return "half_open" if self.clock() - self.opened_at >= self.reset_after else "open"

    def allow(self) -> bool:
        state = self.state
        if state == "closed":
            return True
        if state == "half_open" and not self.probing:
            self.probing = True
            return True
        return False

    def success(self) -> None:
        self.failures = 0
        self.opened_at = None
        self.probing = False

    def failure(self) -> None:
        self.failures += 1
        self.probing = False
        if self.failures >= self.threshold:
            self.opened_at = self.clock()


class LLMClient:
    def __init__(
        self,
        providers: list[Provider],
        max_retries: int = 2,
        backoff: float = 0.2,
        breaker_threshold: int = 5,
        breaker_reset: float = 30.0,
    ) -> None:
        if not providers:
            raise ValueError("at least one provider is required")
        self.providers = providers
        self.max_retries = max_retries
        self.backoff = backoff
        self.breakers = {p.name: CircuitBreaker(breaker_threshold, breaker_reset) for p in providers}

    @property
    def primary(self) -> Provider:
        return self.providers[0]

    def health(self) -> dict[str, str]:
        return {name: b.state for name, b in self.breakers.items()}

    async def complete(self, request: LLMRequest, deadline: float, usage: list[Any]) -> LLMResponse:
        last_error = "no provider available"
        for provider in self.providers:
            breaker = self.breakers[provider.name]
            if not breaker.allow():
                usage.append(CallRecord(request.stage, provider.name, provider.model, "breaker_open", 0.0))
                metrics.LLM_CALLS.labels(provider.name, request.stage, "breaker_open").inc()
                continue
            for attempt in range(self.max_retries + 1):
                remaining = deadline - time.monotonic()
                if remaining <= 0.05:
                    raise LLMUnavailable("turn budget exhausted")
                started = time.perf_counter()
                outcome = "ok"
                try:
                    response = await asyncio.wait_for(provider.complete(request, remaining), remaining)
                except (TimeoutError, ProviderError) as exc:
                    retryable = not isinstance(exc, ProviderError) or exc.retryable
                    outcome = (
                        "timeout"
                        if isinstance(exc, TimeoutError)
                        else ("retryable" if retryable else "error")
                    )
                    last_error = f"{provider.name}: {exc}"
                    self._record(usage, request, provider, outcome, started)
                    if not retryable:
                        break
                    breaker.failure()
                    if attempt < self.max_retries and breaker.state == "closed":
                        delay = self.backoff * (2**attempt) * (0.5 + random.random())
                        await asyncio.sleep(min(delay, max(0.0, (deadline - time.monotonic()) / 2)))
                        continue
                    break
                except LLMOutputError:
                    self._record(usage, request, provider, "bad_output", started)
                    breaker.success()
                    raise
                breaker.success()
                self._record(usage, request, provider, outcome, started, response)
                return response
        raise LLMUnavailable(last_error)

    async def complete_json(self, request: LLMRequest, deadline: float, usage: list[Any]) -> dict[str, Any]:
        response = await self.complete(request, deadline, usage)
        return parse_json(response.text)

    @staticmethod
    def _record(
        usage: list[Any],
        request: LLMRequest,
        provider: Provider,
        outcome: str,
        started: float,
        response: LLMResponse | None = None,
    ) -> None:
        latency = (time.perf_counter() - started) * 1000
        record = CallRecord(
            request.stage,
            provider.name,
            response.model if response else provider.model,
            outcome,
            latency,
            response.input_tokens if response else 0,
            response.output_tokens if response else 0,
        )
        usage.append(record)
        metrics.LLM_CALLS.labels(provider.name, request.stage, outcome).inc()
        metrics.LLM_LATENCY.labels(provider.name, request.stage).observe(latency / 1000)
        if response:
            metrics.LLM_TOKENS.labels(provider.name, "input").inc(response.input_tokens)
            metrics.LLM_TOKENS.labels(provider.name, "output").inc(response.output_tokens)
