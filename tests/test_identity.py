from __future__ import annotations

import pytest

from claimbot.claims.identity import (
    IdentityReading,
    candidate_spans,
    is_bare_list,
    normalize,
    parse_date,
    validate_evidence,
)


@pytest.mark.parametrize(
    "raw,iso",
    [
        ("1985-03-15", "1985-03-15"),
        ("03/15/1985", "1985-03-15"),
        ("March 15, 1985", "1985-03-15"),
        ("15 March 1985", "1985-03-15"),
        ("Mar 15 1985", "1985-03-15"),
        ("March 15th, 1985", "1985-03-15"),
        ("03/04/1985", "1985-03-04"),  # numeric dates are month-first (US)
        ("15/03/1985", "1985-03-15"),  # day-first only when month-first is impossible
        ("1985", None),
    ],
)
def test_parse_date(raw: str, iso: str | None) -> None:
    assert parse_date(raw) == iso


def test_normalize_fields() -> None:
    assert normalize("phone", "(650) 521-2836") == "6505212836"
    assert normalize("phone", "+1 650 521 2836") == "6505212836"
    assert normalize("phone", "4472") is None
    assert normalize("email", "Margaret@Email.com") == "margaret@email.com"
    assert normalize("name", "Margaret Chen") == "margaret chen"
    assert normalize("name", "O'Brien-Smith Jo") == "o'brien-smith jo"
    assert normalize("name", "Margaret") is None
    assert normalize("name", "4472 Chen") is None
    assert normalize("id_last4", "4472") == "4472"
    assert normalize("policy_number", "pol-9921") == "POL-9921"


def test_candidate_spans_are_literal_and_typed() -> None:
    text = "Hi, Margaret Chen here, born March 15, 1985, last four 4472, margaret@email.com"
    spans = candidate_spans(text)
    assert "Margaret Chen" in spans["name"]
    assert "March 15, 1985" in spans["dob"]
    assert spans["id_last4"] == ["4472"]
    assert spans["email"] == ["margaret@email.com"]
    assert all(s in text for values in spans.values() for s in values)
    assert not any("born" in s.casefold() for s in spans["name"])


def reading(**kw: object) -> IdentityReading:
    return IdentityReading(subject=kw.pop("subject", "caller"), **kw)  # type: ignore[arg-type]


def test_validate_accepts_exact_values() -> None:
    text = "My name is Margaret Chen, DOB 1985-03-15"
    values, rejected = validate_evidence(
        text, reading(name="Margaret Chen", birth_date_quote="1985-03-15", birth_date_is_callers=True)
    )
    assert values == {"name": "margaret chen", "dob": "1985-03-15"} and rejected == {}


def test_validate_rejects_non_literal() -> None:
    values, rejected = validate_evidence("My name is Margaret", reading(name="Margaret Chen"))
    assert values == {} and "exact whole value" in rejected["name"]


def test_validate_rejects_quoted_examples() -> None:
    text = 'The form says "Margaret Chen" as an example'
    assert validate_evidence(text, reading(name="Margaret Chen")) == ({}, {"name": "quoted material"})


def test_validate_rejects_date_fragment_as_last4() -> None:
    values, rejected = validate_evidence("born 1985-03-15", reading(id_last4="1985"))
    assert values == {} and rejected == {"id_last4": "part of a date or identifier"}


def test_validate_rejects_overlap_but_keeps_other_fields() -> None:
    values, rejected = validate_evidence("6505212836", reading(phone="6505212836", id_last4="2836"))
    assert values == {"phone": "6505212836"} and "id_last4" in rejected


def test_repeated_value_uses_a_non_overlapping_occurrence() -> None:
    text = "phone 650-521-4472, last four 4472"
    values, _ = validate_evidence(text, reading(phone="650-521-4472", id_last4="4472"))
    assert values == {"phone": "6505214472", "id_last4": "4472"}


def test_someone_else_yields_nothing() -> None:
    assert validate_evidence(
        "My wife Margaret Chen", reading(subject="someone_else", name="Margaret Chen")
    ) == ({}, {})


def test_event_date_is_not_birth_date() -> None:
    text = "My name is Margaret Chen and my claim was filed 2026-01-12"
    values, rejected = validate_evidence(
        text, reading(name="Margaret Chen", birth_date_quote="2026-01-12", birth_date_is_callers=False)
    )
    assert values == {"name": "margaret chen"} and "birth date" in rejected["dob"]


def test_bare_list_keeps_unlabeled_birth_date() -> None:
    text = "Margaret Chen, 1985-03-15, 4472"
    assert is_bare_list(text, ["Margaret Chen", "1985-03-15", "4472"])
    values, _ = validate_evidence(
        text, reading(name="Margaret Chen", birth_date_quote="1985-03-15", id_last4="4472")
    )
    assert values["dob"] == "1985-03-15"
