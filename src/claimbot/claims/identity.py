"""Identity evidence at the model-to-application trust boundary.

The model never types an identity value. For each field the JSON schema it must follow is an
enum of the literal spans of the current message that are format-valid for that field, so the
only thing the model can do is point at text the caller actually wrote. Local checks then reject
quoted material, date fragments and spans counted twice, and the repository does the matching.

Session state keeps no raw identity values: each supplied field is stored as the set of party
ids whose record matches it. Verification is a non-empty single-party intersection across at
least three fields, which is equivalent to matching the raw values without retaining them.
"""

from __future__ import annotations

import re
from datetime import datetime

from pydantic import BaseModel, ConfigDict, Field

FIELDS = ("name", "dob", "phone", "email", "id_last4")
"""The five verification fields; ``policy_number`` narrows the search but never counts."""

FIELD_LABELS = {
    "name": "full name",
    "dob": "date of birth",
    "phone": "phone number",
    "email": "email address",
    "id_last4": "last four digits of your SSN or member ID",
}

_DATE_FORMATS = (
    "%Y-%m-%d",
    "%Y/%m/%d",
    "%m/%d/%Y",
    "%m-%d-%Y",
    "%d/%m/%Y",
    "%B %d, %Y",
    "%B %d %Y",
    "%b %d, %Y",
    "%b %d %Y",
    "%d %B %Y",
    "%d %b %Y",
    "%B %dst, %Y",
    "%B %dnd, %Y",
    "%B %drd, %Y",
    "%B %dth, %Y",
)
_EMAIL = re.compile(r"[\w.+-]+@[\w-]+(?:\.[\w-]+)*\.[A-Za-z]{2,}")


class IdentityReading(BaseModel):
    """What the model reports about identity details in the current message."""

    model_config = ConfigDict(extra="forbid", populate_by_name=True)

    subject: str = Field(
        description="Whose details are asserted: caller, someone_else, hypothetical, or none."
    )
    name: str | None = None
    birth_date_quote: str | None = None
    phone: str | None = None
    email: str | None = None
    id_last4: str | None = None
    policy_number: str | None = None
    birth_date_is_callers: bool = Field(
        default=False, description="True only if the quoted date is given as the caller's own birth date."
    )


def parse_date(value: str) -> str | None:
    """ISO date for a date string, or None.

    Numeric dates follow US convention (MM/DD/YYYY); day-first is accepted only when month-first
    is impossible (``15/03/1985``). Formats with month names are unambiguous.
    """
    cleaned = " ".join(value.replace(",", ", ").split()).replace(" ,", ",").strip(" .")
    cleaned = re.sub(r",\s*,", ",", cleaned)
    for fmt in _DATE_FORMATS:
        try:
            return datetime.strptime(cleaned, fmt).date().isoformat()
        except ValueError:
            continue
    return None


def normalize(field: str, source: str) -> str | None:
    """Canonical value for a format-valid span, or None if the span cannot be that field."""
    value = source.strip()
    if field == "dob":
        return parse_date(value)
    if field == "id_last4":
        return value if re.fullmatch(r"\d{4}", value) else None
    if field == "phone":
        if not re.fullmatch(r"\+?[\d(). -]+", value):
            return None
        digits = re.sub(r"\D", "", value)
        if len(digits) == 11 and digits.startswith("1"):
            digits = digits[1:]
        return digits if len(digits) == 10 else None
    if field == "email":
        return value.casefold() if _EMAIL.fullmatch(value) else None
    if field == "policy_number":
        return value.upper() if re.fullmatch(r"POL-\d+", value, re.I) else None
    if field == "name":
        words = value.split()
        if not 2 <= len(words) <= 4:
            return None
        if not all(re.fullmatch(r"[^\W\d_][^\W\d_'’.-]*(?:['’.-][^\W\d_]+)*\.?", w) for w in words):
            return None
        return " ".join(w.casefold() for w in words)
    raise ValueError(field)


