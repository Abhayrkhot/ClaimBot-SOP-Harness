"""Deterministic SOP controller for the complete customer conversation.

The controller owns phase order, identity gating, claim ownership, fact access,
memory updates, completion, and email consent. Model interpretations can propose
meaning, but this module validates each proposal against current state and
repository data before changing state or producing a grounded response.
"""
import re
from datetime import date, datetime
from .conversation import redact
from .models import ACK, DocumentMemory, Phase, Session
from .repository import FIELDS
from .store import Store

class ConcurrentTurnError(RuntimeError): pass

TRANSITIONS = {Phase.VERIFY_ID: Phase.RESOLVE_INTENT, Phase.RESOLVE_INTENT: Phase.PROCESS_CASE, Phase.PROCESS_CASE: Phase.POST_PROCESS, Phase.POST_PROCESS: Phase.COMPLETE}
OFFTOPIC = 'I can only help with insurance claims and related support.'
HUMAN = ' Would you like a human representative? Reply yes or no. This demo can record the request but cannot transfer a live call.'
SUMMARY = 'Would you like an email summary of our discussion, claim status and next steps sent to your registered email address? Reply send or skip. Delivery is simulated.'
RECOVERY = 'I’m having trouble understanding that message right now. Nothing was changed. Please rephrase your insurance question or ask for a human representative.'
FINISH = re.compile(r"\b(done|finished|goodbye|bye|all done|that(?: is|'s) (?:all|everything))\b", re.I)
YES = re.compile(r'^\s*(yes|sure|okay|ok|please do|go ahead|yeah)\s*[.!]?\s*$', re.I)
NO = re.compile(r"^\s*(no|nope|not now|decline|skip|don't send)\s*[.!]?\s*$", re.I)

def identity(text):
    """Parse explicitly labeled identity values in limited offline rules mode."""
    out = {}; patterns = {'dob': r'\b(?:dob|date of birth|born(?: on)?|birthday)\s*(?:is|:)?\s*([\d/-]{8,10}|[A-Za-z]+ \d{1,2},? \d{4})', 'phone': r'\b(?:phone|mobile|cell)(?: number)?\s*(?:is|:)?\s*(\+?\(?\d[\d ()-]{8,}\d)', 'email': r'\b([\w.+-]+@[\w.-]+\.[a-z]{2,})\b', 'policy_number': r'\b(POL-\d+)\b'}
    for field, pattern in patterns.items():
        match = re.search(pattern, text, re.I)
        if match: out[field] = match.group(1)
    match = re.search(r'\b(?:ssn|social security|id|last\s*(?:four|4))\b[^\d]{0,30}(\d{4})\b', text, re.I)
    if match: out['id_last4'] = match.group(1)
    match = re.search(r"\b(?:my name is|name is|name's|call me)\s+([A-Z][A-Za-z'-]+(?:\s+[A-Z][A-Za-z'-]+){1,3})\b", text, re.I)
    if match and match.group(1).casefold() not in ('the policyholder', 'a policyholder'): out['name'] = match.group(1)
    if 'name' not in out:
        match = re.match(r"\s*([A-Z][A-Za-z'-]+\s+[A-Z][A-Za-z'-]+)\s*,?\s*(?=(?:DOB|date of birth|born|email|phone|last four|SSN)\b)", text, re.I)
        if match: out['name'] = match.group(1)
    if 'dob' in out:
        for fmt in ('%Y-%m-%d','%m/%d/%Y','%d/%m/%Y','%B %d, %Y','%B %d %Y'):
            try: out['dob'] = datetime.strptime(out['dob'].replace('  ', ' '), fmt).date().isoformat(); break
            except ValueError: pass
    return out

