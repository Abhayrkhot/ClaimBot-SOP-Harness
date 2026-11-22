"""Claims SOP decision logic: turns one interpretation into state changes and a response plan.

``decide`` is a deterministic function of (session, interpretation, message). It never trusts
the interpretation for authority: identity comes from cited spans matched by the repository,
claims are resolved only among the verified party's own records, facts come from the catalog,
and every phase change, disclosure and email goes through the turn context's policy checks.
"""

from __future__ import annotations

import re
from collections.abc import Sequence
from pathlib import Path
from typing import Any

from ..sop.policy import PolicyEnforcer, TurnContext
from ..sop.spec import SOPSpec, load_spec
from ..sop.state import ActionRequest, Fact
from .facts import claim_facts, claim_label, list_facts, party_facts, select_for_topics
from .identity import FIELD_LABELS, FIELDS, merge_readings, validate_evidence
from .models import ClaimsSession, DocumentNote, Interpretation, RememberedHint
from .repository import ClaimHint, Repository

SOP_PATH = Path(__file__).with_name("sop.yaml")
EMAIL_ACTION = "email_summary"

_SEND = re.compile(
    r"^\s*(send|send it|yes|yes please|sure|ok|okay|please do|go ahead|yep|yeah)\s*[.!]*\s*$", re.I
)
_SKIP = re.compile(
    r"^\s*(skip|no|no thanks|no thank you|nope|don'?t send|do not send|not now)\s*[.!]*\s*$", re.I
)
_NEGATION = re.compile(r"\b(don'?t|do not|no|not|never|cancel|stop)\b", re.I)
_EMAIL = re.compile(r"[\w.+-]+@[\w-]+(?:\.[\w-]+)*\.[A-Za-z]{2,}")
_DIGITS = re.compile(r"(?<![\w-])\+?\d[\d ()./-]{2,}\d(?!\w)")
_DOC_STOPWORDS = {"report", "note", "the", "original", "provider", "treating", "photos", "scene"}
_ASKS = re.compile(
    r"\?|\b(what|when|why|how|which|where|who|whether|tell me|explain|give me|show me|let me know|need to know|"
    r"want to know|would like to know|wondering|confirm|check|find out|look into|look up|walk me|remind me|"
    r"need|want|asking about|interested in|update on|status)\b",
    re.I,
)
_CLOSING = re.compile(
    r"\b(that'?s (all|it|everything)|that is (all|it|everything)|that'?ll be all|that will be all|all (done|set)|"
    r"i'?m (done|all set|finished)|i am (done|all set|finished)|nothing else|no more questions|no further|"
    r"goodbye|bye|no,? (thanks|thank you))\b",
    re.I,
)
_EMAIL_REQUEST = re.compile(r"\b(e-?mail|send)\b[^.?!]{0,40}\b(summary|recap|e-?mail|copy)\b", re.I)
_POSSESSION = re.compile(
    r"\b(have|has|had|got|lost|losing|missing|misplaced|can'?t find|cannot find|couldn'?t find|don'?t have|"
    r"do not have|doesn'?t have|no longer|found|received|obtained|located|on hand|with me|came in|arrived|"
    r"sent (?:it|them|me)|available)\b",
    re.I,
)
_MONTH_NAMES = ("jan", "feb", "mar", "apr", "may", "jun", "jul", "aug", "sep", "oct", "nov", "dec")


