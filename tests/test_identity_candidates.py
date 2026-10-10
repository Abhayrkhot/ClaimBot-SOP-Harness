"""Unit tests for evidence-bound identity extraction and normalization.

These cases cover accepted formats plus adversarial attribution, correction,
negation, and substring scenarios. They verify that model-proposed text cannot
become trusted identity evidence unless local validation accepts it.
"""
import pytest

from app.identity import IdentityExtraction, validated_candidates
from app.repository import Repository


def extract(text, entries):
    return validated_candidates(text, IdentityExtraction(candidates=[
        {'field': field, 'source': source} for field, source in entries]))


@pytest.mark.parametrize('birthdate', ['1985-03-15', '1985/03/15', '03/15/1985',
                                     '15/03/1985', 'March 15, 1985', 'Mar 15 1985', '15 March 1985'])
def test_verbatim_dates_normalize_without_model_authored_values(birthdate):
    text = f'Margaret Chen, {birthdate}, 4472.'
    values = extract(text, [('name', 'Margaret Chen'), ('dob', birthdate), ('id_last4', '4472')])
    assert Repository().verify(values)['party_id'] == 'P9'


@pytest.mark.parametrize('birthdate', ['03/04/1985', '1985-02-30', '1985'])
def test_ambiguous_or_invalid_dates_do_not_count(birthdate):
    assert extract(birthdate, [('dob', birthdate)]) == {'dob': None}


@pytest.mark.parametrize('text,entries', [
    ('Margaret Chen', [('name', 'Margaret Chen'), ('dob', '1985-03-15')]),
    ('1985-03-15', [('dob', '1985-03-15'), ('id_last4', '1985')]),
    ('6505214472', [('phone', '6505214472'), ('id_last4', '4472')]),
    ('POL-4472', [('policy_number', 'POL-4472'), ('id_last4', '4472')]),
])
def test_missing_or_reused_evidence_fails_closed(text, entries):
    with pytest.raises(ValueError): extract(text, entries)


def test_duplicate_evidence_cannot_reach_three_fields():
    values = extract('Margaret Chen, 4472', [('name', 'Margaret Chen'), ('id_last4', '4472'), ('id_last4', '4472')])
    assert Repository().verify(values) is None


def test_policy_is_not_a_third_pii_field():
    values = extract('Margaret Chen, 4472, POL-9921', [('name', 'Margaret Chen'), ('id_last4', '4472'), ('policy_number', 'POL-9921')])
    assert Repository().verify(values) is None


def test_fields_from_different_people_do_not_verify():
    values = extract('Margaret Chen, 1990-08-21, 4472', [('name', 'Margaret Chen'), ('dob', '1990-08-21'), ('id_last4', '4472')])
    assert Repository().verify(values) is None


def test_conflicting_values_are_withheld():
    assert extract('4472 or 9180', [('id_last4', '4472'), ('id_last4', '9180')]) == {'id_last4': None}


@pytest.mark.parametrize('text,source', [('POL-4472', '4472'), ('1985-03-15', '1985'), ('650-521-4472', '4472')])
def test_a_numeric_component_alone_is_not_independent_evidence(text, source):
    with pytest.raises(ValueError): extract(text, [('id_last4', source)])


def test_policy_number_does_not_reduce_requested_pii_count():
    from test_lean_requirements import service
    reply, state = service().respond('s', 'My name is Margaret Chen. Policy POL-9921.')
    assert not state.verified and '2 more verification details' in reply


def test_model_path_never_uses_regex_identity_parser(monkeypatch):
    from app import workflow
    from app.conversation import ConversationEngine
    from test_response_precision import Provider, OPENING
    from test_lean_requirements import service
    monkeypatch.setattr(workflow, 'identity', lambda _: (_ for _ in ()).throw(AssertionError('regex identity used')))
    _, state = service(ConversationEngine(provider=Provider())).respond('s', OPENING)
    assert state.verified


def test_raw_identity_goes_only_to_extraction_and_not_retained_history():
    from app.conversation import ConversationEngine
    from test_response_precision import Provider, OPENING
    from test_lean_requirements import service
    calls = []
    class RecordingProvider(Provider):
        def complete(self, name, schema, prompt, context, timeout):
            calls.append((name, context))
            return super().complete(name, schema, prompt, context, timeout)
    _, state = service(ConversationEngine(provider=RecordingProvider())).respond('s', OPENING)
    assert calls[0][0] == 'Identity'
    for secret in ('Margaret Chen', '1985-03-15', '4472'):
        assert secret in str(calls[0][1])
        assert secret not in str([call for call in calls if call[0] not in ('Identity', 'IdentityAssertion', 'BirthDateUse')])
        assert secret not in str(state.history)


