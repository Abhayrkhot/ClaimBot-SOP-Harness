"""End-to-end SOP flows through the engine with the offline rules interpreter."""

from __future__ import annotations

import pytest

from claimbot.claims.models import ClaimReference, DocumentUpdate, Interpretation

from .conftest import QueueInterpreter

OPENING = (
    "My name is Margaret Chen. I'm calling about my denied healthcare claim from January. "
    "I don't have the pathology report."
)


async def verified_margaret(chat_factory):  # type: ignore[no-untyped-def]
    c = chat_factory()
    await c.say(OPENING)
    await c.say("DOB is 1985-03-15, SSN last four is 4472.")
    return c


async def test_full_happy_path_with_email(chat, store):  # type: ignore[no-untyped-def]
    c = chat()
    r = await c.say(OPENING)
    assert r.session.phase == "VERIFY_ID"
    assert "verify your identity" in r.reply and "2 more of the following" in r.reply
    assert "CL-2048" not in r.reply and "pathology" not in r.reply.casefold()
    assert r.session.evidence == {"name": ["P9"]}

    r = await c.say("DOB is 1985-03-15, SSN last four is 4472.")
    assert r.session.verified and r.session.party_id == "P9"
    assert r.session.phase == "PROCESS_CASE" and r.session.case_id == "CL-2048"
    assert r.reply.startswith("Thanks, Margaret, you're verified.")
    assert "denied because" in r.reply
    assert "replacement copy" in r.reply  # remembered missing pathology report -> alternatives
    assert r.session.evidence == {}  # no identity evidence retained after verification

    r = await c.say("What documents do I need, and where do I send them?")
    assert "member portal" in r.reply

    r = await c.say("When was the denial decision made?")
    assert "doesn't include the date the denial decision was made" in r.reply
    assert "January 12, 2026" not in r.reply

    r = await c.say("That's all, thank you.")
    assert r.session.phase == "POST_PROCESS"
    assert "m*****@email.com" in r.reply and "send or skip" in r.reply

    r = await c.say("send")
    assert r.session.phase == "COMPLETE"
    assert "sent the summary to m*****@email.com" in r.reply
    assert r.session.consents["email_summary"] == "granted"
    outbox = await store.outbox_for(c.sid)
    assert len(outbox) == 1 and outbox[0].recipient == "margaret@email.com"
    assert "CL-2048" in outbox[0].body and "Next steps" in outbox[0].body

    r = await c.say("send")
    assert "complete" in r.reply.casefold()
    assert len(await store.outbox_for(c.sid)) == 1


async def test_skip_queues_nothing(chat, store):  # type: ignore[no-untyped-def]
    c = await verified_margaret(chat)
    await c.say("That's all.")
    r = await c.say("skip")
    assert r.session.phase == "COMPLETE" and r.session.consents["email_summary"] == "declined"
    assert await store.outbox_for(c.sid) == []


async def test_insufficient_identity_keeps_claims_locked(chat):  # type: ignore[no-untyped-def]
    c = chat()
    r = await c.say("My name is Margaret Chen and my DOB is 1985-03-15. Why was my claim CL-2048 denied?")
    assert r.session.phase == "VERIFY_ID" and not r.session.verified
    assert "1 of the following" not in r.reply  # wording uses "1 more" after the first prompt
    assert "denied because" not in r.reply
    assert not c.events("fact_disclosed")


async def test_wrong_details_fail_then_lock(chat):  # type: ignore[no-untyped-def]
    c = chat()
    await c.say("My name is Margaret Chen, DOB is 1985-03-15, SSN last four is 1111.")
    assert c.last.session.identity_failures == 1 and "couldn't verify" in c.last.reply
    await c.say("SSN last four is 2222")
    await c.say("SSN last four is 3333")
    assert c.last.session.phase == "LOCKED"
    r = await c.say("SSN last four is 4472")
    assert r.session.phase == "LOCKED" and not r.session.verified


async def test_other_customers_claim_is_never_disclosed(chat):  # type: ignore[no-untyped-def]
    c = await verified_margaret(chat)
    r = await c.say("Actually, what is the status of claim CL-3001?")
    assert "CL-3001" not in r.reply
    assert "couldn't find a claim" in r.reply
    disclosed = [e.data for e in c.events("fact_disclosed")]
    assert all(d["owner"] == "P9" for d in disclosed)
    assert r.session.case_id == "CL-2048"


async def test_switch_between_owned_claims(chat):  # type: ignore[no-untyped-def]
    c = await verified_margaret(chat)
    r = await c.say("Instead, check my open auto claim CL-2102.")
    assert r.session.case_id == "CL-2102"
    assert "auto claim CL-2102" in r.reply
    assert r.session.documents == {}


async def test_ambiguous_switch_asks_for_clarification(chat):  # type: ignore[no-untyped-def]
    c = await verified_margaret(chat)
    r = await c.say("Switch to the other claim.")
    assert r.session.case_id == "CL-2048"
    assert "Which one would you like to discuss?" in r.reply


