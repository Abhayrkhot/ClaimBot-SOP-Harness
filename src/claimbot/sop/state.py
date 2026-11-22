"""Domain-neutral session envelope and the value types that cross the policy boundary."""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Literal

from pydantic import BaseModel, Field

ConsentDecision = Literal["offered", "granted", "declined"]
ResponseMode = Literal["model", "rules", "fallback"]


class HistoryItem(BaseModel):
    role: Literal["user", "assistant"]
    text: str


class BaseSession(BaseModel):
    """Generic state every SOP session carries. Domains subclass it with their own memory.

    ``phase`` is only ever changed through ``TurnContext.transition``; the engine checks
    that after every turn. The store keeps the optimistic-concurrency revision outside the
    model, so a stale copy can never be committed over a newer one.
    """

    sop_id: str
    sop_version: int
    phase: str
    turn: int = 0
    verified: bool = False
    party_id: str | None = None
    awaiting: str | None = None
    """What the agent last asked for (identity, claim_choice, email_decision, ...)."""
    off_topic_streak: int = 0
    identity_failures: int = 0
    consents: dict[str, ConsentDecision] = Field(default_factory=dict)
    actions_done: dict[str, int] = Field(default_factory=dict)
    history: list[HistoryItem] = Field(default_factory=list)
    response_mode: ResponseMode = "rules"
    last_request_id: str | None = None
    last_reply: str | None = None
    """Reply to ``last_request_id``, returned again when a client retries the same request."""


@dataclass(frozen=True)
class Fact:
    """One approved statement the agent may say, with the metadata policy needs.

    ``values`` are the salient tokens (dates, amounts, identifiers, document names) that the
    grounding verifier requires in, and allows into, a model-phrased reply.
    """

    id: str
    cls: str
    text: str
    owner: str | None = None
    case_id: str | None = None
    values: tuple[str, ...] = ()


@dataclass(frozen=True)
class AuditEvent:
    kind: str
    data: dict[str, Any] = field(default_factory=dict)


@dataclass(frozen=True)
class ActionRequest:
    """A side effect the policy enforcer authorized; committed atomically with the turn."""

    action: str
    recipient: str
    subject: str
    body: str
