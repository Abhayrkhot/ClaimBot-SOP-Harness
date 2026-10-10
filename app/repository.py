"""Fixture-backed ownership and approved claim statements."""
import json
from datetime import date
from pathlib import Path

FIELDS = ('name', 'dob', 'phone', 'email', 'id_last4')
ALIASES = {'pathology report': 'original pathology report', 'office note': 'treating provider office note'}
ABSENT = {'missing.appeal_decision': 'The available record contains no appeal decision, so I cannot confirm whether an appeal was denied.', 'missing.decision_date': 'No denial-decision date is recorded. The filing date and appeal deadline do not establish when a decision was made.', 'missing.notification_history': 'No notification history is available, so I cannot confirm whether, when, or how a notice was sent, or why you did not receive one.', 'missing.other': 'The available record does not answer that question. A representative would need to check the relevant information.'}

def norm(value, field=''):
    return ''.join(c for c in str(value) if c.isdigit())[-10:] if field == 'phone' else ' '.join(str(value).casefold().split())

class Repository:
    def __init__(self, root=None):
        root = Path(root or Path(__file__).resolve().parents[1] / 'fixtures')
        self.people = json.loads((root / 'policyholders.json').read_text()); self.claims = json.loads((root / 'claims.json').read_text())
        self.guidance = json.loads((root / 'required_document_guideline.json').read_text()); self.schema = json.loads((root / 'claim_schema.json').read_text())

    def verify(self, identity, required=3):
        if not 3 <= required <= len(FIELDS): raise ValueError('Identity verification requires three to five fields')
        matches = []
        for person in self.people:
            if identity.get('policy_number') and norm(identity['policy_number']) != norm(person['policy_number']): continue
            count = 0
            for field in FIELDS:
                if field not in identity: continue
                allowed = [person.get(field, ''), *person.get(field + '_aliases', [])]
                if not any(norm(identity[field], field) == norm(v, field) for v in allowed): break
                count += 1
            else:
                if count >= required: matches.append(person)
        return matches[0] if len(matches) == 1 else None

    def person(self, party_id): return next(p for p in self.people if p['party_id'] == party_id)

    def claims_for(self, party_id, hint):
        found = [c for c in self.claims if c['party_id'] == party_id]
        if hint.case_id: return [c for c in found if c['case_id'] == hint.case_id]
        for field in ('case_type', 'status'):
            if getattr(hint, field): found = [c for c in found if c[field] == getattr(hint, field)]
        if hint.month:
            found = [c for c in found if int(c['created_at'][5:7]) == hint.month]
            if hint.year: found = [c for c in found if int(c['created_at'][:4]) == hint.year]
        return found

    def claim(self, party_id, case_id): return next((c for c in self.claims if c['party_id'] == party_id and c['case_id'] == case_id), None)
    def alternatives(self, documents):
        entries = self.guidance['document_alternative_guidance']; return {d: entries.get(ALIASES.get(d, d), entries['default'])['en'] for d in documents}
    def guidance_text(self, topic, claim):
        entry = next(x for x in self.guidance['claim_followup_guidance'] if x['topic'] == topic)
        return entry['en'].format(case_id=claim['case_id'], documents=', '.join(claim.get('documents_needed', [])), average_processing_time_after_submission=self.guidance['claim_followup_settings']['average_processing_time_after_submission']['en'])

    def facts(self, claim, today):
        docs = claim.get('documents_needed', []); out = dict(ABSENT)
        out['claim.status'] = f"Claim {claim['case_id']} is recorded as {claim['status']}."
        out['claim.filed_date'] = f"The claim was filed on {claim['created_at']}; this is not a denial-decision date."
        out['claim.documents'] = f"The listed missing documents are: {', '.join(docs)}." if docs else 'No missing documents are listed for this claim.'
        if claim.get('denial_reason'): out['claim.denial_reason'] = f"The recorded claim-denial reason is: {claim['denial_reason']}."
        if claim.get('appeal_deadline'):
            out['claim.appeal_deadline'] = f"The recorded appeal deadline is {claim['appeal_deadline']}." + (' It has passed; I cannot confirm eligibility for a late appeal.' if date.fromisoformat(claim['appeal_deadline']) < today else '')
        for field, key, label in [('net_pay','claim.net_pay','recorded net payment'), ('expected_reimbursement_amount','claim.expected_reimbursement','expected reimbursement'), ('allowed_max_amount','claim.allowed_max','allowed maximum')]:
            if claim.get(field): out[key] = f"The {label} is USD {claim[field]}."
        if docs:
            out['guidance.submission'] = self.guidance_text('submission_method', claim); out.update({'alternative.' + d: v for d, v in self.alternatives(docs).items()})
        for entry in self.guidance['claim_followup_guidance']:
            if not entry.get('requires_documents') or docs: out['guidance.' + entry['topic']] = self.guidance_text(entry['topic'], claim)
        return out
