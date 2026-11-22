"""Model-phrased replies, accepted only when the grounding verifier passes.

The phrasing model receives the deterministic reference reply and the plan behind it, never
the caller's raw message or any record data beyond the authorized statements. Its job is tone
and flow. If the call fails, times out or produces a reply the verifier rejects, the reference
is sent instead, so phrasing can only change how an answer sounds, never what it says.
"""

from __future__ import annotations

import json
from typing import Any

from ..llm.base import LLMRequest, LLMUnavailable
from ..llm.client import LLMClient
from ..obs import metrics
from ..sop.engine import Rendered
from ..sop.policy import TurnContext
from .templates import render_template
from .verifier import verify

PHRASING_PROMPT = """You are the voice of an insurance claims support assistant. Rewrite reference_reply as one \
warm, natural, concise message to the caller.

Rules:
- Keep the meaning of every sentence. Copy every claim number, email address, date and dollar amount exactly \
as written in reference_reply.
- Do not add any fact, number, date, name, document, outcome, promise, advice or contact detail that is not in \
reference_reply. Never say a claim will be approved, paid or reconsidered.
- Do not ask for anything reference_reply does not ask for. If it asks the caller to reply "send or skip" or \
"yes or no", keep those exact words.
- Keep the closing question, if any, at the end.
- Plain text only: no markdown, no lists, no greeting line, no sign-off name.
- Reply with the message text only."""

SKIP_LLM = {"terminal", "recovery", "identity_locked"}


class LLMRenderer:
    def __init__(self, client: LLMClient, documents: list[str], max_tokens: int = 400) -> None:
        self.client = client
        self.documents = documents
        """Every document name the domain knows, so an invented requirement is caught."""
        self.max_tokens = max_tokens

    def _plan(self, ctx: TurnContext) -> list[dict[str, Any]]:
        steps = []
        for act in ctx.acts:
            step: dict[str, Any] = {"step": act.kind}
            statements = [ctx.disclosed[i].text for i in act.facts if i in ctx.disclosed]
            if statements:
                step["statements"] = statements
            safe = {k: v for k, v in act.params.items() if isinstance(v, (int, str, bool, list))}
            if safe:
                step["params"] = safe
            steps.append(step)
        return steps

    async def render(self, session: Any, ctx: TurnContext) -> Rendered:
        reference = render_template(ctx)
        if not ctx.acts or any(a.kind in SKIP_LLM for a in ctx.acts):
            return Rendered(text=reference, mode="template")
        required = [v for f in ctx.disclosed.values() for v in f.values]
        payload = {"reference_reply": reference, "plan": self._plan(ctx)}
        request = LLMRequest(
            stage="phrase",
            system=PHRASING_PROMPT,
            user=json.dumps(payload, ensure_ascii=False),
            max_tokens=self.max_tokens,
        )
        try:
            response = await self.client.complete(request, ctx.deadline, ctx.usage)
        except (LLMUnavailable, ValueError):
            metrics.VERIFIER.labels(outcome="unavailable").inc()
            return Rendered(text=reference, mode="template_fallback")
        text = " ".join(response.text.strip().strip('"').split())
        verdict = verify(text, reference, required, self.documents)
        metrics.VERIFIER.labels(outcome="pass" if verdict.ok else "reject").inc()
        if verdict.ok:
            return Rendered(text=text, mode="llm", verifier=verdict.as_dict())
        ctx.emit("phrasing_rejected", reasons=verdict.reasons, extra=verdict.extra, missing=verdict.missing)
        return Rendered(text=reference, mode="llm_rejected", verifier=verdict.as_dict())