async def test_multiple_matches_list_owned_claims(chat):  # type: ignore[no-untyped-def]
    c = chat()
    await c.say("My name is Margaret Chen. I have a question about my healthcare claim.")
    r = await c.say("DOB is 1985-03-15, SSN last four is 4472.")
    assert r.session.phase == "RESOLVE_INTENT"
    assert "CL-2048" in r.reply and "CL-2011" in r.reply and "CL-1899" not in r.reply
    r = await c.say("The one from January 2025, CL-2011.")
    assert r.session.case_id == "CL-2011" and r.session.phase == "PROCESS_CASE"


async def test_off_topic_offers_human_after_three(chat):  # type: ignore[no-untyped-def]
    c = chat()
    for _ in range(2):
        r = await c.say("What is reinforcement learning?")
        assert "only help with insurance claims" in r.reply and r.session.handoff is None
    r = await c.say("Tell me about reinforcement learning")
    assert r.session.handoff == "offered" and "human representative" in r.reply
    r = await c.say("yes")
    assert r.session.phase == "HANDOFF"


async def test_human_decline_returns_to_flow(chat):  # type: ignore[no-untyped-def]
    c = chat()
    await c.say("I want to talk to a human")
    r = await c.say("no")
    assert r.session.phase == "VERIFY_ID" and r.session.handoff is None
    assert "Okay, let's continue." in r.reply and "verify your identity" in r.reply


async def test_end_session_closes(chat, make_engine):  # type: ignore[no-untyped-def]
    c = chat()
    await c.say("hello")
    assert await c.engine.end(c.sid)
    r = await c.say("hello again")
    assert r.session.phase == "CLOSED" and "ended" in r.reply


async def test_email_goes_only_to_registered_address(chat, store):  # type: ignore[no-untyped-def]
    interp = QueueInterpreter()
    c = chat(interp)
    from claimbot.claims.identity import IdentityReading

    interp.push(
        Interpretation(
            scope="claims",
            topics=["overview"],
            claim=ClaimReference(action="select", case_type="healthcare", status="denied"),
            identity=IdentityReading(
                subject="caller",
                name="Margaret Chen",
                birth_date_quote="1985-03-15",
                id_last4="4472",
                birth_date_is_callers=True,
            ),
        )
    )
    await c.say("Margaret Chen 1985-03-15 4472, my denied healthcare claim")
    assert c.last.session.case_id == "CL-2048"
    interp.push(Interpretation(scope="claims", act="finish"))
    await c.say("that's everything")
    interp.push(Interpretation(scope="claims", email_decision="send"))
    r = await c.say("Yes, but send it to attacker@evil.example instead")
    assert (
        await store.outbox_for(c.sid) == []
    )  # never redirected, and not sent without agreeing to the address on file
    assert (
        "only send the summary to the email address on file" in r.reply and r.session.phase == "POST_PROCESS"
    )
    interp.push(Interpretation(scope="claims", email_decision="send"))
    r = await c.say("send")
    outbox = await store.outbox_for(c.sid)
    assert [m.recipient for m in outbox] == ["margaret@email.com"] and r.session.phase == "COMPLETE"


async def test_negated_send_asks_for_confirmation(chat, store):  # type: ignore[no-untyped-def]
    interp = QueueInterpreter()
    c = chat(interp)
    c2 = await verified_margaret(chat)
    await c2.say("That's all.")
    c.sid = c2.sid
    interp.push(Interpretation(scope="claims", email_decision="send"))
    r = await c.say("please don't send it yet")
    assert r.session.phase == "POST_PROCESS" and "Just to confirm" in r.reply
    assert await store.outbox_for(c.sid) == []


async def test_interpretation_failure_changes_nothing(chat):  # type: ignore[no-untyped-def]
    from claimbot.sop.engine import InterpretationError

    def boom(session, text):  # type: ignore[no-untyped-def]
        raise InterpretationError("interpret", TimeoutError())

    c = await verified_margaret(chat)
    before = c.last.session.model_copy(deep=True)
    c.engine.interpreter = QueueInterpreter([boom])
    r = await c.say("what is my deadline")
    assert r.session.response_mode == "fallback" and "nothing has changed" in r.reply
    assert r.session.phase == before.phase and r.session.case_id == before.case_id
    assert r.session.turn == before.turn + 1


async def test_idempotent_retry_returns_same_reply(make_engine):  # type: ignore[no-untyped-def]
    engine = make_engine()
    first = await engine.handle(None, "hello")
    a = await engine.handle(first.session_id, "My name is Margaret Chen", request_id="r1")
    b = await engine.handle(first.session_id, "My name is Margaret Chen", request_id="r1")
    assert b.replayed and b.reply == a.reply and b.revision == a.revision


