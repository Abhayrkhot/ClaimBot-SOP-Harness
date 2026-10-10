from datetime import date

from app.conversation import ConversationEngine
from app.repository import Repository
from app.store import Store
from app.workflow import ConversationService

SAMPLE = ("I'm the policyholder. My name is Margaret Chen, policy POL-9921. "
          "I'm calling about my denied healthcare claim from January. "
          "DOB is 1985-03-15, SSN last four is 4472.")


class Provider:
    def __init__(self, fail=False): self.calls = 0; self.fail = fail

    def complete(self, name, schema, prompt, context, timeout):
        self.calls += 1
        if self.fail: raise RuntimeError('provider unavailable')
        if name == 'IdentityAssertion': return {'asserted_by_caller': True}
        if name == 'BirthDateUse': return {'is_caller_birth_date': True}
        if name == 'Identity':
            return dict(subject='caller', **{field: value for field, value in
                    [('name', 'Margaret Chen'), ('dob', '1985-03-15'), ('id_last4', '4472'), ('policy_number', 'POL-9921')]
                    if value in context['message']})
        if context['phase'] == 'VERIFY_ID':
            return {'scope': 'insurance', 'questions': [], 'emotion': 'neutral', 'intensity': 0,
                    'refusal': False, 'wants_human': False, 'wants_finish': False,
                    'action': 'continue', 'claim_action': 'select', 'email_consent': 'none',
                    'hint': {'case_type': 'healthcare', 'month': 1, 'status': 'denied'},
                    'fact_ids': [], 'follow_up': 'none', 'needs_clarification': False}
        switching = 'CL-2102' in context['message']
        return {'scope': 'insurance', 'questions': ['the current question'], 'emotion': 'neutral', 'intensity': 0,
                'refusal': False, 'wants_human': False, 'wants_finish': False,
                'action': 'continue', 'claim_action': 'switch' if switching else 'none',
                'email_consent': 'none', 'hint': {'case_id': 'CL-2102'} if switching else {},
                'fact_ids': ['claim.status'], 'follow_up': 'none', 'needs_clarification': True}


def service(provider):
    return ConversationService(Repository(), Store(), today=lambda: date(2026, 10, 9),
                               conversation=ConversationEngine(provider=provider))


def test_opening_hint_survives_model_verification():
    agent = service(Provider())
    reply, state = agent.respond('s', SAMPLE)
    assert state.case_id == 'CL-2048' and state.phase == 'PROCESS_CASE'
    assert state.hint.year is None
    assert 'CL-3001' not in reply


def test_facts_answer_even_when_model_asks_for_clarification():
    agent = service(Provider())
    agent.respond('s', SAMPLE)
    reply, state = agent.respond('s', 'How is the claim doing?')
    assert state.phase == 'PROCESS_CASE' and 'recorded as denied' in reply


def test_explicit_owned_claim_switch_clears_stale_claim_context():
    agent = service(Provider())
    agent.respond('s', SAMPLE)
    reply, state = agent.respond('s', 'Actually, check my open auto claim CL-2102.')
    assert state.case_id == 'CL-2102' and state.phase == 'PROCESS_CASE'
    assert 'CL-2048' not in reply


def test_merely_mentioning_another_claim_does_not_switch():
    class ReferenceProvider(Provider):
        def complete(self, *args):
            result = super().complete(*args)
            if args[3].get('phase') == 'PROCESS_CASE' and 'CL-2102' in args[3]['message']:
                result['claim_action'] = 'reference'
            return result
    agent = service(ReferenceProvider())
    agent.respond('s', SAMPLE)
    _, state = agent.respond('s', 'Does CL-2102 have the same filing date?')
    assert state.case_id == 'CL-2048'


def test_model_failure_restores_workflow_state():
    agent = ConversationService(Repository(), Store(), today=lambda: date(2026, 10, 9), conversation=None)
    agent.respond('s', SAMPLE)
    before = agent.store.get('s')
    agent.conversation = ConversationEngine(provider=Provider(fail=True))
    reply, state = agent.respond('s', 'How is my claim doing?')
    assert state.response_mode == 'fallback' and 'nothing was changed' in reply.lower()
    assert state.case_id == before.case_id and state.phase == before.phase and state.hint == before.hint


def test_negated_completion_does_not_finish():
    agent = service(Provider())
    agent.respond('s', SAMPLE)
    reply, state = agent.respond('s', "I'm not done. Explain the claim status.")
    assert state.phase == 'PROCESS_CASE' and 'recorded as denied' in reply


def test_semantic_finish_and_natural_email_consent():
    class ActionProvider(Provider):
        def complete(self, *args):
            result = super().complete(*args)
            message = args[3]['message'].casefold()
            if 'answers my questions' in message: result['action'] = 'finish'; result['fact_ids'] = []
            if 'email the summary' in message: result['email_consent'] = 'send'; result['fact_ids'] = []
            return result
    agent = service(ActionProvider())
    agent.respond('s', SAMPLE)
    _, state = agent.respond('s', 'That answers my questions; I do not need anything else today.')
    assert state.phase == 'POST_PROCESS' and state.email_offer_pending
    _, state = agent.respond('s', 'send')
    assert state.phase == 'COMPLETE' and state.email_choice == 'queued_demo' and len(agent.outbox) == 1


def test_model_scope_drives_deterministic_escalation_threshold():
    class ScopeProvider(Provider):
        def complete(self, *args):
            result = super().complete(*args)
            if args[0] == 'Turn': result['scope'] = 'unrelated'
            return result
    agent = service(ScopeProvider())
    for _ in range(3): reply, state = agent.respond('s', 'Compose a jazz solo for me.')
    assert state.handoff == 'offered' and 'human representative' in reply
