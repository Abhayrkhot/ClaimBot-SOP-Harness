"""Seeded synthetic claims world for evaluation.

Generates policyholders and claims in the fixture format, with the properties that make the
SOP hard: several claims per caller (so claim resolution matters), shared names between
different people (so a name alone never identifies anyone), denied claims with missing
documents and past or future appeal deadlines, and open claims still waiting on documents.
"""

from __future__ import annotations

import json
import random
import shutil
from dataclasses import dataclass
from datetime import date, timedelta
from pathlib import Path
from typing import Any

from ..claims.repository import DEFAULT_DATA, Repository

FIRST = [
    "Margaret",
    "James",
    "Priya",
    "Daniel",
    "Sofia",
    "Wei",
    "Aisha",
    "Lucas",
    "Elena",
    "Omar",
    "Grace",
    "Mateo",
    "Hannah",
    "Kenji",
    "Nora",
    "Diego",
    "Fatima",
    "Samuel",
    "Leah",
    "Arjun",
    "Chloe",
    "Ibrahim",
    "Maya",
    "Tomas",
    "Zoe",
    "Andre",
    "Ines",
    "Felix",
    "Amara",
    "Victor",
    "Lena",
    "Hugo",
    "Rosa",
    "Malik",
    "Clara",
    "Ravi",
    "Julia",
    "Kofi",
    "Anya",
    "Marcus",
    "Yara",
    "Theo",
    "Noor",
    "Pablo",
    "Ellie",
    "Dmitri",
    "Sana",
    "Oscar",
    "Mei",
    "Jonah",
]
LAST = [
    "Chen",
    "Patel",
    "Garcia",
    "Okafor",
    "Nguyen",
    "Kim",
    "Rossi",
    "Silva",
    "Haddad",
    "Novak",
    "Moreau",
    "Tanaka",
    "Schmidt",
    "Kowalski",
    "Ibrahim",
    "Johansson",
    "Costa",
    "Murphy",
    "Fischer",
    "Mendes",
    "Sato",
    "Dubois",
    "Rahman",
    "Petrov",
    "Lopez",
    "Walker",
    "Bianchi",
    "Larsen",
    "Yilmaz",
    "Duarte",
    "Brennan",
    "Osei",
    "Varga",
    "Lindqvist",
    "Herrera",
    "Mwangi",
    "Castillo",
    "Fontaine",
    "Becker",
    "Aziz",
]
DOCS = {
    "healthcare": ["pathology report", "office note", "diagnosis report", "itemized bill"],
    "dental": ["dental x-rays", "treatment plan"],
    "auto": ["repair estimate", "accident scene photos", "police report"],
}
TODAY = date(2026, 11, 22)


@dataclass(frozen=True)
class WorldSpec:
    people: int = 300
    seed: int = 7
    duplicate_name_rate: float = 0.08


def _money(x: float) -> str:
    return f"{x:.2f}"


def _join(items: list[str]) -> str:
    return items[0] if len(items) == 1 else ", ".join(items[:-1]) + " and " + items[-1]


def generate(spec: WorldSpec = WorldSpec()) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    rng = random.Random(spec.seed)
    people: list[dict[str, Any]] = []
    used_last4: set[str] = set()
    for i in range(spec.people):
        if people and rng.random() < spec.duplicate_name_rate:
            name = rng.choice(people)["name"]  # same name, different person
        else:
            name = f"{rng.choice(FIRST)} {rng.choice(LAST)}"
        dob = date(1950, 1, 1) + timedelta(days=rng.randrange(0, 52 * 365))
        last4 = f"{rng.randrange(0, 10000):04d}"
        while last4 in used_last4:
            last4 = f"{rng.randrange(0, 10000):04d}"
        used_last4.add(last4)
        first, last = name.lower().split()[0], name.lower().split()[-1]
        people.append(
            {
                "party_id": f"P{1000 + i}",
                "name": name,
                "policy_number": f"POL-{20000 + i}",
                "dob": dob.isoformat(),
                "id_type": "ssn_last4",
                "id_last4": last4,
                "phone": f"+1{rng.choice(['617', '650', '415', '312', '206'])}{rng.randrange(2000000, 9999999)}",
                "email": f"{first}.{last}{i}@example.com",
            }
        )
    claims: list[dict[str, Any]] = []
    case_no = 40000
    for person in people:
        for _ in range(rng.choice([1, 2, 2, 3, 3, 4])):
            case_no += rng.randrange(1, 40)
            ctype = rng.choice(["healthcare", "healthcare", "dental", "auto"])
            created = date(2025, 1, 1) + timedelta(days=rng.randrange(0, 680))
            allowed = round(rng.uniform(150, 6000), 0)
            expected = round(allowed * rng.uniform(0.75, 0.95), 2)
            status = rng.choices(["denied", "open", "closed"], weights=[0.38, 0.32, 0.30])[0]
            claim: dict[str, Any] = {
                "case_id": f"CL-{case_no}",
                "party_id": person["party_id"],
                "case_type": ctype,
                "created_at": created.isoformat(),
                "status": status,
                "allowed_max_amount": _money(allowed),
            }
            if status == "denied":
                docs = rng.sample(DOCS[ctype], k=rng.choice([1, 1, 2]))
                claim.update(
                    summary=f"{ctype.title()} claim denied for missing documents",
                    denial_reason=f"the review file did not include the {_join(docs)}",
                    documents_needed=docs,
                    appeal_deadline=(created + timedelta(days=rng.choice([60, 90, 180, 365]))).isoformat(),
                    expected_reimbursement_amount="0.00",
                    net_pay="0.00",
                )
            elif status == "open":
                claim.update(
                    summary=f"{ctype.title()} claim in review",
                    expected_reimbursement_amount=_money(expected),
                    net_pay="0.00",
                )
                if rng.random() < 0.45:
                    claim["documents_needed"] = rng.sample(DOCS[ctype], k=1)
            else:
                claim.update(
                    summary=f"{ctype.title()} claim settled",
                    expected_reimbursement_amount=_money(expected),
                    net_pay=_money(expected),
                )
            claim["net_fee"] = claim["allowed_max_amount"]
            claims.append(claim)
    return people, claims


def write(out_dir: str | Path, spec: WorldSpec = WorldSpec()) -> Path:
    out = Path(out_dir)
    out.mkdir(parents=True, exist_ok=True)
    people, claims = generate(spec)
    (out / "policyholders.json").write_text(json.dumps(people, indent=1) + "\n")
    (out / "claims.json").write_text(json.dumps(claims, indent=1) + "\n")
    shutil.copy(DEFAULT_DATA / "required_document_guideline.json", out / "required_document_guideline.json")
    (out / "world.json").write_text(
        json.dumps({"people": spec.people, "seed": spec.seed, "claims": len(claims)}) + "\n"
    )
    return out


def load(path: str | Path | None) -> Repository:
    return Repository.load(path)
