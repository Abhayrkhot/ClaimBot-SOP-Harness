"""Check relevance as well as required facts; use the production nested contract."""
import pytest

from app.conversation import ConversationEngine
from test_lean_requirements import service

OPENING = ('My name is Margaret Chen. DOB is 1985-03-15, and my SSN last four '
           'is 4472. I’m calling about my denied healthcare claim from January.')


class Provider:
    def __init__(self, fail_topics=False):
        self.fail_topics = fail_topics

    def complete(self, name, schema, prompt, context, timeout):
        if name == 'IdentityAssertion': return {'asserted_by_caller': True}
        if name == 'BirthDateUse': return {'is_caller_birth_date': True}
        if name == 'Identity':
            return dict(subject='caller', **{field: value for field, value in
                    [('name', 'Margaret Chen'), ('dob', '1985-03-15'), ('id_last4', '4472')]
                    if value in context['message']})
        if name == 'Topics':
            if self.fail_topics:
                raise ValueError('invalid semantic response')
            return {'topics': ['denial_reason']}
        return {'control': {'scope': 'insurance', 'action': 'continue',
                            'claim_action': 'select', 'email_consent': 'none',
                            'needs_clarification': False},
                'content': {'hint': {'case_type': 'healthcare', 'month': 1, 'status': 'denied'},
                            'topics': ['denial_reason', 'documents', 'notification',
                                       'decision_date', 'net_payment', 'expected_reimbursement']},
                'emotion': 'neutral', 'wants_human': False}


def assert_focused_opening(reply, state):
    assert state.verified and state.case_id == 'CL-2048'
    assert reply.startswith('Hi Margaret,')
    assert 'pathology' in reply and 'office note' in reply
    assert reply.count('pathology') == 1
    assert not any(term in reply.lower() for term in
                   ('notification', 'decision date', 'decision-date', 'denial-decision',
                    'payment', 'reimbursement', 'filed on', '2026-01-12', 'deadline'))
    assert len(reply.split()) <= 65


def test_opening_uses_focused_topics_before_storing_memory():
    agent = service(ConversationEngine(provider=Provider()))
    reply, state = agent.respond('s', OPENING)
    assert_focused_opening(reply, state)


def test_topic_failure_preserves_verification_gate():
    agent = service(ConversationEngine(provider=Provider(fail_topics=True)))
    reply, state = agent.respond('s', OPENING)
    assert not state.verified and state.phase == 'VERIFY_ID'
    assert 'Hi Margaret' not in reply and 'pathology' not in reply
    assert state.response_mode == 'fallback'


def test_do_not_repeat_document_list_already_in_denial_reason():
    from app.models import Turn
    agent = service()
    _, state = agent.respond('s', OPENING)
    plan = Turn(scope='insurance', topics=['denial_reason', 'documents', 'submission'])
    reply = agent._answer(state, None, '', plan)
    assert 'The listed missing documents are' not in reply
    assert 'pathology' in reply and 'portal' in reply.lower()


@pytest.mark.live
def test_live_exact_customer_opening_is_precise():
    agent = service(ConversationEngine())
    reply, state = agent.respond('s', OPENING)
    assert state.response_mode == 'model'
    assert_focused_opening(reply, state)


@pytest.mark.live
@pytest.mark.parametrize('question,required,forbidden', [
    ('Could you explain the reason for turning this down?', ['pathology', 'office note'],
     ['notification', 'net payment', 'reimbursement', '2026-01-12']),
    ('Tell me the current status and what was paid.', ['denied', '0.00'],
     ['pathology', 'notification', '2026-01-12']),
    ('Where do I send the records?', ['portal'],
     ['notification', 'net payment', 'reimbursement']),
])
def test_live_followup_answers_only_requested_facts(question, required, forbidden):
    agent = service(ConversationEngine())
    agent.respond('s', OPENING)
    reply, state = agent.respond('s', question)
    assert state.response_mode == 'model'
    assert all(term in reply.lower() for term in required), reply
    assert not any(term in reply.lower() for term in forbidden), reply
