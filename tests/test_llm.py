from __future__ import annotations

import json
import time
from typing import Any

import pytest

from claimbot.claims.domain import ClaimsDomain
from claimbot.claims.interpreter import LLMInterpreter
from claimbot.claims.models import Interpretation
from claimbot.llm.base import (
    LLMOutputError,
    LLMRequest,
    LLMUnavailable,
    ProviderError,
    parse_json,
    portable_schema,
)
from claimbot.llm.client import CircuitBreaker, LLMClient
from claimbot.llm.providers import ScriptedProvider
from claimbot.sop.engine import InterpretationError
from claimbot.sop.policy import TurnContext

from .conftest import TODAY

REQ = LLMRequest(stage="t", system="s", user="u")


def deadline(seconds: float = 5.0) -> float:
    return time.monotonic() + seconds


async def test_retry_then_success() -> None:
    p = ScriptedProvider(queue=[ProviderError("503", retryable=True), "ok"])
    usage: list[Any] = []
    client = LLMClient([p], max_retries=2, backoff=0.001)
    assert (await client.complete(REQ, deadline(), usage)).text == "ok"
    assert [u.outcome for u in usage] == ["retryable", "ok"]


async def test_non_retryable_goes_to_fallback() -> None:
    primary = ScriptedProvider(queue=[ProviderError("400", retryable=False)], name="primary")
    fallback = ScriptedProvider(queue=["fine"], name="fallback")
    usage: list[Any] = []
    client = LLMClient([primary, fallback], backoff=0.001)
    assert (await client.complete(REQ, deadline(), usage)).text == "fine"
    assert [(u.provider, u.outcome) for u in usage] == [("primary", "error"), ("fallback", "ok")]


async def test_timeout_respects_turn_budget() -> None:
    slow = ScriptedProvider(queue=["late"] * 5, latency=1.0)
    client = LLMClient([slow], max_retries=3, backoff=0.001)
    started = time.monotonic()
    with pytest.raises(LLMUnavailable):
        await client.complete(REQ, deadline(0.3), [])
    assert time.monotonic() - started < 0.8


async def test_breaker_opens_and_skips_provider() -> None:
    bad = ScriptedProvider(queue=[ProviderError("503", retryable=True)] * 10, name="bad")
    good = ScriptedProvider(queue=["a", "b", "c"], name="good")
    client = LLMClient([bad, good], max_retries=0, breaker_threshold=2, backoff=0.001)
    for _ in range(3):
        await client.complete(REQ, deadline(), [])
    assert client.health()["bad"] == "open"
    usage: list[Any] = []
    with pytest.raises(IndexError):
        await client.complete(REQ, deadline(), usage)  # good's queue is empty: proves bad was skipped
    assert usage[0].outcome == "breaker_open"


def test_breaker_half_open_probe() -> None:
    now = [0.0]
    b = CircuitBreaker(threshold=1, reset_after=10, clock=lambda: now[0])
    b.failure()
    assert not b.allow()
    now[0] = 11
    assert b.allow() and not b.allow()  # one probe only
    b.success()
    assert b.state == "closed"


def test_parse_json_tolerates_fences() -> None:
    assert parse_json('```json\n{"a": 1}\n```') == {"a": 1}
    assert parse_json('thinking... {"a": 2}') == {"a": 2}
    with pytest.raises(LLMOutputError):
        parse_json("no json here")


def test_portable_schema_is_closed_and_inlined() -> None:
    schema = portable_schema(Interpretation.model_json_schema())
    assert "$defs" not in json.dumps(schema)
    assert schema["additionalProperties"] is False
    assert set(schema["required"]) == set(schema["properties"])
    assert "maxItems" not in json.dumps(schema) and "default" not in json.dumps(schema)


