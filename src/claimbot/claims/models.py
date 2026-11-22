"""Typed contracts for the claims domain: the model's untrusted reading and the trusted session."""

from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, ConfigDict, Field

from ..sop.state import BaseSession
from .facts import Topic
from .identity import IdentityReading

Emotion = Literal["neutral", "frustrated", "angry", "anxious", "sad", "confused", "overwhelmed"]
Scope = Literal["claims", "off_topic", "unclear"]
DialogueAct = Literal["ask_or_inform", "finish", "confirm", "decline", "greeting", "other"]


class ClaimReference(BaseModel):
    model_config = ConfigDict(extra="forbid")

    action: Literal["none", "select", "switch", "ambiguous"] = Field(
        default="none",
        description=(
            "select: identifies which claim the caller means; switch: asks to move to a different claim;"
            " ambiguous: wants a different claim but gives nothing that identifies it; none otherwise."
        ),
    )
    case_id: str | None = Field(default=None, description="Claim number written in the current message.")
    case_type: str | None = None
    status: str | None = None
    month: int | None = Field(default=None, ge=1, le=12, description="Month the claim is from.")
    year: int | None = Field(default=None, ge=1900, le=2100)


class DocumentUpdate(BaseModel):
    model_config = ConfigDict(extra="forbid")

    document: str
    status: Literal["missing", "available"]


class Interpretation(BaseModel):
    """One structured reading of the current message. Every field is a proposal."""

    model_config = ConfigDict(extra="forbid")

    scope: Scope
    emotion: Emotion = "neutral"
    act: DialogueAct = "ask_or_inform"
    wants_human: bool = False
    email_decision: Literal["send", "skip", "none"] = "none"
    topics: list[Topic] = Field(default_factory=list, max_length=8)
    claim: ClaimReference = Field(default_factory=ClaimReference)
    documents: list[DocumentUpdate] = Field(default_factory=list, max_length=6)
    identity: IdentityReading | None = None


class RememberedHint(BaseModel):
    case_id: str | None = None
    case_type: str | None = None
    status: str | None = None
    month: int | None = None
    year: int | None = None


class DocumentNote(BaseModel):
    status: Literal["missing", "available"]
    turn: int


class ClaimsSession(BaseSession):
    evidence: dict[str, list[str]] = Field(default_factory=dict)
    """Verification field -> party ids whose record matches what the caller supplied."""
    hint: RememberedHint = Field(default_factory=RememberedHint)
    case_id: str | None = None
    pending_topics: list[str] = Field(default_factory=list)
    documents: dict[str, DocumentNote] = Field(default_factory=dict)
    discussed: list[str] = Field(default_factory=list)
    """Fact ids (``case:fact``) already told to the caller; feeds the summary email."""
    handoff: Literal["offered", "requested"] | None = None
    last_ack_turn: int | None = None
