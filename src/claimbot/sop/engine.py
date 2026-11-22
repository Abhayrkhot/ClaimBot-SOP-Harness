"""The turn pipeline: load, interpret, decide under policy, render, verify, commit.

One turn is atomic. The engine works on a deep copy of the stored session; the copy, the audit
events and any authorized side effects are committed together with an optimistic-concurrency
check on the session revision, so concurrent requests for one session cannot interleave and a
summary email can never be queued twice. If interpretation fails the copy is discarded and the
caller gets a recovery message with nothing changed except the turn counter and history.
"""

from __future__ import annotations

import time
import uuid
from collections.abc import Callable
from dataclasses import dataclass, field
from datetime import date
from typing import Any, Protocol

from opentelemetry import trace

from ..obs import metrics
from .policy import PolicyEnforcer, TurnContext
from .spec import SOPSpec
from .state import ActionRequest, AuditEvent, BaseSession, HistoryItem

tracer = trace.get_tracer("claimbot.engine")


class SessionNotFound(LookupError):
    pass


class InterpretationError(RuntimeError):
    """The interpreter could not produce a valid reading of the message."""

    def __init__(self, stage: str, cause: BaseException | None = None) -> None:
        super().__init__(f"interpretation failed at {stage}: {type(cause).__name__ if cause else 'unknown'}")
        self.stage = stage
        self.cause = cause


class Domain(Protocol):
    spec: SOPSpec
    enforcer: PolicyEnforcer

    def new_session(self) -> BaseSession: ...
    def parse_session(self, raw: str) -> BaseSession: ...
    def decide(self, session: Any, interpretation: Any, ctx: TurnContext) -> None: ...
    def recover(self, session: Any, ctx: TurnContext) -> None: ...
    def terminal(self, session: Any, ctx: TurnContext) -> None: ...
    def redact(self, session: Any, text: str, ctx: TurnContext) -> str: ...
    def end(self, session: Any, ctx: TurnContext) -> None: ...


class Interpreter(Protocol):
    mode: str

    async def interpret(self, session: Any, text: str, ctx: TurnContext) -> Any: ...


@dataclass
class Rendered:
    text: str
    mode: str
    """template | llm | llm_rejected (verifier fell back to the template)."""
    verifier: dict[str, Any] = field(default_factory=dict)


class Renderer(Protocol):
    async def render(self, session: Any, ctx: TurnContext) -> Rendered: ...


class SessionStore(Protocol):
    async def load(self, session_id: str) -> tuple[str, int] | None: ...
    async def commit(
        self,
        session_id: str,
        expected_revision: int,
        state_json: str,
        turn: int,
        events: list[AuditEvent],
        actions: list[ActionRequest],
    ) -> int: ...


@dataclass
class TurnResult:
    session_id: str
    reply: str
    session: BaseSession
    revision: int
    events: list[AuditEvent]
    actions: list[ActionRequest]
    render_mode: str
    usage: list[Any]
    latency_ms: float
    replayed: bool = False


class Engine:
    def __init__(
        self,
        domain: Domain,
        interpreter: Interpreter,
        renderer: Renderer,
        store: SessionStore,
        clock: Callable[[], date] = date.today,
        turn_budget_seconds: float = 45.0,
    ) -> None:
        self.turn_budget = turn_budget_seconds
        self.domain = domain
        self.interpreter = interpreter
        self.renderer = renderer
        self.store = store
        self.clock = clock

    async def handle(self, session_id: str | None, text: str, request_id: str | None = None) -> TurnResult:
        started = time.perf_counter()
        with tracer.start_as_current_span("turn") as span:
            if session_id:
                loaded = await self.store.load(session_id)
                if loaded is None:
                    raise SessionNotFound(session_id)
                stored, revision = self.domain.parse_session(loaded[0]), loaded[1]
            else:
                session_id, stored, revision = uuid.uuid4().hex, self.domain.new_session(), 0
            span.set_attribute("claimbot.phase", stored.phase)
            if request_id and stored.last_request_id == request_id and stored.last_reply is not None:
                return TurnResult(
                    session_id, stored.last_reply, stored, revision, [], [], "replay", [], 0.0, True
                )

            session = stored.model_copy(deep=True)
            deadline = time.monotonic() + self.turn_budget
            ctx = TurnContext(session, self.domain.enforcer, text, self.clock(), deadline)
            phase_before = session.phase
            if self.domain.spec.phase(session.phase).terminal:
                self.domain.terminal(session, ctx)
            else:
                try:
                    with tracer.start_as_current_span("interpret"):
                        interpretation = await self.interpreter.interpret(session, text, ctx)
                except InterpretationError as exc:
                    usage = ctx.usage
                    session = stored.model_copy(deep=True)
                    ctx = TurnContext(session, self.domain.enforcer, text, self.clock(), deadline)
                    ctx.usage = usage
                    session.response_mode = "fallback"
                    ctx.emit("interpretation_failed", stage=exc.stage, error=type(exc.cause).__name__)
                    metrics.FALLBACKS.labels(stage=exc.stage).inc()
                    self.domain.recover(session, ctx)
                else:
                    session.response_mode = "model" if self.interpreter.mode == "model" else "rules"
                    with tracer.start_as_current_span("decide"):
                        self.domain.decide(session, interpretation, ctx)
            if session.phase != ctx.phase:
                raise RuntimeError("domain changed the phase without TurnContext.transition")

            with tracer.start_as_current_span("render"):
                rendered = await self.renderer.render(session, ctx)
            session.turn += 1
            limit = self.domain.spec.limits.history_turns * 2
            session.history = [
                *session.history,
                HistoryItem(role="user", text=self.domain.redact(session, text, ctx)),
                HistoryItem(role="assistant", text=rendered.text),
            ][-limit:]
            session.last_request_id = request_id
            session.last_reply = rendered.text
            with tracer.start_as_current_span("commit"):
                new_revision = await self.store.commit(
                    session_id, revision, session.model_dump_json(), session.turn, ctx.events, ctx.actions
                )
            latency_ms = (time.perf_counter() - started) * 1000
            metrics.observe_turn(phase_before, session.phase, rendered.mode, latency_ms, ctx)
            span.set_attribute("claimbot.render_mode", rendered.mode)
            return TurnResult(
                session_id=session_id,
                reply=rendered.text,
                session=session,
                revision=new_revision,
                events=ctx.events,
                actions=ctx.actions,
                render_mode=rendered.mode,
                usage=ctx.usage,
                latency_ms=latency_ms,
            )

    async def end(self, session_id: str) -> bool:
        """Close a session at the caller's request. Returns False if it does not exist."""
        loaded = await self.store.load(session_id)
        if loaded is None:
            return False
        session = self.domain.parse_session(loaded[0])
        if self.domain.spec.phase(session.phase).terminal:
            return True
        ctx = TurnContext(session, self.domain.enforcer, "", self.clock())
        self.domain.end(session, ctx)
        await self.store.commit(
            session_id, loaded[1], session.model_dump_json(), session.turn, ctx.events, []
        )
        return True
