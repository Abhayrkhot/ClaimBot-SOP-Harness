"""Declarative SOP specification.

An SOP is data: phases, the only legal transitions between them (each with a named guard),
which fact classes each phase may disclose, and which side effects each phase may perform.
The runtime loads the spec once, validates it, and the policy enforcer consults it on every
transition, disclosure and action. Domain code supplies the guard predicates by name.
"""

from __future__ import annotations

from collections import deque
from fnmatch import fnmatchcase
from pathlib import Path

import yaml
from pydantic import BaseModel, ConfigDict, Field, model_validator


class PhaseSpec(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    id: str
    description: str = ""
    disclose: tuple[str, ...] = ()
    """Glob patterns of fact classes this phase may disclose (``claim.*``)."""
    actions: tuple[str, ...] = ()
    """Side-effect actions this phase may perform."""
    terminal: bool = False


class TransitionSpec(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True, populate_by_name=True)

    source: str = Field(alias="from")
    """Source phase id, or ``*`` for any non-terminal phase."""
    target: str = Field(alias="to")
    guard: str | None = None


class ActionSpec(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    id: str
    requires_consent: bool = True
    max_per_session: int = Field(default=1, ge=1)
    guards: tuple[str, ...] = ()


class LimitsSpec(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    off_topic_before_handoff: int = Field(default=3, ge=1)
    identity_attempts: int = Field(default=3, ge=1)
    history_turns: int = Field(default=6, ge=1)


class SOPSpec(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    id: str
    version: int
    title: str
    initial: str
    phases: tuple[PhaseSpec, ...]
    transitions: tuple[TransitionSpec, ...]
    actions: tuple[ActionSpec, ...] = ()
    protected: tuple[str, ...] = ()
    """Fact classes that may only be disclosed to the verified owner of the fact."""
    disclosure_guards: tuple[str, ...] = ()
    """Guards that must all hold before any protected fact is disclosed."""
    limits: LimitsSpec = LimitsSpec()

    @model_validator(mode="after")
    def _validate(self) -> SOPSpec:
        ids = [p.id for p in self.phases]
        if len(ids) != len(set(ids)):
            raise ValueError("phase ids must be unique")
        known = set(ids)
        if self.initial not in known:
            raise ValueError(f"initial phase {self.initial!r} is not declared")
        action_ids = {a.id for a in self.actions}
        terminal = {p.id for p in self.phases if p.terminal}
        for t in self.transitions:
            if t.source != "*" and t.source not in known:
                raise ValueError(f"transition source {t.source!r} is not declared")
            if t.target not in known:
                raise ValueError(f"transition target {t.target!r} is not declared")
            if t.source in terminal:
                raise ValueError(f"terminal phase {t.source!r} cannot have outgoing transitions")
        for p in self.phases:
            missing = set(p.actions) - action_ids
            if missing:
                raise ValueError(f"phase {p.id!r} references undeclared actions {sorted(missing)}")
        unreachable = known - self._reachable()
        if unreachable:
            raise ValueError(f"phases unreachable from {self.initial!r}: {sorted(unreachable)}")
        return self

    def _reachable(self) -> set[str]:
        seen = {self.initial}
        queue = deque([self.initial])
        while queue:
            current = queue.popleft()
            for t in self.transitions:
                if (t.source in (current, "*")) and not self.phase(current).terminal and t.target not in seen:
                    seen.add(t.target)
                    queue.append(t.target)
        return seen

    def phase(self, phase_id: str) -> PhaseSpec:
        for p in self.phases:
            if p.id == phase_id:
                return p
        raise KeyError(phase_id)

    def edge(self, source: str, target: str) -> TransitionSpec | None:
        """The declared transition from ``source`` to ``target``; exact sources win over ``*``."""
        exact = [t for t in self.transitions if t.source == source and t.target == target]
        if exact:
            return exact[0]
        if self.phase(source).terminal:
            return None
        wildcard = [t for t in self.transitions if t.source == "*" and t.target == target]
        return wildcard[0] if wildcard else None

    def action(self, action_id: str) -> ActionSpec:
        for a in self.actions:
            if a.id == action_id:
                return a
        raise KeyError(action_id)

    def may_disclose(self, phase_id: str, fact_class: str) -> bool:
        return any(fnmatchcase(fact_class, pattern) for pattern in self.phase(phase_id).disclose)

    def is_protected(self, fact_class: str) -> bool:
        return any(fnmatchcase(fact_class, pattern) for pattern in self.protected)

    def guard_names(self) -> set[str]:
        names = {t.guard for t in self.transitions if t.guard}
        names.update(self.disclosure_guards)
        for a in self.actions:
            names.update(a.guards)
        return names


def load_spec(path: str | Path) -> SOPSpec:
    data = yaml.safe_load(Path(path).read_text())
    return SOPSpec.model_validate(data)
