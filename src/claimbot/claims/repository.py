"""Read-only access to policyholders, claims and document guidance.

The fixture-backed implementation stands in for real policyholder and claims services. Two
properties matter and are kept by any replacement: claim lookup always starts from the verified
party id (no hint can widen the set), and identity matching returns party ids, never records.
"""

from __future__ import annotations

import json
from collections import defaultdict
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from .identity import FIELDS, normalize

DEFAULT_DATA = Path(__file__).resolve().parent / "fixtures"


@dataclass(frozen=True)
class ClaimHint:
    case_id: str | None = None
    case_type: str | None = None
    status: str | None = None
    month: int | None = None
    year: int | None = None

    def is_empty(self) -> bool:
        return not any((self.case_id, self.case_type, self.status, self.month, self.year))


@dataclass
class Repository:
    people: list[dict[str, Any]]
    claims: list[dict[str, Any]]
    guidance: dict[str, Any]
    _by_field: dict[tuple[str, str], set[str]] = field(default_factory=dict, repr=False)
    _by_party: dict[str, list[dict[str, Any]]] = field(default_factory=dict, repr=False)

    def __post_init__(self) -> None:
        index: dict[tuple[str, str], set[str]] = defaultdict(set)
        for person in self.people:
            for f in (*FIELDS, "policy_number"):
                for raw in [person.get(f), *person.get(f + "_aliases", [])]:
                    if not raw:
                        continue
                    value = normalize(f, str(raw))
                    if value is not None:
                        index[(f, value)].add(person["party_id"])
        self._by_field = dict(index)
        by_party: dict[str, list[dict[str, Any]]] = defaultdict(list)
        for claim in self.claims:
            by_party[claim["party_id"]].append(claim)
        for claims in by_party.values():
            claims.sort(key=lambda c: c["created_at"], reverse=True)
        self._by_party = dict(by_party)

    @classmethod
    def load(cls, root: str | Path | None = None) -> Repository:
        base = Path(root) if root else DEFAULT_DATA
        return cls(
            people=json.loads((base / "policyholders.json").read_text()),
            claims=json.loads((base / "claims.json").read_text()),
            guidance=json.loads((base / "required_document_guideline.json").read_text()),
        )

    # -- identity ---------------------------------------------------------------------------

    def match_field(self, field_name: str, value: str) -> list[str]:
        """Party ids whose record has this normalized value for the field."""
        return sorted(self._by_field.get((field_name, value), set()))

    def person(self, party_id: str) -> dict[str, Any]:
        for p in self.people:
            if p["party_id"] == party_id:
                return p
        raise KeyError(party_id)

    # -- claims (ownership first) -------------------------------------------------------------

    def claims_of(self, party_id: str) -> list[dict[str, Any]]:
        return list(self._by_party.get(party_id, []))

    def claim(self, party_id: str, case_id: str) -> dict[str, Any] | None:
        return next((c for c in self.claims_of(party_id) if c["case_id"] == case_id), None)

    def find_claims(self, party_id: str, hint: ClaimHint) -> list[dict[str, Any]]:
        found = self.claims_of(party_id)
        if hint.case_id:
            return [c for c in found if c["case_id"].upper() == hint.case_id.upper()]
        if hint.case_type:
            found = [c for c in found if c["case_type"] == hint.case_type]
        if hint.status:
            found = [c for c in found if c["status"] == hint.status]
        if hint.month:
            found = [c for c in found if int(c["created_at"][5:7]) == hint.month]
        if hint.year:
            found = [c for c in found if int(c["created_at"][:4]) == hint.year]
        return found

    # -- vocabulary for interpretation schemas -----------------------------------------------

    @property
    def case_types(self) -> list[str]:
        return sorted({c["case_type"] for c in self.claims})

    @property
    def statuses(self) -> list[str]:
        return sorted({c["status"] for c in self.claims})

    @property
    def documents(self) -> list[str]:
        names = {d for c in self.claims for d in c.get("documents_needed", [])}
        return sorted(names)

    # -- guidance ---------------------------------------------------------------------------

    def _guidance_key(self, table: dict[str, Any], document: str) -> str | None:
        doc = document.casefold()
        if doc in table:
            return doc
        matches = [k for k in table if k != "default" and (doc in k.casefold() or k.casefold() in doc)]
        return min(matches, key=len) if matches else None

    def alternative_for(self, document: str) -> str:
        table = self.guidance["document_alternative_guidance"]
        key = self._guidance_key(table, document)
        return str(table[key or "default"]["en"])

    def document_guidance(self, document: str) -> str | None:
        table = self.guidance.get("document_guidance", {})
        key = self._guidance_key(table, document)
        return str(table[key]["en"]) if key else None

    def followup(self, topic: str, claim: dict[str, Any]) -> str | None:
        entry = next((e for e in self.guidance["claim_followup_guidance"] if e["topic"] == topic), None)
        if entry is None:
            return None
        docs = claim.get("documents_needed", [])
        if entry.get("requires_documents") and not docs:
            return None
        settings = self.guidance.get("claim_followup_settings", {})
        processing = settings.get("average_processing_time_after_submission", {}).get("en", "")
        return str(entry["en"]).format(
            case_id=claim["case_id"],
            documents=", ".join(docs),
            average_processing_time_after_submission=processing,
        )
