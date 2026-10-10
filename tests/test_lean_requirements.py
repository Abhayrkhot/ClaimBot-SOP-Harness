from datetime import date
from fastapi.testclient import TestClient
from pydantic import ValidationError
import pytest
from app.conversation import strict_schema
from app.main import app
from app.models import Phase, Turn
from app.repository import Repository
from app.store import Store
from app.workflow import ConversationService

SAMPLE = ("I'm the policyholder. My name is Margaret Chen, policy POL-9921. "
          "I'm calling about my denied healthcare claim from January. "
          "DOB is 1985-03-15, SSN last four is 4472.")

def service(model=None): return ConversationService(Repository(), Store(), today=lambda: date(2026, 10, 9), conversation=model)
def talk(agent, *messages):
    result = None
    for message in messages: result = agent.respond('test', message)
    return result

def test_assignment_sample_verifies_and_resolves():
    answer, state = service().respond('test', SAMPLE)
    assert state.verified and state.case_id == 'CL-2048' and state.phase == Phase.PROCESS_CASE
    assert 'pathology' in answer.lower() and 'CL-3001' not in answer

def test_three_fields_and_ownership_gate():
    agent = service(); _, state = agent.respond('a', 'Margaret Chen, DOB 1985-03-15, last four 4472')
    assert state.verified
    answer, state = agent.respond('a', 'Tell me about CL-3001')
    assert state.case_id != 'CL-3001' and 'diagnosis' not in answer.lower()

def test_preverification_does_not_disclose():
    answer, state = service().respond('a', 'Why was CL-2048 denied?')
    assert state.phase == Phase.VERIFY_ID and 'pathology' not in answer.lower() and 'CL-2048' not in answer

def test_early_hint_and_document_memory_survive_verification():
    agent = service(); agent.respond('a', 'My denied healthcare claim is from January. I lost the pathology report.')
    answer, state = agent.respond('a', 'My name is Margaret Chen. DOB 1985-03-15. SSN last four 4472.')
    assert state.case_id == 'CL-2048' and state.document_memory['pathology report'].status == 'missing'
    assert 'replacement' in answer.lower() or 'copy' in answer.lower()

def test_correction_preserves_case_hint():
    agent = service(); agent.respond('a', 'My denied healthcare claim is from January. I lost the pathology report.')
    _, state = agent.respond('a', 'Actually I found the pathology report.')
    assert state.hint.case_type == 'healthcare' and state.hint.status == 'denied'
    assert state.document_memory['pathology report'].status == 'available'

def test_grounded_answers_and_summary_consent():
    talk(service(), SAMPLE, 'Why was it denied?', 'done')
    agent = service(); talk(agent, SAMPLE, 'Why was it denied?', 'done')
    assert agent.store.get('test').phase == Phase.POST_PROCESS and not agent.outbox
    _, state = agent.respond('test', 'skip')
    assert state.phase == Phase.COMPLETE and not agent.outbox

def test_send_requires_post_process_and_queues_once():
    agent = service(); talk(agent, SAMPLE, 'done'); _, state = agent.respond('test', 'send')
    assert state.phase == Phase.COMPLETE and len(agent.outbox) == 1
    agent.respond('test', 'send'); assert len(agent.outbox) == 1

def test_scope_and_handoff():
    agent = service()
    for _ in range(3): answer, state = agent.respond('a', 'What is RL?')
    assert state.handoff == 'offered' and 'human representative' in answer
    _, state = agent.respond('a', 'yes'); assert state.handoff == 'requested'

def test_identity_schema_has_no_authority_fields():
    schema = strict_schema(Turn)
    assert 'identity' not in schema['properties'] and 'phase' not in schema['properties']
    with pytest.raises(ValidationError): Turn.model_validate({'scope':'insurance', 'identity': {}})

def test_invalid_identity_threshold_rejected():
    with pytest.raises(ValueError): Repository().verify({}, required=2)

def test_api_surface():
    client = TestClient(app); response = client.post('/api/chat', json={'text': SAMPLE})
    assert response.status_code == 200 and response.json()['case_id'] == 'CL-2048'
    continued = client.post('/api/chat', json={'session_id': response.json()['session_id'], 'text': 'done'})
    assert continued.status_code == 200 and continued.json()['phase'] == 'POST_PROCESS'
    assert client.get('/api/health').json()['ok']