class ClaimsDomain:
    def __init__(self, repo: Repository, spec: SOPSpec | None = None) -> None:
        self.repo = repo
        self.spec = spec or load_spec(SOP_PATH)
        self.enforcer = PolicyEnforcer(self.spec, self._guards())

    # -- guards -----------------------------------------------------------------------------

    def _guards(self) -> dict[str, Any]:
        def verified(s: Any, ctx: TurnContext) -> bool:
            return bool(s.verified and s.party_id)

        def claim_selected(s: Any, ctx: TurnContext) -> bool:
            return bool(s.party_id and s.case_id and self.repo.claim(s.party_id, s.case_id))

        def recipient_is_registered(s: Any, ctx: TurnContext) -> bool:
            request = ctx.current_request
            return bool(request and s.party_id and request.recipient == self.repo.person(s.party_id)["email"])

        return {
            "identity_verified": verified,
            "identity_attempts_exhausted": lambda s, ctx: (
                s.identity_failures >= self.spec.limits.identity_attempts
            ),
            "claim_selected": claim_selected,
            "email_decided": lambda s, ctx: s.consents.get(EMAIL_ACTION) in ("granted", "declined"),
            "handoff_confirmed": lambda s, ctx: s.handoff == "requested",
            "recipient_is_registered": recipient_is_registered,
        }

    # -- engine hooks -----------------------------------------------------------------------

    def new_session(self) -> ClaimsSession:
        return ClaimsSession(sop_id=self.spec.id, sop_version=self.spec.version, phase=self.spec.initial)

    def parse_session(self, raw: str) -> ClaimsSession:
        return ClaimsSession.model_validate_json(raw)

    def recover(self, s: ClaimsSession, ctx: TurnContext) -> None:
        ctx.say("recovery")

    def terminal(self, s: ClaimsSession, ctx: TurnContext) -> None:
        ctx.say("terminal", phase=s.phase)

    def end(self, s: ClaimsSession, ctx: TurnContext) -> None:
        ctx.transition("CLOSED")

    def redact(self, s: ClaimsSession, text: str, ctx: TurnContext) -> str:
        values = list(ctx.redactions)
        if s.party_id:
            person = self.repo.person(s.party_id)
            values += [person["name"], person["email"], person["phone"], person["dob"], person["id_last4"]]
        for value in sorted({v for v in values if v}, key=len, reverse=True):
            text = re.sub(re.escape(value), "[redacted]", text, flags=re.I)
        text = _EMAIL.sub("[email]", text)
        return _DIGITS.sub("[number]", text)[:2000]

    # -- the decision -----------------------------------------------------------------------

    def decide(self, s: ClaimsSession, it: Interpretation, ctx: TurnContext) -> None:
        if s.handoff == "offered":
            if it.act == "confirm" or it.wants_human:
                s.handoff = "requested"
                ctx.emit("handoff_requested")
                ctx.transition("HANDOFF")
                ctx.say("handoff_recorded")
                return
            s.handoff = None
            s.awaiting = None
            s.off_topic_streak = 0
            if it.act == "decline":
                ctx.say("handoff_declined")
                has_content = bool(
                    it.topics
                    or (it.identity is not None and it.identity.subject == "caller")
                    or it.claim.action != "none"
                    or it.documents
                )
                if not has_content:
                    self._prompt_for_phase(s, ctx)
                    return
        if it.wants_human:
            s.handoff = "offered"
            s.awaiting = "human_confirmation"
            self._acknowledge(it, ctx)
            ctx.say("offer_human")
            return

        identity_supplied = (
            s.phase == "VERIFY_ID" and it.identity is not None and it.identity.subject == "caller"
        )
        if it.scope == "off_topic" and not identity_supplied and not it.topics:
            s.off_topic_streak += 1
            if s.off_topic_streak >= self.spec.limits.off_topic_before_handoff:
                s.handoff = "offered"
                s.awaiting = "human_confirmation"
                ctx.say("off_topic", offer_human=True)
            else:
                ctx.say("off_topic", offer_human=False)
            ctx.emit("off_topic", streak=s.off_topic_streak)
            return
        s.off_topic_streak = 0
        if it.scope == "unclear" and not (
            it.topics or identity_supplied or it.act in ("finish", "confirm", "decline")
        ):
            ctx.say("clarify")
            return
        self._acknowledge(it, ctx)
        self._remember(s, it, ctx)
        handler = {
            "VERIFY_ID": self._verify,
            "RESOLVE_INTENT": self._resolve,
            "PROCESS_CASE": self._process,
            "POST_PROCESS": self._post,
        }[s.phase]
        handler(s, it, ctx)

    def _acknowledge(self, it: Interpretation, ctx: TurnContext) -> None:
        """Acknowledge a negative emotion, at most once every three turns so it never becomes a tic."""
        s: Any = ctx.session
        recent = s.last_ack_turn is not None and s.turn - s.last_ack_turn < 3
        if it.emotion != "neutral" and not recent and not any(a.kind == "acknowledge" for a in ctx.acts):
            ctx.say("acknowledge", emotion=it.emotion)
            s.last_ack_turn = s.turn

    def _prompt_for_phase(self, s: ClaimsSession, ctx: TurnContext) -> None:
        if s.phase == "VERIFY_ID":
            self._request_identity(s, ctx, asked_claim=False)
        elif s.phase == "RESOLVE_INTENT":
            self._resolve(s, Interpretation(scope="claims"), ctx)
        elif s.phase == "POST_PROCESS":
            self._offer_email(s, ctx, again=True)
        else:
            ctx.say("anything_else")

    # -- cross-phase memory -----------------------------------------------------------------

    def _hint_from(self, it: Interpretation, text: str) -> RememberedHint:
        """Claim attributes from the reading, keeping only those the caller actually stated.

        Claim numbers, years and months must appear in the message itself; models readily fill
        a plausible year or month the caller never said, which turns a match into "not found".
        """
        ref = it.claim
        low = text.casefold()
        case_id = ref.case_id.upper() if ref.case_id and ref.case_id.upper() in text.upper() else None
        year = ref.year if ref.year and str(ref.year) in text else None
        month = ref.month if ref.month and _MONTH_NAMES[ref.month - 1] in low else None
        return RememberedHint(
            case_id=case_id,
            case_type=ref.case_type if ref.case_type in self.repo.case_types else None,
            status=ref.status if ref.status in self.repo.statuses else None,
            month=month,
            year=year,
        )

    def _find(self, party_id: str, hint: RememberedHint) -> list[dict[str, Any]]:
        """Owned claims matching a hint, relaxing the least reliable attributes before giving up."""
        h = ClaimHint(**hint.model_dump())
        for relaxed in (h, ClaimHint(h.case_id, h.case_type, h.status, h.month, None),
                        ClaimHint(h.case_id, h.case_type, h.status, None, None),
                        ClaimHint(h.case_id, h.case_type, None, None, None)):  # fmt: skip
            found = self.repo.find_claims(party_id, relaxed)
            if found or h.case_id:
                return found
        return []

    def _asks(self, text: str) -> bool:
        return bool(_ASKS.search(text))

    def _remember(self, s: ClaimsSession, it: Interpretation, ctx: TurnContext) -> None:
        if s.phase in ("VERIFY_ID", "RESOLVE_INTENT"):
            new = self._hint_from(it, ctx.text).model_dump(exclude_none=True)
            if new:
                merged = {**s.hint.model_dump(exclude_none=True), **new}
                if "case_id" in new:
                    merged = {"case_id": new["case_id"]}
                s.hint = RememberedHint(**merged)
            if self._asks(ctx.text):
                s.pending_topics = list(dict.fromkeys([*s.pending_topics, *it.topics]))[-8:]
        self._remember_documents(s, it, ctx)

    def _remember_documents(self, s: ClaimsSession, it: Interpretation, ctx: TurnContext) -> list[str]:
        """Record caller-reported document possession; returns documents newly marked missing."""
        vocab = set(self.repo.documents)
        text = ctx.text.casefold()
        newly_missing = []
        for update in it.documents:
            if update.document not in vocab:
                continue
            words = [w for w in update.document.casefold().split() if w not in _DOC_STOPWORDS]
            named = update.document.casefold() in text or any(w in text for w in words)
            only_known = len(s.documents) == 1 and update.document in s.documents
            if not (named or only_known) or not _POSSESSION.search(ctx.text):
                continue
            previous = s.documents.get(update.document)
            s.documents[update.document] = DocumentNote(status=update.status, turn=s.turn)
            ctx.emit("document_noted", document=update.document, status=update.status)
            if update.status == "missing" and (previous is None or previous.status != "missing"):
                newly_missing.append(update.document)
            elif update.status == "available":
                ctx.notes.setdefault("documents_updated", []).append(update.document)
        ctx.notes["newly_missing"] = newly_missing
        return newly_missing

    # -- VERIFY_ID --------------------------------------------------------------------------

    def _verify(self, s: ClaimsSession, it: Interpretation, ctx: TurnContext) -> None:
        values: dict[str, str] = {}
        reading = merge_readings(it.identity, ctx.text)
        if reading is not None:
            values, rejected = validate_evidence(ctx.text, reading)
            for field, reason in rejected.items():
                ctx.emit("identity_evidence_rejected", field=field, reason=reason)
            cited = reading.model_dump(exclude={"subject", "birth_date_is_callers"})
            ctx.redactions += [v for v in cited.values() if v]
        # Any turn that supplies verification details and still fails counts as an attempt.
        attempted = any(f in FIELDS for f in values)
        for field, value in values.items():
            s.evidence[field] = self.repo.match_field(field, value)
            ctx.emit("identity_evidence", field=field)
        provided = [f for f in FIELDS if f in s.evidence]
        if len(provided) >= 3:
            candidates = set.intersection(*(set(s.evidence[f]) for f in provided))
            if "policy_number" in s.evidence:
                candidates &= set(s.evidence["policy_number"])
            if len(candidates) == 1:
                s.verified, s.party_id = True, candidates.pop()
                s.evidence = {}
                s.awaiting = None
                ctx.emit("identity_verified", fields=len(provided))
                ctx.transition("RESOLVE_INTENT")
                greeting = ctx.disclose(party_facts(self.repo.person(s.party_id))["first_name"])
                ctx.say("greet", *greeting)
                self._resolve(s, it, ctx, just_verified=True)
                return
            if attempted:
                s.identity_failures += 1
                ctx.emit("identity_failed", attempt=s.identity_failures)
                if s.identity_failures >= self.spec.limits.identity_attempts:
                    ctx.transition("LOCKED")
                    ctx.say("identity_locked")
                    return
            s.awaiting = "identity"
            ctx.say("identity_failed", attempts_left=self.spec.limits.identity_attempts - s.identity_failures)
            return
        asked_claim = bool(it.topics) or self._hint_from(it, ctx.text) != RememberedHint()
        self._request_identity(s, ctx, asked_claim=asked_claim)

    def _request_identity(self, s: ClaimsSession, ctx: TurnContext, asked_claim: bool) -> None:
        provided = [f for f in FIELDS if f in s.evidence]
        options = [FIELD_LABELS[f] for f in FIELDS if f not in s.evidence]
        ctx.say(
            "request_identity",
            needed=max(1, 3 - len(provided)),
            options=options,
            first=s.awaiting != "identity",
            partial=bool(provided),
            asked_claim=asked_claim,
        )
        s.awaiting = "identity"

    # -- RESOLVE_INTENT ---------------------------------------------------------------------

    def _resolve(
        self, s: ClaimsSession, it: Interpretation, ctx: TurnContext, just_verified: bool = False
    ) -> None:
        assert s.party_id
        owned = self.repo.claims_of(s.party_id)
        if not owned:
            ctx.say("no_claims")
            return
        hint = ClaimHint(**s.hint.model_dump())
        matches = owned if hint.is_empty() else self._find(s.party_id, s.hint)
        if len(matches) == 1:
            self._select(s, matches[0], ctx)
            self._answer(s, s.pending_topics or ["overview"], ctx)
            s.pending_topics = []
            self._follow_up(s, ctx)
            return
        not_found = not hint.is_empty() and not matches
        if not_found:
            s.hint = RememberedHint()
        options = matches if len(matches) > 1 else owned
        listed = ctx.disclose(*list_facts(options))
        ctx.say("claim_choices", *listed, not_found=not_found, greeted=just_verified)
        s.awaiting = "claim_choice"

    def _select(
        self, s: ClaimsSession, claim: dict[str, Any], ctx: TurnContext, switched: bool = False
    ) -> None:
        s.case_id = claim["case_id"]
        s.hint = RememberedHint(case_id=claim["case_id"])
        s.awaiting = None
        if s.phase == "RESOLVE_INTENT":
            ctx.transition("PROCESS_CASE")
        ctx.emit("claim_selected", case_id=claim["case_id"], switched=switched)
        facts = claim_facts(self.repo, claim, ctx.today)
        ctx.say("claim_switched" if switched else "claim_found", *ctx.disclose(facts["identity"]))

    # -- PROCESS_CASE -----------------------------------------------------------------------

    def _process(self, s: ClaimsSession, it: Interpretation, ctx: TurnContext) -> None:
        assert s.party_id
        assert s.case_id
        text = ctx.text
        if self._maybe_switch(s, it, ctx):
            return
        asks = self._asks(text)
        email_request = bool(_EMAIL_REQUEST.search(text)) and not _NEGATION.search(text)
        closing = bool(_CLOSING.search(text)) and "?" not in text
        lexical_send = s.awaiting == "anything_else" and bool(_SEND.match(text)) and "send" in text.casefold()
        if email_request or lexical_send:
            if "?" in text and it.topics:
                self._answer(s, it.topics, ctx)
            self._finish(s, ctx, requested=email_request and not _EMAIL.search(text))
            return
        if (
            closing
            or (it.act == "finish" and "?" not in text)
            or (it.act == "decline" and s.awaiting == "anything_else" and not asks)
        ):
            self._finish(s, ctx)
            return
        topics = list(it.topics) if asks else []
        newly_missing: list[str] = ctx.notes.get("newly_missing", [])
        if newly_missing and "document_alternatives" not in topics:
            ctx.say("documents_noted", documents=newly_missing, status="missing")
            topics.append("document_alternatives")
        elif ctx.notes.get("documents_updated") and not topics:
            ctx.say("documents_noted", documents=ctx.notes["documents_updated"], status="available")
        if topics:
            self._answer(s, topics, ctx)
            self._follow_up(s, ctx)
        elif not ctx.notes.get("documents_updated"):
            if s.awaiting == "anything_else" and not asks:
                ctx.say("anything_else")
            else:
                ctx.say("clarify")
        else:
            self._follow_up(s, ctx)

    def _maybe_switch(self, s: ClaimsSession, it: Interpretation, ctx: TurnContext) -> bool:
        """Change the active claim only when the caller clearly means a different one of theirs."""
        assert s.party_id
        assert s.case_id
        ref = it.claim
        hint = self._hint_from(it, ctx.text)
        current = self.repo.claim(s.party_id, s.case_id)
        assert current is not None
        others = [c for c in self.repo.claims_of(s.party_id) if c["case_id"] != s.case_id]
        explicit_id = hint.case_id is not None and hint.case_id != s.case_id
        describes_current = bool(self._find(s.party_id, hint)) and current in self._find(s.party_id, hint)
        if not (ref.action == "switch" or explicit_id or (ref.action == "select" and hint != RememberedHint()
                                                            and not describes_current)):  # fmt: skip
            if ref.action == "ambiguous" and others:
                ctx.say("claim_choices", *ctx.disclose(*list_facts(others)), not_found=False, greeted=False)
                s.awaiting = "claim_choice"
                return True
            return False
        matches = (
            [c for c in self._find(s.party_id, hint) if c["case_id"] != s.case_id]
            if hint != RememberedHint()
            else []
        )
        if len(matches) == 1:
            s.documents = {}
            self._select(s, matches[0], ctx, switched=True)
            self._answer(s, it.topics if self._asks(ctx.text) and it.topics else ["overview"], ctx)
            self._follow_up(s, ctx)
            return True
        if ref.action == "switch" or explicit_id:
            not_found = explicit_id and not matches
            options = matches or others
            if options:
                ctx.say(
                    "claim_choices", *ctx.disclose(*list_facts(options)), not_found=not_found, greeted=False
                )
                s.awaiting = "claim_choice"
                return True
        return False

    def _follow_up(self, s: ClaimsSession, ctx: TurnContext) -> None:
        ctx.say("anything_else")
        s.awaiting = "anything_else"

    def _answer(self, s: ClaimsSession, topics: Sequence[str], ctx: TurnContext) -> None:
        assert s.party_id
        assert s.case_id
        claim = self.repo.claim(s.party_id, s.case_id)
        assert claim is not None
        facts = claim_facts(self.repo, claim, ctx.today)
        needed = set(claim.get("documents_needed", []))
        missing = [d for d, note in s.documents.items() if note.status == "missing" and d in needed]
        selected = select_for_topics(topics, facts, missing)
        if (
            missing
            and not any(f.cls == "guidance.alternative" for f in selected)
            and set(topics)
            & {
                "overview",
                "documents",
                "denial_reason",
                "appeal",
                "submission",
            }
        ):
            selected += [facts[f"alternative:{d}"] for d in missing if f"alternative:{d}" in facts]
        allowed = ctx.disclose(*selected)
        if allowed:
            ctx.say("facts", *allowed)
        s.discussed = list(dict.fromkeys([*s.discussed, *(f.id for f in allowed)]))[-30:]

    def _finish(self, s: ClaimsSession, ctx: TurnContext, requested: bool = False) -> None:
        """Wrap up the claim discussion. A caller who asked for the summary has already consented."""
        ctx.transition("POST_PROCESS")
        ctx.record_consent(EMAIL_ACTION, "offered")
        if requested:
            self._send_email(s, ctx)
            return
        self._offer_email(s, ctx, again=False)

    # -- POST_PROCESS -----------------------------------------------------------------------

    def _offer_email(self, s: ClaimsSession, ctx: TurnContext, again: bool) -> None:
        assert s.party_id
        email = ctx.disclose(party_facts(self.repo.person(s.party_id))["email"])
        ctx.say("offer_email", *email, again=again)
        s.awaiting = "email_decision"

    def _post(self, s: ClaimsSession, it: Interpretation, ctx: TurnContext) -> None:
        assert s.party_id
        text = ctx.text
        decision = "send" if _SEND.match(text) else "skip" if _SKIP.match(text) else it.email_decision
        if decision == "none" and _EMAIL_REQUEST.search(text) and not _NEGATION.search(text):
            decision = "send"
        registered = self.repo.person(s.party_id)["email"].casefold()
        if decision == "send" and any(m.casefold() != registered for m in _EMAIL.findall(text)):
            # Never redirect: the summary only goes to the address on file, and the caller must agree to that.
            email = ctx.disclose(party_facts(self.repo.person(s.party_id))["email"])
            ctx.say("email_registered_only", *email)
            s.awaiting = "email_decision"
            return
        if decision == "send" and not _SEND.match(text) and _NEGATION.search(text):
            ctx.say("confirm_email")
            return
        if decision == "none" and it.topics and self._asks(text):
            self._answer(s, it.topics, ctx)
            self._offer_email(s, ctx, again=True)
            return
        if decision == "send":
            self._send_email(s, ctx)
            return
        if decision == "skip":
            ctx.record_consent(EMAIL_ACTION, "declined")
            ctx.transition("COMPLETE")
            ctx.say("email_skipped")
            ctx.say("closing")
            s.awaiting = None
            return
        self._offer_email(s, ctx, again=True)

    def _send_email(self, s: ClaimsSession, ctx: TurnContext) -> None:
        assert s.party_id
        ctx.record_consent(EMAIL_ACTION, "granted")
        person = self.repo.person(s.party_id)
        ctx.perform(
            ActionRequest(
                action=EMAIL_ACTION,
                recipient=person["email"],
                subject="Your claim conversation summary",
                body=self.summary(s, ctx),
            )
        )
        # Disclose the masked address while still in POST_PROCESS; COMPLETE discloses nothing.
        email = ctx.disclose(party_facts(person)["email"])
        ctx.transition("COMPLETE")
        ctx.say("email_queued", *email, registered_only=False)
        ctx.say("closing")
        s.awaiting = None

    def summary(self, s: ClaimsSession, ctx: TurnContext) -> str:
        """Email body built only from facts this caller was told, plus next steps for their claim."""
        assert s.party_id
        assert s.case_id
        person = self.repo.person(s.party_id)
        claim = self.repo.claim(s.party_id, s.case_id)
        assert claim is not None
        facts = {f.id: f for f in claim_facts(self.repo, claim, ctx.today).values()}
        discussed = [
            facts[i].text for i in s.discussed if i in facts and i.startswith(claim["case_id"] + ":")
        ]
        discussed = [t for t in discussed if not t.startswith("your ")]
        steps: list[str] = []
        if claim.get("documents_needed"):
            sub = facts.get(f"{claim['case_id']}:submission")
            if sub:
                steps.append(sub.text)
            missing = [d for d, n in s.documents.items() if n.status == "missing"]
            steps += [self.repo.alternative_for(d) for d in missing]
        else:
            steps.append("No documents are outstanding. Contact us if you have further questions.")
        status = facts[f"{claim['case_id']}:status"].text
        lines = [
            f"Hi {person['name'].split()[0]},",
            "",
            f"Here is a summary of our conversation about your {claim_label(claim)}.",
            "",
            f"Status: {status}",
            "",
            "What we discussed:",
            *[f"- {t}" for t in (discussed or [status])],
            "",
            "Next steps:",
            *[f"- {t}" for t in steps],
        ]
        return "\n".join(lines)


def disclosed_facts(ctx: TurnContext, ids: tuple[str, ...]) -> list[Fact]:
    return [ctx.disclosed[i] for i in ids if i in ctx.disclosed]