def hint(text):
    """Collect conservative case hints for limited offline rules mode."""
    result = {}
    match = re.search(r'\bCL-\d+\b', text, re.I)
    if match: return {'case_id': match.group().upper()}
    for value in ('healthcare', 'dental', 'auto'):
        if re.search(rf'\b{value}\b', text, re.I): result['case_type'] = value
    for value in ('denied', 'closed', 'open'):
        if re.search(rf'\b{value}\b', text, re.I): result['status'] = value
    months = ('january','february','march','april','may','june','july','august','september','october','november','december')
    match = re.search(r'\b(?:claim|case)\b[^.!?]{0,50}\b(?:from|in)\s+(' + '|'.join(months) + r')\b(?:\s+(20\d{2}))?', text, re.I)
    if match: result.update(month=months.index(match.group(1).lower()) + 1, year=int(match.group(2)) if match.group(2) else None)
    return {key: value for key, value in result.items() if value is not None}

def intent(text):
    """Map explicit requests in rules mode; model mode does not use this router."""
    t = text.casefold()
    if re.search(r'\b(guarantee|promise)\b', t): return 'guarantee'
    if re.search(r'\b(appeal|deadline|expired|missed)\b', t): return 'appeal'
    if re.search(r'\b(status|progress|update)\b', t): return 'status'
    if re.search(r'\b(denied|denial|rejected|why|reason|turned down)\b', t): return 'denial'
    if re.search(r'\b(document|paperwork|pathology|office note|missing|replacement|substitute)\b', t): return 'documents'
    if re.search(r'\b(when|date)\b.*\b(filed|submitted)\b', t): return 'filing_date'
    if re.search(r'\b(payment|paid|reimbursement|amount|how much)\b', t): return 'payment'
    return None

def scope(text): return 'unrelated' if re.search(r'\b(photosynthesis|reinforcement learning|what is rl|python|weather|recipe|bitcoin|election)\b', text, re.I) else 'insurance'

def remember_documents(state, text):
    """Store caller-reported document possession for later grounded guidance."""
    for doc in ('pathology report', 'office note', 'diagnosis report'):
        if doc not in text.casefold() or '?' in text or re.search(r'\b(if|might|maybe|suppose)\b', text, re.I): continue
        status = 'missing' if re.search(r"\b(lost|missing|cannot|can't|don't have|not yet)\b", text, re.I) else 'available' if re.search(r'\b(i|we)\s+(found|have|received|obtained|located)', text, re.I) else None
        if status: state.document_memory[doc] = DocumentMemory(status=status, source_turn=state.revision + 1, source_text=text)

def advance(state, target):
    """Permit only the declared forward transition from the current phase."""
    if TRANSITIONS.get(state.phase) != target: raise ValueError('Illegal phase transition')
    if not state.verified or not state.party_id: raise PermissionError('Verified customer required')
    if target != Phase.RESOLVE_INTENT and not state.case_id: raise ValueError('Claim selection required')
    if target == Phase.COMPLETE and state.email_choice is None: raise ValueError('Email decision required')
    state.phase = target

