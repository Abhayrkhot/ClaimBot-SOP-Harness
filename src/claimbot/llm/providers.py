"""Provider adapters. Each maps the neutral request onto one vendor SDK and its error types.

The OpenAI-compatible adapter covers OpenAI, vLLM, Ollama and other compatible servers with
JSON-schema constrained decoding. The Anthropic adapter uses the official SDK's structured
outputs. SDK-level retries are disabled in both; ``LLMClient`` owns retries so the shared turn
budget, the circuit breaker and the fallback chain see every attempt.
"""

from __future__ import annotations

import json
from collections.abc import Callable
from typing import Any

from .base import LLMOutputError, LLMRequest, LLMResponse, ProviderError

# Models for which the server-side refusal fallback is enabled by default.
_FALLBACK_MODELS = {
    "claude-opus-5-5",
    "claude-opus-5",
    "claude-sonnet-5-5",
    "claude-fable-5-1",
    "claude-fable-5-2",
}


class OpenAICompatProvider:
    def __init__(
        self,
        model: str,
        api_key: str,
        base_url: str | None = None,
        extra_body: dict[str, Any] | None = None,
        name: str | None = None,
        http_client: Any = None,
    ) -> None:
        from openai import AsyncOpenAI

        self.model = model
        self.name = name or ("openai" if base_url is None else "openai-compatible")
        self.extra_body = extra_body or {}
        self._client = AsyncOpenAI(api_key=api_key, base_url=base_url, max_retries=0, http_client=http_client)

    async def complete(self, request: LLMRequest, timeout: float) -> LLMResponse:
        import openai

        kwargs: dict[str, Any] = {}
        if request.schema is not None:
            kwargs["response_format"] = {
                "type": "json_schema",
                "json_schema": {"name": request.schema_name, "strict": True, "schema": request.schema},
            }
        try:
            response = await self._client.chat.completions.create(
                model=self.model,
                messages=[
                    {"role": "system", "content": request.system},
                    {"role": "user", "content": request.user},
                ],
                max_tokens=request.max_tokens,
                temperature=request.temperature,
                timeout=timeout,
                extra_body=self.extra_body or None,
                **kwargs,
            )
        except (openai.APITimeoutError, openai.APIConnectionError, openai.RateLimitError) as exc:
            raise ProviderError(type(exc).__name__, retryable=True) from exc
        except openai.APIStatusError as exc:
            raise ProviderError(f"HTTP {exc.status_code}", retryable=exc.status_code >= 500) from exc
        choice = response.choices[0]
        if choice.finish_reason == "length":
            raise LLMOutputError("response truncated at max_tokens")
        usage = response.usage
        return LLMResponse(
            text=choice.message.content or "",
            model=response.model or self.model,
            input_tokens=usage.prompt_tokens if usage else 0,
            output_tokens=usage.completion_tokens if usage else 0,
        )


class AnthropicProvider:
    def __init__(
        self, model: str, api_key: str | None = None, effort: str | None = None, http_client: Any = None
    ) -> None:
        from anthropic import AsyncAnthropic

        self.model = model
        self.name = "anthropic"
        self.effort = effort
        self._client = AsyncAnthropic(api_key=api_key, max_retries=0, http_client=http_client)

    async def complete(self, request: LLMRequest, timeout: float) -> LLMResponse:
        import anthropic

        output_config: dict[str, Any] = {}
        if request.schema is not None:
            output_config["format"] = {"type": "json_schema", "schema": request.schema}
        if self.effort:
            output_config["effort"] = self.effort
        kwargs: dict[str, Any] = {"output_config": output_config} if output_config else {}
        try:
            if self.model in _FALLBACK_MODELS:
                response = await self._client.beta.messages.create(
                    model=self.model,
                    max_tokens=max(request.max_tokens, 2048),
                    system=request.system,
                    messages=[{"role": "user", "content": request.user}],
                    timeout=timeout,
                    betas=["server-side-fallback-2026-07-01"],
                    fallbacks="default",
                    **kwargs,
                )
            else:
                response = await self._client.messages.create(
                    model=self.model,
                    max_tokens=max(request.max_tokens, 2048),
                    system=request.system,
                    messages=[{"role": "user", "content": request.user}],
                    timeout=timeout,
                    **kwargs,
                )
        except (anthropic.APITimeoutError, anthropic.APIConnectionError, anthropic.RateLimitError) as exc:
            raise ProviderError(type(exc).__name__, retryable=True) from exc
        except anthropic.APIStatusError as exc:
            raise ProviderError(f"HTTP {exc.status_code}", retryable=exc.status_code >= 500) from exc
        if response.stop_reason == "refusal":
            raise ProviderError("refusal", retryable=False)
        if response.stop_reason == "max_tokens":
            raise LLMOutputError("response truncated at max_tokens")
        text = next((b.text for b in response.content if getattr(b, "type", None) == "text"), "")
        return LLMResponse(
            text=text,
            model=response.model,
            input_tokens=response.usage.input_tokens,
            output_tokens=response.usage.output_tokens,
        )


class ScriptedProvider:
    """Deterministic test double: answers with a function of the request, or a queue of texts."""

    def __init__(
        self,
        responder: Callable[[LLMRequest], str | dict[str, Any] | BaseException] | None = None,
        queue: list[str | dict[str, Any] | BaseException] | None = None,
        name: str = "scripted",
        latency: float = 0.0,
    ) -> None:
        self.name = name
        self.model = "scripted"
        self.responder = responder
        self.queue = list(queue or [])
        self.latency = latency
        self.requests: list[LLMRequest] = []

    async def complete(self, request: LLMRequest, timeout: float) -> LLMResponse:
        import asyncio

        self.requests.append(request)
        if self.latency:
            await asyncio.sleep(self.latency)
        item = self.responder(request) if self.responder else self.queue.pop(0)
        if isinstance(item, BaseException):
            raise item
        text = item if isinstance(item, str) else json.dumps(item)
        return LLMResponse(
            text=text, model=self.model, input_tokens=len(request.user) // 4, output_tokens=len(text) // 4
        )
