"""The fact catalog: every claim statement the agent is allowed to make, built from one record.

Raw records never reach a model or a reply. A claim is converted into a fixed set of approved
statements, each tagged with its class (for the SOP's disclosure rules), its owner (for the
ownership guard) and its salient values (for the grounding verifier). Missing data becomes an
explicit ``absence.*`` statement, so no answer is ever composed by substituting a nearby field.
"""

from __future__ import annotations

from collections.abc import Iterable
from datetime import date
from typing import Any, Literal, get_args

from ..sop.state import Fact
from .repository import Repository

Topic = Literal[
    "overview",
    "status",
    "denial_reason",
    "documents",
    "appeal",
    "appeal_deadline",
    "filing_date",
    "payment",
    "expected_reimbursement",
    "allowed_amount",
    "submission",
    "submission_timing",
    "processing_time",
    "file_format",
    "receipt_confirmation",
    "document_alternatives",
    "decision_date",
    "notification",
    "other",
]
TOPICS: tuple[str, ...] = get_args(Topic)

TOPIC_DESCRIPTIONS = {
    "overview": "a broad request to explain, review or help with the claim, without a specific fact",
    "status": "current status or outcome of the claim",
    "denial_reason": "why the claim was denied",
    "documents": "which documents or records are required or missing",
    "appeal": "how to challenge, appeal or get a denial reconsidered (evidence and steps)",
    "appeal_deadline": "the last date to appeal, or whether the appeal window is still open",
    "filing_date": "when the claim was filed or received",
    "payment": "the amount already paid out",
    "expected_reimbursement": "the expected payout",
    "allowed_amount": "the allowed or maximum covered amount",
    "submission": "where or how to send or upload documents",
    "submission_timing": "how soon documents should be sent",
    "processing_time": "how long review takes after documents are received",
    "file_format": "file format, scan or photo quality requirements",
    "receipt_confirmation": "how the caller will know the documents arrived",
    "document_alternatives": "what to do if a required document is unavailable or lost",
    "decision_date": "the date the denial decision was made",
    "notification": "whether or when a decision notice was sent",
    "other": "a claim question none of the other topics cover",
}


def human_date(iso: str) -> str:
    d = date.fromisoformat(iso)
    return f"{d.strftime('%B')} {d.day}, {d.year}"


def money(amount: str) -> str:
    return f"${float(amount):,.2f}"


def _join(items: list[str]) -> str:
    if len(items) <= 1:
        return "".join(items)
    return ", ".join(items[:-1]) + (", and " if len(items) > 2 else " and ") + items[-1]


def mask_email(email: str) -> str:
    local, _, domain = email.partition("@")
    return f"{local[0]}*****@{domain}"


def claim_label(claim: dict[str, Any]) -> str:
    return f"{claim['case_type']} claim {claim['case_id']}"


