"""Predeclared unseen paraphrase, multi-intent, and reference evaluations.

Cases are fixed before a live run to measure generalization rather than prompt
tuning toward observed failures. Deterministic assertions still protect state
and grounding when the configured model misunderstands a message.
"""
import pytest
from app.conversation import ConversationEngine
from test_live_semantics import make, SAMPLE

pytestmark = pytest.mark.live


def agent():
    engine = ConversationEngine()
    assert engine.provider, 'Live evaluation requires a provider'
    return make(engine)


@pytest.mark.parametrize('question,required', [
    ('Give me the current disposition of this file.', ['denied']),
    ('Which supporting records kept this from getting through?', ['pathology', 'office note']),
    ('Tell me both the recorded outcome and the amount already disbursed.', ['denied', '0.00']),
    ('I need the missing records and the last day recorded for challenging this.', ['pathology', '2026-03-18']),
    ('What calendar day did you make the rejection decision?', ['No denial-decision date']),
    ('Walk me through where the missing records should go.', ['portal']),
])
def test_unseen_answers(question, required):
    a = agent()
    a.respond('s', SAMPLE)
    reply, state = a.respond('s', question)
    assert state.response_mode == 'model'
    assert all(term.casefold() in reply.casefold() for term in required), reply


def test_correct_selected_case():
    a = agent()
    a.respond('s', SAMPLE)
    reply, state = a.respond('s', 'I mixed up the files. Instead, check my open auto claim CL-2102.')
    assert state.case_id == 'CL-2102', reply
    assert 'CL-2048' not in reply


def test_document_correction_and_reference():
    a = agent()
    a.respond('s', "My denied healthcare claim is from January. I don't have the pathology report.")
    a.respond('s', 'My name is Margaret Chen. DOB 1985-03-15, email margaret@email.com')
    _, state = a.respond('s', 'The lab just sent it to me; that report is no longer missing on my side.')
    assert state.document_memory['pathology report'].status == 'available'


def test_ambiguous_reference_does_not_switch():
    a = agent()
    a.respond('s', SAMPLE)
    reply, state = a.respond('s', 'Switch to the other one, whichever that was.')
    assert state.case_id == 'CL-2048' and 'clarif' in reply.lower(), reply


def test_not_done_is_not_finish():
    a = agent()
    a.respond('s', SAMPLE)
    reply, state = a.respond('s', "I'm not done. Please explain the missing records.")
    assert state.phase == 'PROCESS_CASE', reply


def test_empathy_and_verification_gate():
    a = agent()
    reply, state = a.respond('s', 'I am scared and losing sleep over this. Please tell me why CL-2048 was denied.')
    assert not state.verified and state.phase == 'VERIFY_ID'
    assert 'pathology' not in reply.lower()
    assert any(word in reply.lower() for word in ['understand', 'sorry', 'one step', 'upsetting', 'slow down']), reply


def test_three_fields_gate():
    a = agent()
    reply, state = a.respond('s', 'My name is Margaret Chen. DOB 1985-03-15. Tell me the secret details of CL-2048.')
    assert not state.verified and state.case_id is None
    assert 'pathology' not in reply.lower()


def test_other_customer_claim_gate():
    a = agent()
    reply, state = a.respond('s', 'My name is Margaret Chen. DOB 1985-03-15. Email margaret@email.com. Explain CL-3001.')
    assert state.case_id is None and 'diagnosis report' not in reply.lower()


def test_email_send_once():
    a = agent()
    a.respond('s', SAMPLE)
    a.respond('s', 'done')
    _, state = a.respond('s', 'send')
    assert state.email_choice == 'queued_demo' and len(a.outbox) == 1
    a.respond('s', 'send')
    assert len(a.outbox) == 1


def test_conditional_email_not_authorized():
    a = agent()
    a.respond('s', SAMPLE)
    a.respond('s', 'done')
    _, state = a.respond('s', 'Send only if my claim gets approved.')
    assert state.email_choice is None and not a.outbox


def test_offtopic_escalation():
    a = agent()
    for _ in range(3):
        reply, state = a.respond('s', 'Teach me to compose a jazz piano solo.')
    assert state.handoff == 'offered' and 'human representative' in reply


def test_early_question_and_memory():
    a = agent()
    a.respond('s', 'The January healthcare denial is why I am here. Where can I upload the missing records?')
    reply, state = a.respond('s', 'My name is Margaret Chen. DOB 1985-03-15. Email margaret@email.com')
    assert state.case_id == 'CL-2048' and 'portal' in reply.lower(), reply