_NAME_STOPWORDS = {
    "my", "name", "is", "i", "am", "im", "i'm", "this", "the", "and", "dob", "date", "of", "birth",
    "email", "phone", "number", "ssn", "last", "four", "digits", "id", "it's", "its", "hi", "hello",
    "calling", "about", "claim", "here", "yes", "no", "born", "on", "at", "me", "call",
}  # fmt: skip


def candidate_spans(text: str, max_chars: int = 1200) -> dict[str, list[str]]:
    """Format-valid literal spans of ``text`` for each field (the evidence enums)."""
    text = text[:max_chars]
    tokens = list(re.finditer(r"\S+", text))
    spans: list[str] = []
    for i, start in enumerate(tokens):
        for end in tokens[i : i + 5]:
            span = text[start.start() : end.end()].strip(" ,.;:!?()[]\"“”'")
            if span and span not in spans:
                spans.append(span)
    out: dict[str, list[str]] = {}
    for field in (*FIELDS, "policy_number"):
        values = [s for s in spans if normalize(field, s) is not None]
        if field == "name":
            values = [s for s in values if not {w.casefold().strip(".") for w in s.split()} & _NAME_STOPWORDS]
        out[field] = values
    # A four-digit run that only ever appears inside a date is a year, not an ID suffix.
    date_ranges = [
        (m.start(), m.start() + len(d)) for d in out["dob"] for m in re.finditer(re.escape(d), text)
    ]
    out["id_last4"] = [
        v
        for v in out["id_last4"]
        if any(
            not any(a <= m.start() and m.end() <= b for a, b in date_ranges)
            for m in re.finditer(rf"(?<!\d){v}(?!\d)", text)
        )
    ]
    return out


def is_bare_list(text: str, sources: list[str]) -> bool:
    """True when the message is only the supplied values and separators (an unlabeled list)."""
    masked = text
    for source in sorted(sources, key=len, reverse=True):
        masked = masked.replace(source, " " * len(source))
    return re.fullmatch(r"[\s,;:/|().!?-]*", masked) is not None


def validate_evidence(text: str, reading: IdentityReading) -> tuple[dict[str, str], dict[str, str]]:
    """Canonical values whose cited spans survive local provenance checks, and why others failed.

    A cited span is rejected when it is not an exact whole value of the message, sits inside
    quotation marks, is a fragment of a date or identifier, overlaps a span already used for
    another field, or is not format-valid. A rejected field simply does not count; the other
    fields in the same message still do. Nothing counts when the model reports the details
    belong to someone else or are hypothetical.
    """
    if reading.subject != "caller":
        return {}, {}
    cited = {
        "name": reading.name,
        "dob": reading.birth_date_quote,
        "phone": reading.phone,
        "email": reading.email,
        "id_last4": reading.id_last4,
        "policy_number": reading.policy_number,
    }
    quoted = [(m.start(), m.end()) for m in re.finditer(r"\"[^\"\n]*\"|“[^”\n]*”", text)]
    taken: list[tuple[int, int]] = []
    values: dict[str, str] = {}
    rejected: dict[str, str] = {}
    for field, source in cited.items():
        if not source:
            continue
        value = normalize(field, source)
        if value is None:
            rejected[field] = "not a valid value"
            continue
        reason = "not an exact whole value of the message"
        for match in re.finditer(re.escape(source), text):
            start, end = match.span()
            if (start and text[start - 1].isalnum()) or (end < len(text) and text[end].isalnum()):
                continue
            if any(a < start and end < b for a, b in quoted):
                reason = "quoted material"
                continue
            if field == "id_last4" and (
                (start and text[start - 1] in "-/") or (end < len(text) and text[end] in "-/")
            ):
                reason = "part of a date or identifier"
                continue
            if any(start < b and end > a for a, b in taken):
                reason = "overlaps another field"
                continue
            taken.append((start, end))
            values[field] = value
            break
        else:
            rejected[field] = reason
    if (
        "dob" in values
        and not reading.birth_date_is_callers
        and not is_bare_list(text, [v for v in cited.values() if v])
    ):
        values.pop("dob")
        rejected["dob"] = "not stated as the caller's birth date"
    return values, rejected
