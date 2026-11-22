"""Grounding verifier for model-phrased replies.

The template rendering of a turn's plan is the reference: it contains exactly the facts the
policy enforcer authorized. A model-phrased reply passes only if

- every salient entity in it (claim and policy numbers, emails, dates, money amounts, other
  digits, document names, claim-state words) also appears in the reference, so nothing was
  invented or pulled from elsewhere;
- every value of every authorized fact is still present, so nothing was dropped or altered;
- consent and handoff prompts keep their explicit reply instructions;
- it does not talk about its own instructions and is not much longer than the reference.

The phrasing model never sees unauthorized data, so a check over entities and state words is
enough to catch the failure modes that matter (wrong numbers, invented dates, promises about
outcomes) without a second model in the loop.
"""

from __future__ import annotations

import re
from collections.abc import Callable, Iterable
from dataclasses import dataclass, field

from .identity import parse_date

_ID = re.compile(r"\b[A-Z]{2,4}-\d{3,}\b")
_EMAIL = re.compile(r"[\w.*+-]+@[\w-]+(?:\.[\w-]+)*\.[A-Za-z]{2,}")
_MONTHS = (
    "january|february|march|april|may|june|july|august|september|october|november|december"
    "|jan|feb|mar|apr|jun|jul|aug|sep|sept|oct|nov|dec"
)
_DATE_FORMS = [
    re.compile(r"\b\d{4}-\d{2}-\d{2}\b"),
    re.compile(r"\b\d{1,2}/\d{1,2}/\d{4}\b"),
    re.compile(rf"\b(?:{_MONTHS})\.? \d{{1,2}}(?:st|nd|rd|th)?,? \d{{4}}\b", re.I),
    re.compile(rf"\b\d{{1,2}} (?:{_MONTHS}) \d{{4}}\b", re.I),
]
_MONTH_DAY = re.compile(rf"\b(?:{_MONTHS})\.? \d{{1,2}}(?:st|nd|rd|th)?\b", re.I)
_MONEY = re.compile(
    r"(?:USD\s*|\$\s?)\d[\d,]*(?:\.\d{1,2})?|\b\d[\d,]*\.\d{2}\b(?:\s*(?:USD|dollars))?", re.I
)
_NUMBER = re.compile(r"\b\d+(?:\.\d+)?\b")
_STATE_WORDS = re.compile(
    r"\b(approved?|accepted|overturned|reversed|guarantee[sd]?|refund(?:ed)?|denied|rejected|closed|"
    r"pending|paid|settled|escalated|expedite[sd]?)\b",
    re.I,
)
_STATE_CANON = {"approve": "approved", "guarantees": "guarantee", "guaranteed": "guarantee",
                "refunded": "refund", "expedited": "expedite", "expedites": "expedite"}  # fmt: skip
_LEAK = re.compile(r"\b(reference reply|the reference|steps? list|system prompt|as an ai)\b", re.I)


def _norm_money(raw: str) -> str:
    digits = re.sub(r"[^\d.]", "", raw)
    try:
        return f"{float(digits):.2f}"
    except ValueError:
        return digits


def _norm_date(raw: str) -> str | None:
    cleaned = re.sub(r"(\d)(st|nd|rd|th)\b", r"\1", raw).replace(".", "")
    return parse_date(re.sub(r"\bsept\b", "sep", cleaned, flags=re.I))


def _month_day(raw: str) -> str | None:
    match = re.match(r"([A-Za-z]+)\.? (\d{1,2})", raw)
    return f"{match.group(1)[:3].lower()}-{int(match.group(2))}" if match else None


def _iso_month_day(iso: str) -> str:
    months = ("jan", "feb", "mar", "apr", "may", "jun", "jul", "aug", "sep", "oct", "nov", "dec")
    _, month, day = iso.split("-")
    return f"{months[int(month) - 1]}-{int(day)}"


def entities(text: str, documents: Iterable[str] = ()) -> set[tuple[str, str]]:
    """Salient entities in ``text`` as (kind, normalized value) pairs."""
    found: set[tuple[str, str]] = set()
    masked = text

    def take(pattern: re.Pattern[str], kind: str, norm: Callable[[str], str | None]) -> None:
        nonlocal masked
        for match in pattern.finditer(masked):
            raw = match.group()
            value = norm(raw)
            if value:
                found.add((kind, value))
            masked = masked.replace(raw, " " * len(raw), 1)

    take(_EMAIL, "email", str.casefold)
    take(_ID, "id", str.upper)
    for form in _DATE_FORMS:
        take(form, "date", _norm_date)
    take(_MONTH_DAY, "month_day", _month_day)
    take(_MONEY, "money", _norm_money)
    take(_NUMBER, "number", lambda r: r.lstrip("0") or "0")
    low = text.casefold()
    found.update(("document", d.casefold()) for d in documents if d.casefold() in low)
    for word in _STATE_WORDS.findall(text):
        w = word.casefold()
        found.add(("state", _STATE_CANON.get(w, w)))
    return found


def required_values(values: Iterable[str]) -> set[tuple[str, str]]:
    """Normalize authorized fact values into the entities a faithful reply must contain."""
    out: set[tuple[str, str]] = set()
    for value in values:
        if re.fullmatch(r"\d{4}-\d{2}-\d{2}", value):
            out.add(("date", value))
        elif re.fullmatch(r"\d+\.\d{2}", value):
            out.add(("money", _norm_money(value)))
        elif _ID.fullmatch(value):
            out.add(("id", value.upper()))
        elif _EMAIL.fullmatch(value):
            out.add(("email", value.casefold()))
        else:
            out.add(("text", value.casefold()))
    return out


@dataclass
class Verdict:
    ok: bool
    extra: list[str] = field(default_factory=list)
    missing: list[str] = field(default_factory=list)
    reasons: list[str] = field(default_factory=list)

    def as_dict(self) -> dict[str, object]:
        return {"ok": self.ok, "extra": self.extra, "missing": self.missing, "reasons": self.reasons}


def verify(
    reply: str,
    reference: str,
    required: Iterable[str],
    documents: Iterable[str] = (),
    max_ratio: float = 2.0,
) -> Verdict:
    docs = list(documents)
    got = entities(reply, docs)
    allowed = entities(reference, docs)
    allowed_dates = [v for k, v in allowed if k == "date"]
    # A date in the reference also licenses its month-day form and its year and day digits.
    allowed |= {("month_day", _iso_month_day(d)) for d in allowed_dates}
    allowed |= {("number", p.lstrip("0")) for d in allowed_dates for p in d.split("-")}
    extra = sorted(f"{k}:{v}" for k, v in got - allowed)
    low = reply.casefold()
    missing = []
    for kind, value in sorted(required_values(required)):
        present = value in low if kind == "text" else (kind, value) in got
        if not present:
            missing.append(f"{kind}:{value}")
    ref_low = reference.casefold()
    reasons = [
        f"lost instruction '{instruction}'"
        for instruction in ("send or skip", "yes or no")
        if instruction in ref_low and not all(w in low for w in instruction.split(" or "))
    ]
    if _LEAK.search(reply):
        reasons.append("mentions its instructions")
    if len(reply.split()) > max_ratio * len(reference.split()) + 25:
        reasons.append("too long")
    if not reply.strip():
        reasons.append("empty")
    if extra:
        reasons.append("unsupported entities")
    if missing:
        reasons.append("dropped authorized values")
    return Verdict(ok=not reasons, extra=extra, missing=missing, reasons=reasons)