def claim_facts(repo: Repository, claim: dict[str, Any], today: date) -> dict[str, Fact]:
    """All approved statements for one claim, keyed by fact id."""
    owner, case = claim["party_id"], claim["case_id"]
    docs: list[str] = list(claim.get("documents_needed", []))
    facts: dict[str, Fact] = {}

    def add(fid: str, cls: str, text: str, *values: str) -> None:
        facts[fid] = Fact(id=f"{case}:{fid}", cls=cls, text=text, owner=owner, case_id=case, values=values)

    add("identity", "claim.identity", f"your {claim_label(claim)}", case)
    add("status", "claim.status", f"Claim {case} is currently {claim['status']}.", case, claim["status"])
    filed = claim["created_at"]
    add("filing_date", "claim.filing_date", f"It was filed on {human_date(filed)}.", filed)
    if claim.get("denial_reason"):
        add("denial_reason", "claim.denial_reason", f"It was denied because {claim['denial_reason']}.", *docs)
    else:
        add(
            "denial_reason",
            "absence.denial_reason",
            f"Claim {case} is not recorded as denied, so there is no denial reason on file.",
            case,
        )
    if docs:
        add(
            "documents",
            "claim.documents",
            f"The documents still needed for this claim are the {_join(docs)}.",
            *docs,
        )
    else:
        add("documents", "absence.documents", "There are no outstanding documents on this claim.")
    if claim.get("appeal_deadline"):
        deadline = claim["appeal_deadline"]
        text = f"The recorded appeal deadline is {human_date(deadline)}."
        if date.fromisoformat(deadline) < today:
            text += (
                " That date has passed, so I can't confirm whether a late appeal would be accepted;"
                " a representative can review the options."
            )
        add("appeal_deadline", "claim.appeal_deadline", text, deadline)
    else:
        add("appeal_deadline", "absence.appeal_deadline", "No appeal deadline is recorded for this claim.")
    for key, label, fid in (
        ("net_pay", "amount paid so far", "payment"),
        ("expected_reimbursement_amount", "expected reimbursement", "expected_reimbursement"),
        ("allowed_max_amount", "allowed maximum", "allowed_amount"),
    ):
        if claim.get(key) is not None:
            add(fid, f"claim.{fid}", f"The {label} is {money(claim[key])}.", claim[key])
        else:
            add(fid, f"absence.{fid}", f"The record does not include the {label}.")

    for topic, fid in (
        ("submission_method", "submission"),
        ("submission_timing", "submission_timing"),
        ("processing_time_after_submission", "processing_time"),
        ("file_format_requirements", "file_format"),
        ("receipt_confirmation", "receipt_confirmation"),
        ("missing_required_material_alternatives", "alternatives_general"),
    ):
        guidance = repo.followup(topic, claim)
        if guidance:
            add(fid, f"guidance.{fid}", guidance, *docs)
        else:
            add(
                fid,
                f"absence.{fid}",
                "There are no outstanding documents on this claim, so nothing needs to be sent.",
            )
    for doc in docs:
        detail = repo.document_guidance(doc)
        if detail:
            add(f"format:{doc}", "guidance.document_format", detail)
        add(f"alternative:{doc}", "guidance.alternative", repo.alternative_for(doc))

    add(
        "decision_date",
        "absence.decision_date",
        "The record doesn't include the date the denial decision was made, and the filing date and"
        " appeal deadline are not decision dates.",
    )
    add(
        "notification",
        "absence.notification",
        "There is no notification history on the record, so I can't confirm whether, when or how a"
        " decision notice was sent.",
    )
    add(
        "other",
        "absence.other",
        "The claim record doesn't answer that question. A representative would need to look into it.",
    )
    return facts


def list_facts(claims: Iterable[dict[str, Any]]) -> list[Fact]:
    return [
        Fact(
            id=f"list:{c['case_id']}",
            cls="claims.list",
            text=f"a {c['case_type']} claim {c['case_id']} filed {human_date(c['created_at'])} ({c['status']})",
            owner=c["party_id"],
            case_id=c["case_id"],
            values=(c["case_id"], c["created_at"]),
        )
        for c in claims
    ]


def party_facts(person: dict[str, Any]) -> dict[str, Fact]:
    first = person["name"].split()[0]
    masked = mask_email(person["email"])
    owner = person["party_id"]
    return {
        "first_name": Fact(
            id="party:first_name", cls="party.first_name", text=first, owner=owner, values=(first,)
        ),
        "email": Fact(id="party:email", cls="party.email", text=masked, owner=owner, values=(masked,)),
    }


def select_for_topics(
    topics: Iterable[str], facts: dict[str, Fact], missing_documents: Iterable[str] = ()
) -> list[Fact]:
    """Facts answering the requested topics, in a stable order without duplicates."""
    keys: list[str] = []
    for topic in topics:
        if topic == "overview":
            keys += (
                ["status", "denial_reason"] if facts["denial_reason"].cls.startswith("claim.") else ["status"]
            )
            if facts["documents"].cls.startswith("claim."):
                keys.append("documents")
        elif topic == "appeal":
            keys += ["documents", "appeal_deadline", "submission"]
        elif topic == "file_format":
            keys += ["file_format", *[k for k in facts if k.startswith("format:")]]
        elif topic == "document_alternatives":
            missing = [f"alternative:{d}" for d in missing_documents if f"alternative:{d}" in facts]
            keys += missing or [k for k in facts if k.startswith("alternative:")] or ["alternatives_general"]
        elif topic in facts:
            keys.append(topic)
        else:
            keys.append("other")
    # A denial reason that already names every needed document makes the document list redundant.
    if (
        "denial_reason" in keys
        and "documents" in keys
        and facts["denial_reason"].cls == "claim.denial_reason"
    ):
        reason = facts["denial_reason"].text.casefold()
        if all(v.casefold() in reason for v in facts["documents"].values):
            keys.remove("documents")
    return [facts[k] for k in dict.fromkeys(keys) if k in facts]
