"""Policy enforcement point (PEP) and the per-turn context domain code acts through.

Domain logic never mutates the phase, never renders a protected value and never queues a side
effect directly. It calls ``TurnContext.transition``, ``disclose`` and ``perform``; each call is
checked against the SOP spec and the domain's named guards, and every decision (allowed or
blocked) is written to the audit trail. A bug in domain logic can therefore drop a reply's
content, but it cannot leak a fact to an unverified caller or send an unconsented email.
"""

from __future__ import annotations

from collections.abc import Callable, Mapping
from dataclasses import dataclass, field
from datetime import date
from typing import Any

from .spec import SOPSpec
from .state import ActionRequest, AuditEvent, BaseSession, ConsentDecision, Fact

Guard = Callable[[BaseSession, "TurnContext"], bool]


class PolicyViolation(RuntimeError):
    """A domain proposal broke the SOP. Raised for transitions and actions, which are bugs."""

    def __init__(self, code: str, detail: str) -> None:
        super().__init__(f"{code}: {detail}")
        self.code = code
        self.detail = detail


class PolicyEnforcer:
    def __init__(self, spec: SOPSpec, guards: Mapping[str, Guard]) -> None:
        missing = spec.guard_names() - set(guards)
        if missing:
            raise ValueError(f"SOP {spec.id!r} references unregistered guards {sorted(missing)}")
        self.spec = spec
        self.guards = dict(guards)

    def _guard(self, name: str, session: BaseSession, ctx: TurnContext) -> bool:
        return bool(self.guards[name](session, ctx))

    def check_transition(self, session: BaseSession, target: str, ctx: TurnContext) -> None:
        edge = self.spec.edge(session.phase, target)
        if edge is None:
            raise PolicyViolation("illegal_transition", f"{session.phase} -> {target} is not declared")
        if edge.guard and not self._guard(edge.guard, session, ctx):
            raise PolicyViolation("guard_failed", f"{session.phase} -> {target} requires {edge.guard}")

    def disclosure_block_reason(self, session: BaseSession, fact: Fact, ctx: TurnContext) -> str | None:
        if not self.spec.may_disclose(session.phase, fact.cls):
            return f"class {fact.cls} not disclosable in {session.phase}"
        if self.spec.is_protected(fact.cls):
            if fact.owner is None:
                return "protected fact without an owner"
            for name in self.spec.disclosure_guards:
                if not self._guard(name, session, ctx):
                    return f"guard {name} failed"
            if fact.owner != session.party_id:
                return "fact owned by another party"
        return None

    def check_action(self, session: BaseSession, action: str, ctx: TurnContext) -> None:
        spec = self.spec.action(action)
        if action not in self.spec.phase(session.phase).actions:
            raise PolicyViolation("action_not_allowed", f"{action} is not allowed in {session.phase}")
        if spec.requires_consent and session.consents.get(action) != "granted":
            raise PolicyViolation("consent_missing", f"{action} requires granted consent")
        if session.actions_done.get(action, 0) >= spec.max_per_session:
            raise PolicyViolation("action_limit", f"{action} already performed")
        for name in spec.guards:
            if not self._guard(name, session, ctx):
                raise PolicyViolation("guard_failed", f"{action} requires {name}")


@dataclass
class Act:
    """One step of the response plan. ``facts`` must all have passed ``disclose``."""

    kind: str
    facts: tuple[str, ...] = ()
    params: dict[str, Any] = field(default_factory=dict)


class TurnContext:
    """Everything one turn may do, mediated by the policy enforcer."""

    def __init__(
        self,
        session: BaseSession,
        enforcer: PolicyEnforcer,
        text: str,
        today: date,
        deadline: float = float("inf"),
    ) -> None:
        self.session = session
        self.deadline = deadline
        """``time.monotonic()`` value by which every model call in this turn must finish."""
        self.enforcer = enforcer
        self.text = text
        self.today = today
        self.events: list[AuditEvent] = []
        self.disclosed: dict[str, Fact] = {}
        self.blocked: list[tuple[Fact, str]] = []
        self.actions: list[ActionRequest] = []
        self.acts: list[Act] = []
        self.phase = session.phase
        self.usage: list[Any] = []
        """LLM call records (``claimbot.llm.client.CallRecord``) made during this turn."""
        self.redactions: list[str] = []
        """Literal substrings the domain wants scrubbed from stored history."""
        self.current_request: ActionRequest | None = None
        self.notes: dict[str, Any] = {}
        """Per-turn scratch space for domain code; never persisted."""

    # -- state changes ---------------------------------------------------------------------

    def transition(self, target: str) -> None:
        self.enforcer.check_transition(self.session, target, self)
        source = self.session.phase
        self.session.phase = target
        self.phase = target
        self.emit("phase_transition", source=source, target=target)

    def disclose(self, *facts: Fact) -> list[Fact]:
        """Authorize facts for this reply. Returns the ones that passed; blocks are audited."""
        allowed: list[Fact] = []
        for fact in facts:
            reason = self.enforcer.disclosure_block_reason(self.session, fact, self)
            if reason:
                self.blocked.append((fact, reason))
                self.emit("disclosure_blocked", fact=fact.id, cls=fact.cls, owner=fact.owner, reason=reason)
                continue
            if fact.id not in self.disclosed:
                self.disclosed[fact.id] = fact
                self.emit(
                    "fact_disclosed", fact=fact.id, cls=fact.cls, owner=fact.owner, case_id=fact.case_id
                )
            allowed.append(fact)
        return allowed

    def record_consent(self, action: str, decision: ConsentDecision) -> None:
        self.session.consents[action] = decision
        self.emit("consent", action=action, decision=decision)

    def perform(self, request: ActionRequest) -> None:
        self.current_request = request
        try:
            self.enforcer.check_action(self.session, request.action, self)
        finally:
            self.current_request = None
        self.session.actions_done[request.action] = self.session.actions_done.get(request.action, 0) + 1
        self.actions.append(request)
        self.emit("action_queued", action=request.action)

    # -- response plan and audit -------------------------------------------------------------

    def say(self, kind: str, *facts: Fact, **params: Any) -> None:
        """Add a response step. Facts not authorized by ``disclose`` are dropped here."""
        ids = tuple(f.id for f in facts if f.id in self.disclosed)
        if facts and not ids:
            return
        self.acts.append(Act(kind=kind, facts=ids, params=params))

    def emit(self, kind: str, **data: Any) -> None:
        self.events.append(AuditEvent(kind=kind, data=data))