def test_hallucinated_identity_preserves_existing_state():
    from app.conversation import ConversationEngine
    from test_response_precision import Provider
    from test_lean_requirements import service
    from app.models import Session
    class InventingProvider(Provider):
        def complete(self, name, schema, prompt, context, timeout):
            if name == 'Identity': return {'subject': 'caller', 'name': 'Margaret Chen', 'dob': '1985-03-15', 'id_last4': '4472'}
            return super().complete(name, schema, prompt, context, timeout)
    agent = service(ConversationEngine(provider=InventingProvider()))
    with agent.store.turn(): agent.store.save('s', Session(identity={'name': 'Margaret Chen'}))
    reply, state = agent.respond('s', 'Please verify me.')
    assert state.identity == {'name': 'Margaret Chen'} and not state.verified
    assert state.response_mode == 'fallback' and 'Nothing was changed' in reply


@pytest.mark.parametrize('attribution', [{'subject': 'other'}, {'subject': 'hypothetical'}, {'date_role': 'other'}])
def test_attribution_cannot_count_third_party_or_claim_date(attribution):
    from app.conversation import ConversationEngine
    from app.models import Session
    from test_response_precision import Provider, OPENING
    class AttributedProvider(Provider):
        def complete(self, name, schema, prompt, context, timeout):
            if name == 'BirthDateUse': return {'is_caller_birth_date': False}
            result = super().complete(name, schema, prompt, context, timeout)
            if name == 'Identity': result.update({key: value for key, value in attribution.items() if key == 'subject'})
            return result
    engine = ConversationEngine(provider=AttributedProvider())
    engine.begin_turn()
    candidates, safe = engine.extract_identity(OPENING, Session())
    assert Repository().verify(candidates) is None
    assert '1985-03-15' not in safe and 'Margaret Chen' not in safe


def test_independent_attribution_rejection_cannot_verify():
    from app.conversation import ConversationEngine
    from app.models import Session
    from test_response_precision import Provider, OPENING
    class RejectingProvider(Provider):
        def complete(self, name, schema, prompt, context, timeout):
            if name == 'IdentityAssertion': return {'asserted_by_caller': False}
            return super().complete(name, schema, prompt, context, timeout)
    engine = ConversationEngine(provider=RejectingProvider())
    engine.begin_turn()
    candidates, _ = engine.extract_identity(OPENING, Session())
    assert candidates == {}


def test_validation_retry_stays_within_evidence_contract():
    from app.conversation import ConversationEngine
    from app.models import Session
    from test_response_precision import Provider
    class RepairProvider(Provider):
        def complete(self, name, schema, prompt, context, timeout):
            if name == 'IdentityAssertion': return {'asserted_by_caller': True}
            if name == 'BirthDateUse': return {'is_caller_birth_date': True}
            return {'subject': 'caller', 'name': 'Margaret Chen', 'dob': 'March 15, 1985' if 'validation_feedback' in context else '1985-03-15', 'id_last4': '4472'}
    engine = ConversationEngine(provider=RepairProvider())
    engine.begin_turn()
    candidates, _ = engine.extract_identity('Margaret Chen, March 15, 1985, 4472.', Session())
    assert Repository().verify(candidates)['party_id'] == 'P9'


def test_missing_attribution_fails_schema_validation():
    from app.identity import IdentitySlots
    with pytest.raises(ValueError):
        IdentitySlots.model_validate({'name': 'Margaret Chen', 'dob': '1985-03-15', 'id_last4': '4472'})


@pytest.mark.parametrize('text', ['"Margaret Chen, 1985-03-15, 4472"', 'Example: “Margaret Chen, 1985-03-15, 4472.”'])
def test_quoted_details_cannot_supply_identity_evidence(text):
    with pytest.raises(ValueError):
        extract(text, [('name', 'Margaret Chen'), ('dob', '1985-03-15'), ('id_last4', '4472')])


def test_numeric_reply_cannot_generate_a_name_date_or_phone():
    from app.identity import IdentitySlots, evidence_schema
    from app.conversation import strict_schema
    schema = evidence_schema('4472', strict_schema(IdentitySlots))['properties']
    for field in ('name', 'birth_date_quote', 'phone', 'email'):
        assert schema[field]['type'] == 'null'
    assert schema['id_last4']['anyOf'][0]['enum'] == ['4472']


def test_generation_schema_contains_literal_dates_not_invented_normalizations():
    from app.identity import IdentitySlots, evidence_schema
    from app.conversation import strict_schema
    schema = evidence_schema('Margaret Chen, March 15, 1985, 4472.', strict_schema(IdentitySlots))
    assert schema['properties']['birth_date_quote']['anyOf'][0]['enum'] == ['March 15, 1985']


@pytest.mark.parametrize('text,expected', [
    ('Margaret Chen, March 15, 1985, 4472.', True),
    ('Margaret Chen, March 15, 1985, 4472. My claim is from January.', False),
    ('Margaret Chen, March 15, 1985, 4472. These are my neighbor’s details, not mine.', False),
    ('My name is Margaret Chen. My claim was filed on March 15, 1985. Last four 4472.', False),
    ('My neighbor is Margaret Chen, born March 15, 1985, last four 4472.', False),
])
def test_tuple_structure_does_not_accept_prose_about_someone_else(text, expected):
    from app.identity import is_identity_tuple
    assert is_identity_tuple(text, {'name': 'Margaret Chen', 'dob': 'March 15, 1985', 'id_last4': '4472'}) is expected
