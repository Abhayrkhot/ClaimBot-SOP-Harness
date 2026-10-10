"""End-to-end live-model corpus for semantic generalization and SOP safety.

These opt-in cases use the configured provider through the production adapter.
They measure paraphrase understanding while continuing to assert deterministic
verification, ownership, grounding, and consent behavior.
"""
from datetime import date

import pytest

from app.conversation import ConversationEngine
from app.repository import Repository
from app.store import Store
from app.workflow import ConversationService

SAMPLE = ("I'm the policyholder. My name is Margaret Chen, policy POL-9921. "
          "I'm calling about my denied healthcare claim from January. "
          "DOB is 1985-03-15, SSN last four is 4472.")
UNKNOWN = 'The available record does not answer that question.'

PARAPHRASES = [
    'I need assistance with my claim.', 'I have a question about this claim.', 'Can someone help me?',
    'What can you tell me about my claim?', 'I need information on my claim.', 'Can you help me understand this?',
    'I want to discuss my claim.', 'Please explain my claim.', 'I need support with this case.',
    'Can we go over my claim?', 'I need guidance on this claim.', 'Tell me more about my case.',
    'How is my claim doing?', 'Any news?', "What's happening with my claim?", 'Where does my claim stand?',
    'Has anything changed?', 'Is my claim still open?', 'Can I get an update?', "What's the latest?",
    'Is there progress on it?', 'What is the current situation?', 'Can you check where things are?',
    'Do you have a status update for me?', 'Why did you turn it down?', "Why wasn't it approved?",
    'What led to this outcome?', 'What caused the denial?', 'Why did this happen?', 'Why did you reject it?',
    'What was the reason for the rejection?', 'What made the claim unsuccessful?', 'Why was it not covered?',
    'Can you explain the decision?', 'What is behind this result?', 'Why was my request refused?',
    'What should I send?', 'What do you need from me?', 'Which records are required?', 'What paperwork do I need?',
    'How do I provide the missing items?', 'What supporting evidence do you need?', 'Which files should I upload?',
    'What information should I submit?', 'What do I need to provide?', 'Can I send additional records?',
    'What materials are needed?', 'How can I complete my file?', 'Can I contest this?',
    'How do I challenge the decision?', 'Is reconsideration possible?', 'What are my options to appeal?',
    'Can I request a review?', 'How do I dispute this?', 'Is there a reconsideration process?',
    'Can this decision be reviewed?', 'When must I appeal?', 'What is the process for challenging the denial?',
    'When did you receive my claim?', 'What date did I submit it?', 'When was it filed?', 'When was it received?',
    'On what day did the claim arrive?', 'When did I send this in?', 'What is the submission date?',
    'When did the insurer get it?', 'How long ago was it filed?', 'How much will I get?', 'What did the insurer pay?',
    'What is my payout?', 'What reimbursement should I expect?', 'What amount is approved?',
    'How much money was issued?', 'What was the payment amount?', 'Will I receive any money?',
    'What did this claim pay?', 'I am calling about my claim.', 'This is about my claim.',
    'I want to talk about the claim.', 'Please look at my case.', 'Can you review my claim?',
    'I need you to check something.', 'There is an issue with my claim.', 'I have a concern about the claim.',
]

def make(engine):
    return ConversationService(Repository(), Store(), today=lambda: date(2026, 10, 9), conversation=engine)

@pytest.mark.live
@pytest.mark.parametrize('question', PARAPHRASES)
def test_existing_84_paraphrases_without_keyword_routing(question):
    engine = ConversationEngine()
    assert engine.provider, 'Live evaluation requires a configured provider'
    agent = make(engine); agent.respond('live', SAMPLE)
    reply, state = agent.respond('live', question)
    assert state.response_mode == 'model' and state.phase == 'PROCESS_CASE'
    assert UNKNOWN not in reply

@pytest.mark.live
def test_unseen_multi_intent_correction_and_reference_cases():
    engine = ConversationEngine()
    assert engine.provider, 'Live evaluation requires a configured provider'
    agent = make(engine); agent.respond('unseen', 'My denied healthcare claim is from January, but I first said auto by mistake.')
    agent.respond('unseen', 'My name is Margaret Chen, DOB 1985-03-15, email margaret@email.com')
    reply, state = agent.respond('unseen', 'What caused the refusal, and what should I send to appeal?')
    assert state.case_id == 'CL-2048' and 'pathology' in reply.lower() and '2026-03-18' in reply
    reply, state = agent.respond('unseen', 'Actually, I found the pathology report.')
    assert state.document_memory['pathology report'].status == 'available'
    reply, state = agent.respond('unseen', 'What about that one?')
    assert state.phase == 'PROCESS_CASE' and 'clarify' in reply.lower()
