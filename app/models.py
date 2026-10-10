"""Typed state and untrusted model output."""
from enum import StrEnum
from typing import Literal
from pydantic import BaseModel, ConfigDict, Field

Scope = Literal['insurance', 'mixed', 'unrelated', 'unclear']
Emotion = Literal['neutral', 'frustrated', 'angry', 'anxious', 'sad', 'confused', 'overwhelmed']
Topic = Literal['general_help', 'status', 'denial_reason', 'documents', 'appeal_support', 'appeal_deadline', 'filing_date',
                'net_payment', 'expected_reimbursement', 'allowed_max', 'submission',
                'decision_date', 'notification', 'other']
ACK = {'angry': 'I hear how upsetting this has been. Let’s work through what I can check.', 'frustrated': 'I understand why you want a clearer explanation.', 'anxious': 'We can take this one step at a time and check what is known.', 'sad': 'I’m sorry you’re dealing with this.', 'confused': 'Let me help make the next step clearer.', 'overwhelmed': 'We can slow down and focus on one step at a time.'}

class Phase(StrEnum):
    VERIFY_ID = 'VERIFY_ID'; RESOLVE_INTENT = 'RESOLVE_INTENT'; PROCESS_CASE = 'PROCESS_CASE'; POST_PROCESS = 'POST_PROCESS'; COMPLETE = 'COMPLETE'

class Hint(BaseModel):
    case_id: str | None = Field(default=None, description='Claim ID explicitly present in the current message.')
    case_type: Literal['healthcare', 'dental', 'auto'] | None = Field(default=None, description='Claim type described in the current message.')
    month: int | None = Field(default=None, ge=1, le=12, description='Claim month described in the current message, regardless of word order.')
    year: int | None = Field(default=None, ge=1900, le=2200, description='Claim year explicitly present in the current message.')
    status: Literal['denied', 'closed', 'open'] | None = Field(default=None, description='Claim status described in the current message.')

class MemoryUpdate(BaseModel):
    model_config = ConfigDict(extra='forbid')
    kind: Literal['note', 'correction', 'reference']
    key: str = Field(min_length=1, max_length=64)
    value: str = Field(min_length=1, max_length=240)

class DocumentUpdate(BaseModel):
    model_config = ConfigDict(extra='forbid')
    document: Literal['pathology report', 'office note', 'diagnosis report', 'repair estimate', 'accident scene photos']
    status: Literal['missing', 'available']

class Control(BaseModel):
    model_config = ConfigDict(extra='forbid')
    scope: Scope
    action: Literal['continue', 'finish', 'unclear']
    claim_action: Literal['none', 'select', 'switch', 'reference', 'unclear', 'ambiguous_reference']
    email_consent: Literal['send', 'skip', 'none', 'unclear']
    needs_clarification: bool

class Content(BaseModel):
    model_config = ConfigDict(extra='forbid')
    questions: list[str] = Field(default_factory=list, max_length=6)
    hint: Hint = Field(default_factory=Hint)
    document_updates: list[DocumentUpdate] = Field(default_factory=list, max_length=6)
    topics: list[Topic] = Field(default_factory=list, max_length=6)

class Interpretation(BaseModel):
    model_config = ConfigDict(extra='forbid')
    control: Control
    content: Content
    emotion: Emotion
    wants_human: bool

class TopicSelection(BaseModel):
    model_config = ConfigDict(extra='forbid')
    topics: list[Topic] = Field(default_factory=list, max_length=6)

class Turn(BaseModel):
    model_config = ConfigDict(extra='forbid')
    scope: Scope
    questions: list[str] = Field(default_factory=list, max_length=6, description='Only questions or implied requests in the current message; empty when there are none.')
    emotion: Emotion = 'neutral'
    intensity: int = Field(default=0, ge=0, le=3)
    refusal: bool = False; wants_human: bool = False; wants_finish: bool = False
    action: Literal['continue', 'finish', 'unclear'] = Field(default='continue', description='Whether the current message clearly ends claim discussion.')
    claim_action: Literal['none', 'select', 'switch', 'reference', 'unclear', 'ambiguous_reference'] = Field(default='none', description='How the current message relates to claim selection or a change of active claim.')
    email_consent: Literal['send', 'skip', 'none', 'unclear'] = Field(default='none', description='Explicit response in the current message to a pending email offer; otherwise none.')
    hint: Hint = Field(default_factory=Hint)
    memory_updates: list[MemoryUpdate] = Field(default_factory=list, max_length=6)
    document_updates: list[DocumentUpdate] = Field(default_factory=list, max_length=6)
    topics: list[Topic] = Field(default_factory=list, max_length=6, description='Only fact topics directly requested in the current message; empty when no fact is requested.')
    fact_ids: list[str] = Field(default_factory=list, max_length=12)
    follow_up: Literal['documents', 'appeal', 'human', 'clarify', 'none'] = 'none'
    needs_clarification: bool = False

class DocumentMemory(BaseModel):
    status: Literal['missing', 'available']; source_turn: int; source_text: str

class Session(BaseModel):
    phase: Phase = Phase.VERIFY_ID
    identity: dict[str, str] = Field(default_factory=dict); verified: bool = False; party_id: str | None = None
    hint: Hint = Field(default_factory=Hint); case_id: str | None = None; original_question: str = ''
    opening_message: str = ''
    outstanding_clarification: str = ''
    pending_questions: list[str] = Field(default_factory=list); caller_notes: list[str] = Field(default_factory=list)
    pending_topics: list[Topic] = Field(default_factory=list)
    history: list[dict[str, str]] = Field(default_factory=list)
    document_memory: dict[str, DocumentMemory] = Field(default_factory=dict)
    discussed: list[str] = Field(default_factory=list); next_steps: list[str] = Field(default_factory=list)
    refusals: int = 0; irrelevant: int = 0; handoff: Literal['offered', 'requested'] | None = None
    email_choice: str | None = None; email_offer_pending: bool = False; finish_offered: bool = False
    response_mode: Literal['rules', 'model', 'fallback'] = 'rules'; revision: int = 0
    pending_email_body: str | None = Field(default=None, exclude=True)