class ConversationService:
    """Orchestrate one atomic message through interpretation and the SOP."""
    def __init__(self, repo, store=None, today=date.today, conversation=None): self.repo, self.store, self.today, self.conversation = repo, store or Store(), today, conversation
    @property
    def outbox(self): return self.store.messages()
    def exists(self, sid):
        with self.store.lock: return self.store.get(sid) is not None
    def end_session(self, sid):
        with self.store.turn():
            state = self.store.get(sid)
            if not state: return False
            state.phase, state.email_choice = Phase.COMPLETE, state.email_choice or 'skipped'; state.revision += 1; self.store.save(sid, state)
        return True
    def _apply_plan(self, state, plan, text):
        """Copy safe semantic memory into state without executing any action."""
        for key, value in plan.hint.model_dump(exclude_none=True).items():
            if key == 'year' and state.hint.year is None: continue
            if key == 'case_id' and value.upper() not in text.upper(): continue
            if key == 'case_id' and state.case_id and value != state.case_id and plan.claim_action != 'switch': continue
            if getattr(state.hint, key) is None:
                setattr(state.hint, key, value)
        if plan.questions: state.original_question = plan.questions[0]
        for update in plan.memory_updates:
            note = f'{update.kind}:{update.key}={update.value}'
            state.caller_notes = (state.caller_notes + [note])[-12:]
        named = [update for update in plan.document_updates if update.document in text.casefold()]
        if not named and not state.document_memory and len(plan.document_updates) == 1:
            named = plan.document_updates
        if not named and len(state.document_memory) == 1:
            known = next(iter(state.document_memory)); named = [u for u in plan.document_updates if u.document == known]
        for update in named:
            state.document_memory[update.document] = DocumentMemory(status=update.status, source_turn=state.revision + 1, source_text=text)
        if plan.follow_up != 'none': state.next_steps = list(dict.fromkeys(state.next_steps + [plan.follow_up]))[-6:]
    def respond(self, sid, text):
        """Process a turn atomically and restore the snapshot on model failure."""
        with self.store.lock: state = self.store.get(sid) or Session()
        revision = state.revision; before = state.model_copy(deep=True)
        active = state.phase != Phase.COMPLETE and state.handoff != 'requested'; was_verified, had_case = state.verified, state.case_id
        # POST_PROCESS is a deterministic consent gate. Calling the model here
        # can only delay or block an exact send/skip decision; it has no authority
        # to change the pending offer or authorize the outbox side effect.
        model_mode = bool(active and state.phase != Phase.POST_PROCESS and
                          self.conversation and self.conversation.mode == 'model')
        if not model_mode: state.identity.update(identity(text))
        if not model_mode and state.phase == Phase.VERIFY_ID and not state.opening_message: state.opening_message = redact(text, state.identity)
        # Exact claim IDs are safe to detect locally; access still depends on the
        # verified party and the repository's ownership-first lookup.
        explicit_hint = hint(text)
        requested_case = explicit_hint.get('case_id')
        if not model_mode and (not state.case_id or requested_case in (None, state.case_id)):
            for key, value in explicit_hint.items(): setattr(state.hint, key, value)
        elif model_mode and requested_case and requested_case in (None, state.case_id): state.hint.case_id = requested_case
        if not model_mode: remember_documents(state, text)
        safe = redact(text, state.identity); plan = None; interpretation_failed = False
        if model_mode:
            try:
                self.conversation.begin_turn()
                if state.phase == Phase.VERIFY_ID:
                    candidates, safe = self.conversation.extract_identity(text, state)
                    for field, value in candidates.items():
                        if value is None: state.identity.pop(field, None)
                        else: state.identity[field] = value
                    if not state.opening_message: state.opening_message = safe
                facts = self.repo.facts(self.repo.claim(state.party_id, state.case_id), self.today()) if state.case_id else {}
                plan = self.conversation.interpret(safe, state, facts); state.response_mode = 'model'; self._apply_plan(state, plan, text)
                target = requested_case or plan.hint.case_id
                switch_action = plan.claim_action == 'switch' or (plan.claim_action == 'select' and requested_case and requested_case != state.case_id)
                if state.verified and switch_action:
                    previous_case = state.case_id
                    if not (target or plan.hint.model_dump(exclude_none=True)):
                        state.outstanding_clarification = 'claim_reference'
                    elif target != previous_case and self._switch_reference(state, plan.hint, target):
                        facts = self.repo.facts(self.repo.claim(state.party_id, state.case_id), self.today())
                        plan = self.conversation.interpret(safe, state, facts); self._apply_plan(state, plan, text)
                    else: state.outstanding_clarification = 'claim_reference'
                elif state.phase == Phase.PROCESS_CASE and plan.claim_action in ('unclear', 'ambiguous_reference'):
                    state.outstanding_clarification = 'claim_reference'
                elif state.phase == Phase.PROCESS_CASE and plan.claim_action in ('none', 'reference'):
                    state.outstanding_clarification = ''
            except Exception as exc:
                # Fail closed: discard every tentative mutation made during this
                # model turn before generating the recovery response.
                self.conversation.failed('interpret', exc); state = before; state.response_mode = 'fallback'; interpretation_failed = True
        elif not model_mode:
            for key, value in hint(text).items(): setattr(state.hint, key, value)
        questions = plan.questions if plan else ([] if model_mode else ([text] if intent(text) else []))
        if questions and state.phase in (Phase.VERIFY_ID, Phase.RESOLVE_INTENT) and not FINISH.search(text): state.pending_questions = list(dict.fromkeys(state.pending_questions + questions))[-6:]
        if plan and plan.topics and state.phase in (Phase.VERIFY_ID, Phase.RESOLVE_INTENT): state.pending_topics = list(dict.fromkeys(state.pending_topics + plan.topics))[-6:]
        reply = self._reply(state, text, safe, plan, was_verified, had_case, model_mode, interpretation_failed, state.identity != before.identity)
        if active: state.history = (state.history + [{'role':'user','content':safe}, {'role':'assistant','content':reply}])[-12:]
        with self.store.turn():
            current = self.store.get(sid)
            # The revision check catches a second request that loaded the same
            # state before this transaction acquired the database write lock.
            if (current.revision if current else 0) != revision: raise ConcurrentTurnError('Conversation changed; retry')
            if state.pending_email_body is not None: self.store.queue(sid, self.repo.person(state.party_id)['email'], state.pending_email_body); state.pending_email_body = None
            state.revision += 1; self.store.save(sid, state)
        return reply, state
    def _reply(self, state, text, safe_text, plan, was_verified, had_case, model_mode=False, interpretation_failed=False, identity_updated=False):
        """Choose the response path from trusted state and the current phase."""
        if state.phase == Phase.COMPLETE: return 'This conversation is complete. Please start a new session.'
        if state.handoff == 'requested': return 'Your request for human support is recorded in this demo. Contact the insurer using your policy or member portal. No live transfer has occurred.'
        if interpretation_failed: return RECOVERY
        current_scope = plan.scope if plan else scope(text)
        if model_mode and current_scope == 'unclear': return 'Could you clarify what you would like help with? Nothing was changed.'
        empathy = ACK.get(plan.emotion, '') if plan else ('I hear how frustrating this is.' if re.search(r'\b(ridiculous|frustrated|unfair|upset|angry)\b', text, re.I) else '')
        out_of_scope = current_scope == 'unrelated' or (current_scope == 'mixed' and plan and plan.topics == ['other'] and not plan.hint.model_dump(exclude_none=True))
        if out_of_scope and not (state.phase == Phase.VERIFY_ID and identity_updated):
            state.irrelevant += 1
            if state.irrelevant >= 3: state.handoff = 'offered'; return OFFTOPIC + HUMAN
            return OFFTOPIC + ' What would you like to do next?'
        state.irrelevant = 0
        if not model_mode and re.search(r'\b(?:human|live)\s+(?:representative|agent|support)\b', text, re.I): state.handoff = 'offered'
        if plan and plan.wants_human: state.handoff = 'offered'
        if state.handoff == 'offered' and YES.fullmatch(text): state.handoff = 'requested'; return 'Your request for human support is recorded in this demo. Contact the insurer using your policy or member portal. No live transfer has occurred.'
        if state.handoff == 'offered': return (ACK.get(plan.emotion, '') + ' ' if plan else '') + self._verify_prompt(state) + HUMAN
        finish = plan.action == 'finish' if model_mode and plan else FINISH.search(text) is not None
        if finish and state.phase == Phase.PROCESS_CASE: advance(state, Phase.POST_PROCESS); state.email_offer_pending = True; return SUMMARY
        if state.phase == Phase.POST_PROCESS:
            send = re.fullmatch(r'\s*send\s*[.!]?\s*', text, re.I)
            skip = NO.fullmatch(text)
            if send and state.email_offer_pending: return self._email(state, True)
            if skip and state.email_offer_pending: return self._email(state, False)
            return SUMMARY
        if state.phase == Phase.VERIFY_ID:
            person = self.repo.verify(state.identity)
            if person:
                state.party_id, state.verified = person['party_id'], True; state.identity.clear(); advance(state, Phase.RESOLVE_INTENT)
                greeting = f"Hi {person['name'].split()[0]}, thanks for verifying your identity. "
                return greeting + (empathy + ' ' if empathy else '') + self._resolve(state, plan if model_mode else None, safe_text, model_mode and not had_case)
            return (empathy + ' ' if empathy else '') + ('I couldn’t verify your identity with the details provided. Please check them or provide different details.' if sum(field in state.identity for field in FIELDS) >= 3 else self._verify_prompt(state))
        if state.phase == Phase.RESOLVE_INTENT: return self._resolve(state, plan if model_mode else None, safe_text, model_mode and not had_case)
        if state.outstanding_clarification == 'claim_reference': return 'Could you clarify which claim you want to use, for example its claim number or type?'
        if plan and plan.needs_clarification and not self._plan_keys(plan): return 'Could you clarify what you would like me to do? Nothing has been sent.'
        return (empathy + ' ' if empathy else '') + self._answer(state, None if model_mode else intent(text), text, plan if model_mode else None)
    def _verify_prompt(self, state):
        labels = {'name':'full name','dob':'date of birth','phone':'phone number','email':'email address','id_last4':'last four ID/SSN digits'}; missing = [v for k,v in labels.items() if k not in state.identity]
        count = max(0, 3 - sum(field in state.identity for field in labels))
        return f"Before I look into your claim, could you share {count} more verification {'detail' if count == 1 else 'details'}? You can use {', '.join(missing)}."
    def _resolve(self, state, plan=None, safe_text='', reground=False):
        """Resolve remembered hints only against claims owned by the caller."""
        matches = self.repo.claims_for(state.party_id, state.hint)
        if len(matches) != 1:
            state.outstanding_clarification = 'claim_reference'
            return 'How can I help you with your claim today?' if not matches else 'Could you tell me which claim you mean, for example its claim number or type?'
        state.outstanding_clarification = ''
        state.case_id = matches[0]['case_id']; advance(state, Phase.PROCESS_CASE)
        if reground and plan and '?' not in safe_text:
            plan = plan.model_copy(update={'topics': []})
            state.pending_questions.clear()
            state.pending_topics.clear()
        if plan and state.pending_topics: plan = plan.model_copy(update={'topics': list(dict.fromkeys(state.pending_topics + plan.topics))})
        rules_mode = not self.conversation or self.conversation.mode != 'model'
        answers = self._answer(state, None, '', plan) if plan and self._plan_keys(plan) else (' '.join(self._answer(state, intent(q), q, None) for q in state.pending_questions if intent(q)) if plan is None and rules_mode else '')
        if not answers:
            # A newly selected claim gets one grounded overview when no specific fact was requested.
            facts = self.repo.facts(matches[0], self.today())
            answers = facts.get('claim.denial_reason', facts['claim.status'])
            if answers not in state.discussed: state.discussed.append(answers)
        if any(item.status == 'missing' for item in state.document_memory.values()): answers += ' ' + ' '.join(self.repo.alternatives([doc for doc, item in state.document_memory.items() if item.status == 'missing']).values())
        state.pending_questions.clear(); state.pending_topics.clear(); return f"I found your {matches[0]['case_type']} claim {state.case_id}. {answers}".strip()

    def _switch_reference(self, state, reference, case_id=None):
        """Switch owned claims while clearing claim-specific conversational state."""
        query = type(state.hint)(case_id=case_id) if case_id else reference
        matches = self.repo.claims_for(state.party_id, query)
        if len(matches) != 1: return False
        claim = matches[0]; state.case_id = claim['case_id']
        state.hint = type(state.hint)(case_id=claim['case_id'], case_type=claim['case_type'],
                                      status=claim['status'], month=int(claim['created_at'][5:7]),
                                      year=int(claim['created_at'][:4]))
        state.pending_questions.clear(); state.discussed.clear(); state.next_steps.clear(); state.document_memory.clear()
        state.original_question = ''
        return True
    def _plan_keys(self, plan):
        topics = {'general_help':['claim.status', 'claim.denial_reason'], 'status':['claim.status'], 'denial_reason':['claim.denial_reason'],
                  'documents':['claim.documents'], 'appeal_support':['claim.documents', 'claim.appeal_deadline'], 'appeal_deadline':['claim.appeal_deadline'],
                  'filing_date':['claim.filed_date'], 'net_payment':['claim.net_pay'],
                  'expected_reimbursement':['claim.expected_reimbursement'], 'allowed_max':['claim.allowed_max'],
                  'submission':['guidance.submission'], 'decision_date':['missing.decision_date'],
                  'notification':['missing.notification_history'], 'other':['missing.other']}
        return list(dict.fromkeys(key for topic in plan.topics for key in topics[topic])) if plan.topics else plan.fact_ids
    def _answer(self, state, which, text, plan):
        """Render only approved repository facts selected for this request."""
        claim = self.repo.claim(state.party_id, state.case_id); facts = self.repo.facts(claim, self.today()); keys = [k for k in (self._plan_keys(plan) if plan else []) if k in facts]
        if not plan: keys = {'status':['claim.status'], 'denial':['claim.denial_reason'], 'appeal':['claim.appeal_deadline'], 'filing_date':['claim.filed_date'], 'notification':['missing.notification_history'], 'decision_date':['missing.decision_date'], 'documents':['claim.documents'], 'payment':['claim.net_pay','claim.expected_reimbursement','claim.allowed_max']}.get(which, [])
        if not plan and which == 'guarantee': return 'I cannot guarantee approval. Submitting documents does not guarantee a change in the claim decision.'
        if not plan and which == 'documents' and 'instead' in text.casefold(): keys = [k for k in facts if k.startswith('alternative.')]
        if not keys: keys = ['missing.other']
        if 'missing.appeal_decision' in keys: keys += ['claim.denial_reason', 'claim.status']
        if 'claim.denial_reason' in keys and 'claim.documents' in keys and claim.get('documents_needed') and all(doc.casefold() in facts['claim.denial_reason'].casefold() for doc in claim['documents_needed']):
            keys.remove('claim.documents')
        lines = list(dict.fromkeys(facts[k] for k in keys if k in facts)); state.discussed.extend(x for x in lines if x not in state.discussed); return ' '.join(lines)
    def _email(self, state, send):
        """Apply explicit send/skip consent to an existing pending offer."""
        state.email_offer_pending = False; state.email_choice = 'queued_demo' if send else 'skipped'
        if send: state.pending_email_body = self._summary(state)
        advance(state, Phase.COMPLETE); return ("I've prepared your summary and queued it in our demo outbox. No real email was sent." if send else "Of course. I won't send an email.") + ' Thank you for speaking with ClaimBot!'
    def _summary(self, state):
        """Build the demo email from facts actually discussed in this session."""
        claim = self.repo.claim(state.party_id, state.case_id)
        steps = [self.repo.guidance_text('submission_method', claim)] if claim.get('documents_needed') else ['Ask a representative if more information is needed.']
        steps += list(self.repo.alternatives([doc for doc, item in state.document_memory.items() if item.status == 'missing']).values())
        return f"Claim {claim['case_id']} ({claim['case_type']}) was filed on {claim['created_at']} and is recorded as {claim['status']}.\n\nWhat we discussed:\n" + '\n'.join('- '+x for x in state.discussed) + '\n\nNext steps:\n' + '\n'.join('- '+x for x in steps)
