"""HTTP surface: a JSON chat endpoint and the test UI."""
import hmac
import os
import uuid
from pathlib import Path

from fastapi import FastAPI, Header, HTTPException
from fastapi.responses import HTMLResponse
from pydantic import BaseModel, Field

from .conversation import ConversationEngine
from .repository import Repository
from .store import Store
from .workflow import ConcurrentTurnError, ConversationService

app = FastAPI(title='Insurance SOP Agent')
service = ConversationService(Repository(), Store(os.getenv('SESSION_DB', ':memory:')),
                              conversation=ConversationEngine())


class Message(BaseModel):
    session_id: str | None = Field(default=None, max_length=64)
    text: str = Field(min_length=1, max_length=4000)


@app.post('/api/chat')
def chat(message: Message):
    if not message.text.strip():
        raise HTTPException(422, 'Message cannot be blank')
    if message.session_id and not service.exists(message.session_id):
        raise HTTPException(404, 'Session expired or not found. Start a new chat.')
    sid = message.session_id or str(uuid.uuid4())
    try:
        reply, state = service.respond(sid, message.text)
    except ConcurrentTurnError as exc:
        raise HTTPException(409, str(exc)) from exc
    # The caller may read the summary queued for their own session; /api/outbox is admin-only.
    queued = service.store.message_for(sid) if state.email_choice == 'queued_demo' else None
    return {'session_id': sid, 'reply': reply, 'phase': state.phase, 'verified': state.verified,
            'captured_fields': list(state.identity), 'case_id': state.case_id,
            'remembered_hint': state.hint.model_dump(exclude_none=True),
            'email_choice': state.email_choice, 'handoff': state.handoff,
            'response_mode': state.response_mode, 'email_preview': queued}


class EndRequest(BaseModel):
    session_id: str = Field(min_length=1, max_length=64)


@app.post('/api/end')
def end_chat(message: EndRequest):
    if not service.end_session(message.session_id):
        raise HTTPException(404, 'Session expired or not found')
    return {'ended': True}


@app.get('/api/health')
def health():
    return {'ok': True, 'mode': service.conversation.mode,
            'provider': type(service.conversation.provider).__name__ if service.conversation.provider else None}


@app.get('/api/outbox')
def outbox(authorization: str | None = Header(default=None)):
    token = os.getenv('ADMIN_API_TOKEN')
    if not token:
        raise HTTPException(404, 'Not found')
    if not authorization or not hmac.compare_digest(authorization, 'Bearer ' + token):
        raise HTTPException(401, 'Authentication required')
    return {'count': len(service.outbox), 'note': 'Demo only; no email delivery',
            'messages': service.outbox}


@app.get('/')
def ui():
    return HTMLResponse((Path(__file__).parent / 'index.html').read_text())
