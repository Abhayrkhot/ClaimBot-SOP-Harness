"""Provider adapters against a mocked HTTP transport: request shape and error mapping."""

from __future__ import annotations

import json
from collections.abc import Callable
from typing import Any

import httpx
import httpx2
import pytest

from claimbot.llm.base import LLMOutputError, LLMRequest, ProviderError
from claimbot.llm.providers import AnthropicProvider, OpenAICompatProvider

SCHEMA = {
    "type": "object",
    "properties": {"a": {"type": "string"}},
    "required": ["a"],
    "additionalProperties": False,
}
REQ = LLMRequest(stage="t", system="sys", user="hello", schema=SCHEMA, schema_name="Out", max_tokens=300)


def transport(handler: Callable[[Any], Any], seen: list[Any], lib: Any = httpx) -> Any:
    """Mock HTTP client. The OpenAI SDK uses ``httpx``; the Anthropic SDK 2.x uses ``httpx2``."""

    def wrapped(request: Any) -> Any:
        seen.append(request)
        return handler(request)

    return lib.AsyncClient(transport=lib.MockTransport(wrapped))


def openai_body(content: str, finish: str = "stop") -> dict[str, Any]:
    return {
        "id": "c1",
        "object": "chat.completion",
        "created": 0,
        "model": "qwen",
        "choices": [
            {"index": 0, "message": {"role": "assistant", "content": content}, "finish_reason": finish}
        ],
        "usage": {"prompt_tokens": 11, "completion_tokens": 7, "total_tokens": 18},
    }


async def test_openai_compat_sends_strict_schema_and_extra_body() -> None:
    seen: list[httpx.Request] = []
    client = transport(lambda r: httpx.Response(200, json=openai_body('{"a": "x"}')), seen)
    p = OpenAICompatProvider(
        "qwen",
        "k",
        "http://vllm:8000/v1",
        {"chat_template_kwargs": {"enable_thinking": False}},
        http_client=client,
    )
    out = await p.complete(REQ, timeout=5)
    body = json.loads(seen[0].content)
    assert str(seen[0].url) == "http://vllm:8000/v1/chat/completions"
    assert body["response_format"]["json_schema"] == {"name": "Out", "strict": True, "schema": SCHEMA}
    assert body["chat_template_kwargs"] == {"enable_thinking": False}
    assert body["temperature"] == 0 and body["max_tokens"] == 300
    assert [m["role"] for m in body["messages"]] == ["system", "user"]
    assert (out.text, out.input_tokens, out.output_tokens) == ('{"a": "x"}', 11, 7)


@pytest.mark.parametrize(
    "status,retryable", [(429, True), (500, True), (503, True), (400, False), (401, False)]
)
async def test_openai_compat_error_mapping(status: int, retryable: bool) -> None:
    client = transport(lambda r: httpx.Response(status, json={"error": {"message": "x"}}), [])
    p = OpenAICompatProvider("qwen", "k", "http://vllm/v1", http_client=client)
    with pytest.raises(ProviderError) as info:
        await p.complete(REQ, timeout=5)
    assert info.value.retryable is retryable


async def test_openai_compat_truncation_is_output_error() -> None:
    client = transport(lambda r: httpx.Response(200, json=openai_body('{"a": ', "length")), [])
    p = OpenAICompatProvider("qwen", "k", "http://vllm/v1", http_client=client)
    with pytest.raises(LLMOutputError):
        await p.complete(REQ, timeout=5)


async def test_openai_compat_connection_error_is_retryable() -> None:
    def fail(request: httpx.Request) -> httpx.Response:
        raise httpx.ConnectError("refused", request=request)

    p = OpenAICompatProvider("qwen", "k", "http://vllm/v1", http_client=transport(fail, []))
    with pytest.raises(ProviderError) as info:
        await p.complete(REQ, timeout=5)
    assert info.value.retryable


