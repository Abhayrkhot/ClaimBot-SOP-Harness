"""Verify that the consented summary contains grounded, actionable evidence.

The test checks content and recipient selection after a real workflow session;
it does not exercise external delivery because the project intentionally uses a
simulated outbox.
"""
from app.models import DocumentMemory
from test_lean_requirements import SAMPLE, service


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