def ctx_for(repo, phase: str = "VERIFY_ID") -> tuple[Any, TurnContext]:  # type: ignore[no-untyped-def]
    domain = ClaimsDomain(repo)
    session = domain.new_session().model_copy(update={"phase": phase})
    return session, TurnContext(session, domain.enforcer, "", TODAY, deadline())


def interp_json(**over: Any) -> dict[str, Any]:
    base: dict[str, Any] = {
        "scope": "claims",
        "emotion": "neutral",
        "act": "ask_or_inform",
        "wants_human": False,
        "email_decision": "none",
        "topics": [],
        "claim": {
            "action": "none",
            "case_id": None,
            "case_type": None,
            "status": None,
            "month": None,
            "year": None,
        },
        "documents": [],
    }
    return {**base, **over}


async def test_interpreter_schema_restricts_identity_to_message_spans(repo) -> None:  # type: ignore[no-untyped-def]
    provider = ScriptedProvider(
        queue=[
            interp_json(
                identity={
                    "subject": "caller",
                    "name": "Margaret Chen",
                    "birth_date_quote": None,
                    "phone": None,
                    "email": None,
                    "id_last4": "4472",
                    "policy_number": None,
                    "birth_date_is_callers": False,
                }
            )
        ]
    )
    interp = LLMInterpreter(LLMClient([provider]), repo)
    session, ctx = ctx_for(repo)
    text = "I'm Margaret Chen, last four 4472"
    result = await interp.interpret(session, text, ctx)
    assert result.identity and result.identity.id_last4 == "4472"
    schema = provider.requests[0].schema
    assert schema is not None
    idp = schema["properties"]["identity"]["properties"]
    assert idp["id_last4"]["anyOf"][0]["enum"] == ["4472"]
    assert "Margaret Chen" in idp["name"]["anyOf"][0]["enum"]
    assert idp["phone"] == {"type": "null"}
    doc_enum = schema["properties"]["documents"]["items"]["properties"]["document"]["enum"]
    assert doc_enum == repo.documents


async def test_interpreter_omits_identity_after_verification(repo) -> None:  # type: ignore[no-untyped-def]
    provider = ScriptedProvider(queue=[interp_json(topics=["status"])])
    interp = LLMInterpreter(LLMClient([provider]), repo)
    session, ctx = ctx_for(repo, "PROCESS_CASE")
    result = await interp.interpret(session, "status?", ctx)
    assert result.topics == ["status"]
    assert (
        provider.requests[0].schema is not None
        and "identity" not in provider.requests[0].schema["properties"]
    )


async def test_interpreter_retries_invalid_output_with_feedback(repo) -> None:  # type: ignore[no-untyped-def]
    provider = ScriptedProvider(queue=[{"scope": "nonsense"}, interp_json(topics=["status"])])
    interp = LLMInterpreter(LLMClient([provider]), repo)
    session, ctx = ctx_for(repo, "PROCESS_CASE")
    result = await interp.interpret(session, "status?", ctx)
    assert result.topics == ["status"]
    assert "feedback" in json.loads(provider.requests[1].user)
    assert [u.stage for u in ctx.usage] == ["interpret", "interpret_retry"]


async def test_interpreter_raises_after_two_invalid_outputs(repo) -> None:  # type: ignore[no-untyped-def]
    provider = ScriptedProvider(queue=["not json", "still not json"])
    interp = LLMInterpreter(LLMClient([provider]), repo)
    session, ctx = ctx_for(repo, "PROCESS_CASE")
    with pytest.raises(InterpretationError):
        await interp.interpret(session, "status?", ctx)


async def test_interpreter_raises_when_provider_down(repo) -> None:  # type: ignore[no-untyped-def]
    provider = ScriptedProvider(queue=[ProviderError("503", retryable=True)] * 5)
    interp = LLMInterpreter(LLMClient([provider], max_retries=1, backoff=0.001), repo)
    session, ctx = ctx_for(repo, "PROCESS_CASE")
    with pytest.raises(InterpretationError):
        await interp.interpret(session, "status?", ctx)