def anthropic_body(text: str, stop: str = "end_turn", model: str = "claude-opus-5-5") -> dict[str, Any]:
    return {
        "id": "msg_1",
        "type": "message",
        "role": "assistant",
        "model": model,
        "content": [{"type": "text", "text": text}],
        "stop_reason": stop,
        "stop_sequence": None,
        "usage": {"input_tokens": 20, "output_tokens": 9},
    }


async def test_anthropic_structured_output_with_server_fallback() -> None:
    seen: list[httpx.Request] = []
    client = transport(lambda r: httpx2.Response(200, json=anthropic_body('{"a": "y"}')), seen, httpx2)
    p = AnthropicProvider("claude-opus-5-5", "k", effort="low", http_client=client)
    out = await p.complete(REQ, timeout=5)
    body = json.loads(seen[0].content)
    assert seen[0].url.path == "/v1/messages"
    assert "server-side-fallback-2026-07-01" in seen[0].headers["anthropic-beta"]
    assert body["fallbacks"] == "default"
    assert body["output_config"] == {"format": {"type": "json_schema", "schema": SCHEMA}, "effort": "low"}
    assert body["system"] == "sys" and body["messages"] == [{"role": "user", "content": "hello"}]
    assert "temperature" not in body
    assert (out.text, out.input_tokens, out.output_tokens) == ('{"a": "y"}', 20, 9)


async def test_anthropic_other_models_use_stable_endpoint() -> None:
    seen: list[httpx.Request] = []
    client = transport(
        lambda r: httpx2.Response(200, json=anthropic_body("{}", model="claude-haiku-5-5")), seen, httpx2
    )
    p = AnthropicProvider("claude-haiku-5-5", "k", http_client=client)
    await p.complete(REQ, timeout=5)
    body = json.loads(seen[0].content)
    assert "fallbacks" not in body and "anthropic-beta" not in seen[0].headers


async def test_anthropic_refusal_is_not_retryable() -> None:
    client = transport(lambda r: httpx2.Response(200, json=anthropic_body("", stop="refusal")), [], httpx2)
    p = AnthropicProvider("claude-opus-5-5", "k", http_client=client)
    with pytest.raises(ProviderError) as info:
        await p.complete(REQ, timeout=5)
    assert not info.value.retryable


@pytest.mark.parametrize("status,retryable", [(429, True), (529, True), (400, False)])
async def test_anthropic_error_mapping(status: int, retryable: bool) -> None:
    body = {"type": "error", "error": {"type": "x", "message": "x"}}
    client = transport(lambda r: httpx2.Response(status, json=body), [], httpx2)
    p = AnthropicProvider("claude-opus-5-5", "k", http_client=client)
    with pytest.raises(ProviderError) as info:
        await p.complete(REQ, timeout=5)
    assert info.value.retryable is retryable


async def test_error_label_keeps_status_and_code_but_not_message() -> None:
    body = {"error": {"message": "Incorrect API key provided: sk-proj-abc...wxyz", "type": "invalid_request_error",
                      "code": "invalid_api_key"}}  # fmt: skip
    client = transport(lambda r: httpx.Response(401, json=body), [])
    p = OpenAICompatProvider("qwen", "k", "http://vllm/v1", http_client=client)
    with pytest.raises(ProviderError) as info:
        await p.complete(REQ, timeout=5)
    assert str(info.value) == "HTTP 401 invalid_api_key" and not info.value.retryable
    assert "sk-" not in str(info.value)


async def test_rate_limit_is_retryable_with_code() -> None:
    body = {"error": {"message": "slow down", "type": "requests", "code": "rate_limit_exceeded"}}
    client = transport(lambda r: httpx.Response(429, json=body), [])
    p = OpenAICompatProvider("qwen", "k", "http://vllm/v1", http_client=client)
    with pytest.raises(ProviderError) as info:
        await p.complete(REQ, timeout=5)
    assert str(info.value) == "HTTP 429 rate_limit_exceeded" and info.value.retryable
