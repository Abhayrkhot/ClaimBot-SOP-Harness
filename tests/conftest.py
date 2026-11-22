from __future__ import annotations

import os
from collections.abc import AsyncIterator, Callable
from datetime import date
from typing import Any

import pytest

from claimbot.claims.domain import ClaimsDomain
from claimbot.claims.interpreter import RuleInterpreter
from claimbot.claims.models import Interpretation
from claimbot.claims.render import TemplateRenderer
from claimbot.claims.repository import Repository
from claimbot.sop.engine import Engine
from claimbot.store.sqlite import SQLiteStore

TODAY = date(2026, 11, 22)

for key in ("OPENAI_API_KEY", "ANTHROPIC_API_KEY", "LLM_API_KEY", "LLM_MODEL", "LLM_BASE_URL"):
    os.environ.pop(key, None)


class QueueInterpreter:
    """Test double that returns pre-built interpretations (or builds them from the message)."""

    mode = "model"

    def __init__(
        self, items: list[Interpretation | Callable[[Any, str], Interpretation]] | None = None
    ) -> None:
        self.items = list(items or [])

    def push(self, item: Interpretation | Callable[[Any, str], Interpretation]) -> None:
        self.items.append(item)

    async def interpret(self, session: Any, text: str, ctx: Any) -> Interpretation:
        item = self.items.pop(0)
        return item(session, text) if callable(item) else item


@pytest.fixture
def repo() -> Repository:
    return Repository.load()


@pytest.fixture
async def store() -> AsyncIterator[SQLiteStore]:
    s = await SQLiteStore(":memory:").open()
    yield s
    await s.close()


@pytest.fixture
def make_engine(repo: Repository, store: SQLiteStore) -> Callable[..., Engine]:
    def build(interpreter: Any = None, renderer: Any = None) -> Engine:
        return Engine(
            ClaimsDomain(repo),
            interpreter or RuleInterpreter(repo),
            renderer or TemplateRenderer(),
            store,
            clock=lambda: TODAY,
        )

    return build


class Chat:
    """Drive one conversation through an engine and keep every result."""

    def __init__(self, engine: Engine) -> None:
        self.engine = engine
        self.sid: str | None = None
        self.results: list[Any] = []

    async def say(self, text: str) -> Any:
        result = await self.engine.handle(self.sid, text)
        self.sid = result.session_id
        self.results.append(result)
        return result

    @property
    def last(self) -> Any:
        return self.results[-1]

    def events(self, kind: str) -> list[Any]:
        return [e for r in self.results for e in r.events if e.kind == kind]


@pytest.fixture
def chat(make_engine: Callable[..., Engine]) -> Callable[..., Chat]:
    def build(interpreter: Any = None, renderer: Any = None) -> Chat:
        return Chat(make_engine(interpreter, renderer))

    return build
