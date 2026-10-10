"""Submission regressions for contextual identity and partial-verification scope."""
import pytest

from app.conversation import ConversationEngine
from test_lean_requirements import service

TUPLE = 'Margaret Chen, 1985-03-15, 4472.'
DISCLAIMER = TUPLE + ' These are my neighbor’s details, not mine.'
UNRELATED = [
    'Write a recipe for chocolate cake.',
    'Explain reinforcement learning to me.',
    'Compose a jazz solo for me.',
]


class SafetyProvider:
    """An extractor error must still encounter the independent attribution gate."""
    def __init__(self):
        self.calls = []

    def complete(self, name, schema, prompt, context, timeout):
        self.calls.append(name)
        if name == 'Identity':
            return dict(subject='caller', **{
                field: value for field, value in
                [('name', 'Margaret Chen'), ('dob', '1985-03-15'), ('id_last4', '4472')]
                if value in context['message']})
        if name == 'IdentityAssertion':
            return {'asserted_by_caller': False}
        if name == 'BirthDateUse':
            return {'is_caller_birth_date': True}
        if name == 'Topics':
            return {'topics': []}
        return {
            'control': {'scope': 'unrelated' if context['message'] in UNRELATED else 'insurance',
                        'action': 'continue', 'claim_action': 'none',
                        'email_consent': 'none', 'needs_clarification': False},
            'content': {'questions': [], 'hint': {}, 'document_updates': [], 'topics': []},
            'emotion': 'neutral', 'wants_human': False,
        }


def assert_no_disclosure(reply, state):
    assert not state.verified and state.phase == 'VERIFY_ID'
    assert state.case_id is None
    assert 'pathology' not in reply.lower() and 'Hi Margaret' not in reply


def test_tuple_with_context_cannot_skip_attribution_rejection():
    provider = SafetyProvider()
    reply, state = service(ConversationEngine(provider=provider)).respond('s', DISCLAIMER)
    assert 'IdentityAssertion' in provider.calls
    assert state.response_mode == 'model' and state.identity == {}
    assert_no_disclosure(reply, state)


def check_partial_identity_escalation(agent):
    reply, state = agent.respond('s', 'Margaret Chen')
    assert state.response_mode == 'model' and state.identity == {'name': 'Margaret Chen'}
    assert_no_disclosure(reply, state)
    for attempt, message in enumerate(UNRELATED, 1):
        reply, state = agent.respond('s', message)
        assert state.response_mode == 'model' and state.irrelevant == attempt
        assert state.identity == {'name': 'Margaret Chen'}
        assert 'only help with insurance claims' in reply
        assert_no_disclosure(reply, state)
    assert state.handoff == 'offered' and 'human representative' in reply


def test_partial_identity_does_not_disable_scope_escalation():
    check_partial_identity_escalation(service(ConversationEngine(provider=SafetyProvider())))


@pytest.mark.live
def test_live_tuple_with_third_party_disclaimer_never_verifies():
    reply, state = service(ConversationEngine()).respond('s', DISCLAIMER)
    assert state.response_mode == 'model'
    assert_no_disclosure(reply, state)


@pytest.mark.live
def test_live_labeled_identity_with_claim_context_verifies():
    reply, state = service(ConversationEngine()).respond(
        's', "I'm the policyholder. My name is Margaret Chen, policy POL-9921. "
             "I'm calling about my denied healthcare claim from January. "
             "DOB is 1985-03-15, SSN last four is 4472.")
    assert state.response_mode == 'model' and state.verified
    assert state.phase == 'PROCESS_CASE' and state.case_id == 'CL-2048'
    assert reply.startswith('Hi Margaret,') and 'pathology' in reply.lower()
