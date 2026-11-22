from __future__ import annotations

from typing import Any

import pytest

from claimbot.claims.domain import ClaimsDomain
from claimbot.sop.policy import PolicyEnforcer, PolicyViolation, TurnContext
from claimbot.sop.spec import SOPSpec
from claimbot.sop.state import ActionRequest, Fact

from .conftest import TODAY

BASE: dict[str, Any] = {
    "id": "t",
    "version": 1,
    "title": "t",
    "initial": "A",
    "phases": [{"id": "A"}, {"id": "B"}, {"id": "Z", "terminal": True}],
    "transitions": [{"from": "A", "to": "B", "guard": "g"}, {"from": "B", "to": "Z"}],
}


def test_spec_validation_rejects_unreachable_phase() -> None:
    bad = {**BASE, "phases": [*BASE["phases"], {"id": "C"}]}
    with pytest.raises(ValueError, match="unreachable"):
        SOPSpec.model_validate(bad)


def test_spec_rejects_terminal_outgoing() -> None:
    bad = {**BASE, "transitions": [*BASE["transitions"], {"from": "Z", "to": "A"}]}
    with pytest.raises(ValueError, match="terminal"):
        SOPSpec.model_validate(bad)


def test_enforcer_requires_registered_guards() -> None:
    spec = SOPSpec.model_validate(BASE)
    with pytest.raises(ValueError, match="unregistered guards"):
        PolicyEnforcer(spec, {})


def test_claims_spec_loads(repo) -> None:  # type: ignore[no-untyped-def]
    domain = ClaimsDomain(repo)
    assert domain.spec.edge("VERIFY_ID", "RESOLVE_INTENT") is not None
    assert domain.spec.edge("VERIFY_ID", "PROCESS_CASE") is None
    assert domain.spec.edge("PROCESS_CASE", "HANDOFF") is not None  # wildcard edge
    assert domain.spec.edge("COMPLETE", "HANDOFF") is None  # terminal


def ctx_for(repo, **state: Any) -> tuple[ClaimsDomain, TurnContext]:  # type: ignore[no-untyped-def]
    domain = ClaimsDomain(repo)
    session = domain.new_session().model_copy(update=state)
    return domain, TurnContext(session, domain.enforcer, "", TODAY)


def test_transition_guard_enforced(repo) -> None:  # type: ignore[no-untyped-def]
    _, ctx = ctx_for(repo)
    with pytest.raises(PolicyViolation, match="identity_verified"):
        ctx.transition("RESOLVE_INTENT")
    with pytest.raises(PolicyViolation, match="illegal_transition"):
        ctx.transition("PROCESS_CASE")


def test_disclosure_blocked_before_verification(repo) -> None:  # type: ignore[no-untyped-def]
    _, ctx = ctx_for(repo)
    fact = Fact(id="CL-2048:status", cls="claim.status", text="x", owner="P9", case_id="CL-2048")
    assert ctx.disclose(fact) == []
    assert ctx.events[-1].kind == "disclosure_blocked"
    ctx.say("facts", fact)
    assert ctx.acts == []  # unauthorized facts never reach the plan


def test_disclosure_blocked_for_other_owner(repo) -> None:  # type: ignore[no-untyped-def]
    _, ctx = ctx_for(repo, phase="PROCESS_CASE", verified=True, party_id="P9", case_id="CL-2048")
    other = Fact(id="CL-3001:status", cls="claim.status", text="x", owner="P12", case_id="CL-3001")
    mine = Fact(id="CL-2048:status", cls="claim.status", text="y", owner="P9", case_id="CL-2048")
    assert ctx.disclose(other, mine) == [mine]
    assert ctx.blocked[0][1] == "fact owned by another party"


def test_phase_class_rules(repo) -> None:  # type: ignore[no-untyped-def]
    _, ctx = ctx_for(repo, phase="RESOLVE_INTENT", verified=True, party_id="P9")
    fact = Fact(id="CL-2048:status", cls="claim.status", text="x", owner="P9", case_id="CL-2048")
    assert ctx.disclose(fact) == []  # claim facts are not disclosable before a claim is selected


def test_email_requires_consent_and_registered_recipient(repo) -> None:  # type: ignore[no-untyped-def]
    _, ctx = ctx_for(repo, phase="POST_PROCESS", verified=True, party_id="P9", case_id="CL-2048")
    good = ActionRequest("email_summary", "margaret@email.com", "s", "b")
    with pytest.raises(PolicyViolation, match="consent"):
        ctx.perform(good)
    ctx.record_consent("email_summary", "granted")
    with pytest.raises(PolicyViolation, match="recipient_is_registered"):
        ctx.perform(ActionRequest("email_summary", "attacker@evil.example", "s", "b"))
    ctx.perform(good)
    with pytest.raises(PolicyViolation, match="already performed"):
        ctx.perform(good)


def test_email_not_allowed_outside_post_process(repo) -> None:  # type: ignore[no-untyped-def]
    _, ctx = ctx_for(repo, phase="PROCESS_CASE", verified=True, party_id="P9", case_id="CL-2048")
    ctx.record_consent("email_summary", "granted")
    with pytest.raises(PolicyViolation, match="not allowed"):
        ctx.perform(ActionRequest("email_summary", "margaret@email.com", "s", "b"))