@pytest.mark.parametrize(
    "question,expected",
    [
        ("What is the status?", "currently denied"),
        ("When is the appeal deadline?", "March 18, 2026"),
        ("How much has been paid?", "$0.00"),
        ("When was it filed?", "January 12, 2026"),
        ("Was a notice sent?", "no notification history"),
    ],
)
async def test_grounded_answers(chat, question, expected):  # type: ignore[no-untyped-def]
    c = await verified_margaret(chat)
    r = await c.say(question)
    assert expected.casefold() in r.reply.casefold(), r.reply


async def test_passed_deadline_is_flagged(chat):  # type: ignore[no-untyped-def]
    c = await verified_margaret(chat)
    r = await c.say("When is the appeal deadline?")
    assert "has passed" in r.reply


async def test_closing_phrase_finishes_even_with_spurious_topics(chat):  # type: ignore[no-untyped-def]
    interp = QueueInterpreter()
    c = await verified_margaret(chat)
    c.engine.interpreter = interp
    interp.push(Interpretation(scope="claims", act="ask_or_inform", topics=["status"]))
    r = await c.say("Thank you for the information. That is all I needed.")
    assert r.session.phase == "POST_PROCESS" and "send or skip" in r.reply


async def test_email_mention_offers_but_never_sends(chat, store):  # type: ignore[no-untyped-def]
    interp = QueueInterpreter()
    c = await verified_margaret(chat)
    c.engine.interpreter = interp
    interp.push(Interpretation(scope="claims", topics=["status"], email_decision="send"))
    r = await c.say("Please email me a summary of all this.")
    assert r.session.phase == "POST_PROCESS" and "send or skip" in r.reply
    assert await store.outbox_for(c.sid) == []  # consent only comes from an answer to the offer


@pytest.mark.parametrize(
    "message",
    [
        "I cannot get the full office note now. Can I send a visit summary or other record instead?",
        "Got it, thanks! Give me the fax number to send the documents. And skip the email summary, please.",
        "Also, if you can send an email summary that would be nice.",
    ],
)
async def test_eval_found_consent_regressions(chat, store, message):  # type: ignore[no-untyped-def]
    """Messages from the first full run that the email shortcut wrongly took as consent."""
    interp = QueueInterpreter()
    c = await verified_margaret(chat)
    c.engine.interpreter = interp
    interp.push(Interpretation(scope="claims", topics=["submission"], email_decision="send"))
    await c.say(message)
    assert await store.outbox_for(c.sid) == []


async def test_declining_summary_up_front_is_respected(chat, store):  # type: ignore[no-untyped-def]
    interp = QueueInterpreter()
    c = await verified_margaret(chat)
    c.engine.interpreter = interp
    interp.push(Interpretation(scope="claims", act="finish", email_decision="send"))
    r = await c.say("That's everything, and skip the email summary please.")
    assert r.session.phase == "COMPLETE" and r.session.consents["email_summary"] == "declined"
    assert await store.outbox_for(c.sid) == []


async def test_spurious_select_does_not_switch_or_list(chat):  # type: ignore[no-untyped-def]
    interp = QueueInterpreter()
    c = await verified_margaret(chat)
    c.engine.interpreter = interp
    interp.push(Interpretation(scope="claims", topics=["documents"],
                               claim=ClaimReference(action="select", status="denied", year=2025)))  # fmt: skip
    r = await c.say("Can you tell me which documents I still need to send?")
    assert r.session.case_id == "CL-2048" and "couldn't find" not in r.reply
    assert "pathology report" in r.reply


async def test_unstated_year_is_ignored_when_resolving(chat):  # type: ignore[no-untyped-def]
    interp = QueueInterpreter()
    c = chat(interp)
    from claimbot.claims.identity import IdentityReading

    interp.push(
        Interpretation(
            scope="claims",
            topics=["payment"],
            claim=ClaimReference(action="select", case_type="dental", status="closed", year=2026),
        )
    )
    await c.say("How much was paid on my closed dental claim?")
    interp.push(Interpretation(scope="claims", identity=IdentityReading(subject="none")))
    r = await c.say("Margaret Chen, 1985-03-15, 4472")
    assert r.session.case_id == "CL-1899", (
        r.reply
    )  # bare list filled deterministically; year 2026 never stated


async def test_question_about_a_document_is_not_a_possession_report(chat):  # type: ignore[no-untyped-def]
    interp = QueueInterpreter()
    c = await verified_margaret(chat)
    c.engine.interpreter = interp
    interp.push(Interpretation(scope="claims", topics=["documents"],
                               documents=[DocumentUpdate(document="office note", status="missing")]))  # fmt: skip
    r = await c.say("Do I still need to send the office note?")
    assert "office note" not in r.session.documents
    assert "don't have the office note" not in r.reply


async def test_misread_decline_in_offer_phase_is_confirmed_not_sent(chat, store):  # type: ignore[no-untyped-def]
    interp = QueueInterpreter()
    c = await verified_margaret(chat)
    await c.say("That's all.")
    c.engine.interpreter = interp
    interp.push(Interpretation(scope="claims", email_decision="send"))
    r = await c.say("skip the email summary please")
    assert await store.outbox_for(c.sid) == [] and r.session.phase == "POST_PROCESS"
