"""Contract tests for model-assisted turns using controlled provider outputs.

The fake provider isolates orchestration behavior: structured interpretation is
allowed to suggest meaning, while repository ownership, state transitions, and
grounded responses remain deterministic.
"""
from app import workflow
from app.conversation import ConversationEngine
from test_lean_requirements import SAMPLE, service

class Provider:
    def __init__(self): self.calls = []
    def complete(self, name, schema, prompt, context, timeout):
        if name == 'IdentityAssertion': return {'asserted_by_caller': True}
        if name == 'BirthDateUse': return {'is_caller_birth_date': True}
        if name == 'Identity':
            return dict(subject='caller', **{field: value for field, value in
                    [('name', 'Margaret Chen'), ('dob', '1985-03-15'), ('id_last4', '4472'), ('policy_number', 'POL-9921')]
                    if value in context['message']})
        self.calls.append((name, context, schema, timeout))
        if context['phase'] == 'VERIFY_ID':
            return {'scope': 'insurance', 'questions': ['Why was my claim denied?'], 'emotion': 'neutral', 'intensity': 0, 'refusal': False, 'wants_human': False, 'wants_finish': False, 'hint': {'case_type': 'healthcare', 'month': 1, 'status': 'denied'}, 'fact_ids': [], 'follow_up': 'none', 'needs_clarification': False}
        return {'scope': 'insurance', 'questions': ['Why was my claim denied?'], 'emotion': 'neutral', 'intensity': 0, 'refusal': False, 'wants_human': False, 'wants_finish': False, 'hint': {}, 'fact_ids': ['claim.denial_reason'], 'follow_up': 'none', 'needs_clarification': False}

def test_model_path_uses_one_typed_turn_and_redacts_identity():
    provider = Provider(); agent = service(ConversationEngine(provider=provider))
    answer, state = agent.respond('model', SAMPLE)
    assert state.verified and state.case_id == 'CL-2048' and state.response_mode == 'model'
    assert 'pathology' in answer.lower()
    assert 'Margaret Chen' not in str(provider.calls[0][1]) and '4472' not in str(provider.calls[0][1])
    assert provider.calls[0][0] == 'Turn'

def test_model_output_cannot_add_unknown_fact():
    provider = Provider(); original = provider.complete
    def bad(name, schema, prompt, context, timeout):
        result = original(name, schema, prompt, context, timeout)
        if context.get('phase') == 'PROCESS_CASE': result['fact_ids'] = ['other_customer.secret']
        return result
    provider.complete = bad; agent = service(ConversationEngine(provider=provider)); agent.respond('model', SAMPLE)
    answer, state = agent.respond('model', 'Why was it denied?')
    assert state.response_mode == 'model' and 'secret' not in answer and 'available record does not answer' in answer.lower()

def test_model_path_uses_structured_output_without_keyword_routing(monkeypatch):
    provider = Provider()
    monkeypatch.setattr(workflow, 'intent', lambda text: (_ for _ in ()).throw(AssertionError('keyword routing called')))
    agent = service(ConversationEngine(provider=provider))
    answer, state = agent.respond('model', SAMPLE)
    assert state.case_id == 'CL-2048'
    answer, state = agent.respond('model', 'How is my claim doing?')
    assert 'pathology' in answer.lower() and state.response_mode == 'model'

def test_model_updates_structured_memory_and_failure_recovers_safely():
    provider = Provider(); original = provider.complete
    def memory(name, schema, prompt, context, timeout):
        result = original(name, schema, prompt, context, timeout)
        if name == 'Turn' and context['phase'] == 'VERIFY_ID':
            result['memory_updates'] = [{'kind':'reference', 'key':'caller_hint', 'value':'January healthcare denial'}]
            result['document_updates'] = [{'document':'pathology report', 'status':'missing'}]
        return result
    provider.complete = memory; agent = service(ConversationEngine(provider=provider)); agent.respond('model', SAMPLE)
    state = agent.store.get('model')
    assert state.document_memory['pathology report'].status == 'missing'
    assert state.caller_notes == ['reference:caller_hint=January healthcare denial']

    class FailAfter(Provider):
        def __init__(self): super().__init__(); self.calls = 0
        def complete(self, *args):
            self.calls += 1
            if self.calls > 1: raise RuntimeError('provider unavailable')
            return super().complete(*args)
    failing = FailAfter(); safe_agent = service(ConversationEngine(provider=failing)); safe_agent.respond('model', SAMPLE)
    answer, state = safe_agent.respond('model', 'How is my claim doing?')
    assert state.response_mode == 'fallback' and 'trouble understanding' in answer.lower()
