"""Verify that the consented summary contains grounded, actionable evidence.

The test checks content and recipient selection after a real workflow session;
it does not exercise external delivery because the project intentionally uses a
simulated outbox.
"""
from app.models import DocumentMemory, Phase
from app.conversation import ConversationEngine
from test_lean_requirements import SAMPLE, service


HOSTED_DEMO_OPENING = (
    "I’m the policyholder. My name is Margaret Chen, policy POL-9921. "
    "I’m calling about my denied healthcare claim from January. "
    "DOB is 1985-03-15, SSN last four is 4472."
)


def test_summary_includes_grounded_outcome_and_actionable_next_steps():
    agent = service()
    _, state = agent.respond('summary', SAMPLE)
    state.document_memory['pathology report'] = DocumentMemory(
        status='missing', source_turn=1, source_text='I lost the report.')
    with agent.store.turn():
        agent.store.save('summary', state)
    agent.respond('summary', 'done')
    agent.respond('summary', 'send')
    message = agent.store.message_for('summary')
    assert 'denied' in message['body']
    assert 'portal' in message['body'].lower()
    assert 'replacement' in message['body'].lower()
    assert message['to'] == 'margaret@email.com'


def test_hosted_demo_full_email_preview_flow():
    """Protect the exact public-demo path from verification through preview."""
    agent = service()

    opening_reply, state = agent.respond('hosted-demo', HOSTED_DEMO_OPENING)
    assert state.phase == Phase.PROCESS_CASE
    assert state.verified and state.case_id == 'CL-2048'
    assert 'Hi Margaret' in opening_reply
    assert 'pathology report' in opening_reply and 'office note' in opening_reply

    offer_reply, state = agent.respond(
        'hosted-demo', 'That’s all. I’m done with my claim questions.')
    assert state.phase == Phase.POST_PROCESS
    assert state.email_offer_pending and state.email_choice is None
    assert 'email summary' in offer_reply and 'send or skip' in offer_reply
    assert agent.store.message_for('hosted-demo') is None

    completion_reply, state = agent.respond('hosted-demo', 'send')
    preview = agent.store.message_for('hosted-demo')
    assert state.phase == Phase.COMPLETE
    assert state.email_choice == 'queued_demo' and not state.email_offer_pending
    assert 'No real email was sent' in completion_reply
    assert preview['to'] == 'margaret@email.com'
    assert preview['subject'] == 'Insurance claim conversation summary'
    assert 'Claim CL-2048 (healthcare)' in preview['body']
    assert 'recorded as denied' in preview['body']
    assert 'pathology report' in preview['body'] and 'office note' in preview['body']
    assert 'member portal' in preview['body']


def test_post_process_consent_does_not_depend_on_model_provider():
    """An unavailable provider must not block deterministic send consent."""
    class UnavailableProvider:
        def complete(self, *args, **kwargs):
            raise AssertionError('POST_PROCESS must not call the model')

    agent = service()
    agent.respond('consent', SAMPLE)
    agent.respond('consent', 'done')
    agent.conversation = ConversationEngine(provider=UnavailableProvider())

    reply, state = agent.respond('consent', 'send')
    assert state.phase == Phase.COMPLETE
    assert state.email_choice == 'queued_demo'
    assert agent.store.message_for('consent')['to'] == 'margaret@email.com'
    assert 'No real email was sent' in reply
