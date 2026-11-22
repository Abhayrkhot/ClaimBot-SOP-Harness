"""Response rendering: deterministic templates, or model phrasing checked by the verifier."""

from __future__ import annotations

from ..sop.engine import Rendered
from ..sop.policy import TurnContext
from .templates import render_template


class TemplateRenderer:
    async def render(self, session: object, ctx: TurnContext) -> Rendered:
        return Rendered(text=render_template(ctx), mode="template")
