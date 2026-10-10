"""Validate identity evidence at the model-to-application trust boundary.

The model may point to exact substrings in the current message, but local code
checks attribution, format, and provenance before accepting a candidate. The
repository performs the actual three-of-five match, so model output alone can
never verify a caller.
"""
import re
from datetime import datetime
from typing import Annotated, Literal

from pydantic import BaseModel, ConfigDict, Field

IdentityField = Literal['name', 'dob', 'phone', 'email', 'id_last4', 'policy_number']


class IdentityCandidate(BaseModel):
    model_config = ConfigDict(extra='forbid')
    field: IdentityField
    source: str = Field(min_length=1, max_length=150, description='Exact value substring copied from the current message, without its label.')


class IdentityExtraction(BaseModel):
    model_config = ConfigDict(extra='forbid')
    candidates: list[IdentityCandidate] = Field(default_factory=list, max_length=12)


class IdentitySlots(BaseModel):
    model_config = ConfigDict(extra='forbid', populate_by_name=True)
    subject: Literal['caller', 'other', 'hypothetical', 'unclear'] = Field(description='Whose asserted details are these? An unqualified verification reply is caller; explicitly someone else is other.')
    name: Annotated[str, Field(pattern=r'^[^\d@,]+\s+[^\d@,]+$')] | None = Field(default=None, description='Exact full name with at least two words, or null. Never use a number or email username as a name.')
    dob: str | None = Field(default=None, alias='birth_date_quote', description='Copy the birth date EXACTLY as written, including month words and punctuation. Do not convert or reformat it. Null if absent.')
    phone: Annotated[str, Field(min_length=10, max_length=40)] | None = Field(default=None, description='Exact full phone number with at least 10 digits, or null. Four digits are never a phone number.')
    email: str | None = Field(default=None, description='Exact email address supplied by the caller, or null.')
    id_last4: Annotated[str, Field(pattern=r'^\d{4}$', min_length=4, max_length=4)] | None = Field(default=None, description='Exact separate four ID/SSN digits supplied by the caller, or null.')
    policy_number: str | None = Field(default=None, description='Exact policy number supplied by the caller, or null.')


class BirthDateUse(BaseModel):
    model_config = ConfigDict(extra='forbid')
    is_caller_birth_date: bool


class IdentityAssertion(BaseModel):
    model_config = ConfigDict(extra='forbid')
    asserted_by_caller: bool


def normalize(field, source):
    """Normalize an evidence substring only after its format is locally valid."""
    value = source.strip()
    if field == 'dob':
        dates = set()
        for fmt in ('%Y-%m-%d', '%Y/%m/%d', '%m/%d/%Y', '%d/%m/%Y',
                    '%B %d, %Y', '%B %d %Y', '%b %d, %Y', '%b %d %Y', '%d %B %Y', '%d %b %Y'):
            try: dates.add(datetime.strptime(value, fmt).date().isoformat())
            except ValueError: pass
        return dates.pop() if len(dates) == 1 else None
    if field == 'id_last4': return value if re.fullmatch(r'\d{4}', value) else None
    if field == 'phone':
        if not re.fullmatch(r'[+\d(). -]+', value): return None
        digits = re.sub(r'\D', '', value)
        return digits if len(digits) == 10 or (len(digits) == 11 and digits.startswith('1')) else None
    if field == 'email': return value if re.fullmatch(r'[\w.+-]+@[\w.-]+\.[A-Za-z]{2,}', value) else None
    if field == 'policy_number': return value.upper() if re.fullmatch(r'POL-\d+', value, re.I) else None
    return ' '.join(value.split()) if len(value.split()) >= 2 and all(c.isalpha() or c in " '-’" for c in value) else None


def evidence_schema(text, schema):
    """Constrain model candidate values to literal substrings of this message."""
    """Constrain generated values to type-compatible spans of this message, never a record."""
    tokens = list(re.finditer(r'\S+', text))
    spans = list(dict.fromkeys(text[a.start():b.end()].strip(' ,.;:[]"“”')
                              for i, a in enumerate(tokens) for b in tokens[i:i + 5]))
    for field in ('name', 'dob', 'phone', 'email', 'id_last4', 'policy_number'):
        key = 'birth_date_quote' if field == 'dob' else field
        values = [span for span in spans if len(span) <= 150 and normalize(field, span) is not None]
        description = schema['properties'][key].get('description', '')
        schema['properties'][key] = ({'anyOf': [{'type': 'string', 'enum': values}, {'type': 'null'}]}
                                     if values else {'type': 'null'})
        schema['properties'][key]['description'] = description
    return schema


def is_identity_tuple(text, sources):
    """Detect compact unlabeled identity lists that need attribution checking."""
    """An unlabeled verification list contains only supplied values and separators."""
    if not sources:
        return False
    masked = text
    for source in sorted(sources.values(), key=len, reverse=True):
        masked = masked.replace(source, ' ' * len(source))
    return re.fullmatch(r'[\s,;:/|().!?-]*', masked) is not None


def validated_candidates(text, extraction):
    """Return canonical candidates whose cited evidence survives local checks."""
    result, spans = {}, []
    quoted_spans = [(match.start(), match.end()) for match in re.finditer(r'"[^"\n]*"|“[^”\n]*”', text)]
    for candidate in extraction.candidates:
        start = text.find(candidate.source)
        end = start + len(candidate.source)
        if start < 0 or (start and text[start - 1].isalnum()) or (end < len(text) and text[end].isalnum()):
            raise ValueError('Identity evidence must be an exact whole value in the message')
        if any(a < start and end < b for a, b in quoted_spans):
            raise ValueError('Quoted material requires direct unquoted identity confirmation')
        field = candidate.field
        value = normalize(field, candidate.source)
        if value is None:
            result[field] = None
            continue
        if field == 'id_last4' and ((start and text[start - 1] in '-/') or (end < len(text) and text[end] in '-/')):
            raise ValueError('A date or identifier component is not separate identity evidence')
        if any(start < b and end > a and field != old_field for a, b, old_field in spans):
            raise ValueError('One identity span cannot count as multiple fields')
        result[field] = value if field not in result or result[field] == value else None
        spans.append((start, end, field))
    return result
