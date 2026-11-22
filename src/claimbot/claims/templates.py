"""Deterministic rendering of a response plan. Also the reference the grounding verifier uses.

Every claim-derived word in the output comes from a fact the policy enforcer authorized this
turn; act parameters carry only non-sensitive values (counts, field labels, emotions).
"""

from __future__ import annotations

from typing import Any

from ..sop.policy import Act, TurnContext

ACK = {
    "angry": "I hear how upsetting this has been, and I want to help sort it out.",
    "frustrated": "I understand this has been frustrating.",
    "anxious": "I know this is stressful. Let's take it one step at a time.",
    "sad": "I'm sorry you're dealing with this.",
    "confused": "Let me help make this clearer.",
    "overwhelmed": "We can go one step at a time.",
}

TERMINAL = {
    "COMPLETE": "This conversation is complete. Please start a new chat if you need more help.",
    "HANDOFF": "A human representative has been requested for this conversation. Please start a new chat"
    " if you need anything else in the meantime.",
    "LOCKED": "I couldn't verify your identity in this chat, so I can't access claim details. Please contact"
    " support through your member portal.",
    "CLOSED": "This chat has ended. Please start a new chat if you need more help.",
}


def _join(items: list[str], conj: str = "or") -> str:
    if len(items) <= 1:
        return "".join(items)
    return ", ".join(items[:-1]) + f", {conj} " + items[-1]


def render_act(act: Act, ctx: TurnContext) -> str:
    facts = [ctx.disclosed[i].text for i in act.facts if i in ctx.disclosed]
    p: dict[str, Any] = act.params
    match act.kind:
        case "acknowledge":
            return ACK.get(p["emotion"], "")
        case "greet":
            return f"Thanks, {facts[0]}, you're verified." if facts else "Thanks, you're verified."
        case "request_identity":
            n = p["needed"]
            options = _join(p["options"])
            noun = "detail" if n == 1 else "details"
            if p["first"]:
                lead = "I can help with that." if p["asked_claim"] else "I can help with your claim."
                more = " more" if p["partial"] else ""
                return (
                    f"{lead} First I need to verify your identity. Please share {n}{more} of the following:"
                    f" your {options}."
                )
            return f"Thanks. I still need {n} more verification {noun}. You can use your {options}."
        case "identity_failed":
            left = p["attempts_left"]
            tail = f" You have {left} more {'try' if left == 1 else 'tries'}." if left > 0 else ""
            return (
                "I couldn't verify your identity with those details. Please double-check them or use"
                f" different ones.{tail}"
            )
        case "identity_locked":
            return TERMINAL["LOCKED"]
        case "claim_found":
            return f"I found {facts[0]}." if facts else ""
        case "claim_switched":
            return f"Okay, let's look at {facts[0]} instead." if facts else ""
        case "claim_choices":
            lead = "I couldn't find a claim matching that description. " if p.get("not_found") else ""
            if len(facts) == 1:
                return f"{lead}I see {facts[0]} on your account. Is that the one you mean?"
            listing = "; ".join(facts)
            return f"{lead}I see {len(facts)} claims on your account: {listing}. Which one would you like to discuss?"
        case "no_claims":
            return "I don't see any claims on your account."
        case "facts":
            return " ".join(facts)
        case "documents_noted":
            docs = _join(list(p["documents"]), "and")
            if p["status"] == "missing":
                return f"Thanks for letting me know you don't have the {docs}."
            return f"Thanks, I've noted that you have the {docs}."
        case "anything_else":
            return "Is there anything else I can help you with on this claim?"
        case "clarify":
            return "Could you tell me a little more about what you need help with on your claim?"
        case "offer_email":
            email = facts[0] if facts else "your registered email address"
            if p.get("again"):
                return f"Would you like me to email a summary to {email}? Please reply send or skip."
            return (
                "Before you go, would you like an email summary of our conversation, the claim status and"
                f" next steps sent to {email}? Please reply send or skip."
            )
        case "email_registered_only":
            email = facts[0] if facts else "your registered email address"
            return (
                "For security, I can only send the summary to the email address on file, "
                f"{email}. Would you like me to send it there? Please reply send or skip."
            )
        case "confirm_email":
            return "Just to confirm, should I send the summary email? Please reply send or skip."
        case "email_queued":
            email = facts[0] if facts else "your registered email address"
            note = (
                " For security, I can only send it to the email address on file."
                if p.get("registered_only")
                else ""
            )
            return f"Done. I've sent the summary to {email}.{note}"
        case "email_skipped":
            return "No problem, I won't send an email."
        case "closing":
            return "Thanks for contacting us, and take care."
        case "off_topic":
            if p["offer_human"]:
                return (
                    "I can only help with insurance claims. Would you like me to connect you with a human"
                    " representative? Please reply yes or no."
                )
            return "I can only help with insurance claims. What can I help you with on your claim?"
        case "offer_human":
            return (
                "I can connect you with a human representative. Would you like that? Please reply yes or no."
            )
        case "handoff_recorded":
            return (
                "I've requested a human representative for you. Someone from the claims team will follow up."
            )
        case "handoff_declined":
            return "Okay, let's continue."
        case "recovery":
            return (
                "Sorry, I had trouble understanding that, and nothing has changed. Could you rephrase it, or ask"
                " for a human representative?"
            )
        case "terminal":
            return TERMINAL.get(p["phase"], TERMINAL["COMPLETE"])
        case _:
            raise ValueError(f"unknown act {act.kind!r}")


def render_template(ctx: TurnContext) -> str:
    parts = [render_act(act, ctx) for act in ctx.acts]
    return " ".join(p for p in parts if p).strip() or render_act(Act("clarify"), ctx)
