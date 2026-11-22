"""HTTP load test of the runtime itself (engine, policy, store, API) with model time excluded.

Virtual callers run a scripted six-turn verification-to-email conversation in a loop against a
server in rules mode, so every request exercises verification, claim resolution, grounded
answers, the consent gate, the outbox write and the optimistic-concurrency commit. Model
latency is measured separately in the end-to-end runs.
"""

from __future__ import annotations

import asyncio
import json
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import httpx

SCRIPT = [
    "My name is Margaret Chen. I'm calling about my denied healthcare claim from January.",
    "DOB is 1985-03-15, SSN last four is 4472.",
    "What documents do I need, and where do I send them?",
    "When is the appeal deadline?",
    "That's all, thank you.",
    "send",
]


@dataclass
class LoadResult:
    concurrency: int
    duration_s: float
    turns: int
    conversations: int
    errors: int
    conflicts: int
    turns_per_s: float
    p50_ms: float
    p95_ms: float
    p99_ms: float

    def as_dict(self) -> dict[str, Any]:
        return self.__dict__.copy()


def _pct(values: list[float], q: float) -> float:
    ordered = sorted(values)
    return ordered[min(len(ordered) - 1, int(q * len(ordered)))] if ordered else float("nan")


async def run_load(base_url: str, concurrency: int, duration_s: float, warmup_s: float = 3.0) -> LoadResult:
    latencies: list[float] = []
    counters = {"turns": 0, "conversations": 0, "errors": 0, "conflicts": 0}
    limits = httpx.Limits(max_connections=concurrency * 2, max_keepalive_connections=concurrency * 2)
    async with httpx.AsyncClient(base_url=base_url, timeout=30, limits=limits) as client:
        start = time.perf_counter()
        measure_from = start + warmup_s
        stop = measure_from + duration_s

        async def caller() -> None:
            while time.perf_counter() < stop:
                sid = None
                for message in SCRIPT:
                    t0 = time.perf_counter()
                    try:
                        r = await client.post("/v1/chat", json={"session_id": sid, "message": message})
                    except httpx.HTTPError:
                        counters["errors"] += 1
                        break
                    t1 = time.perf_counter()
                    measuring = t0 >= measure_from and t1 <= stop
                    if r.status_code == 409:
                        counters["conflicts"] += 1
                        break
                    if r.status_code != 200:
                        counters["errors"] += 1
                        break
                    sid = r.json()["session_id"]
                    if measuring:
                        latencies.append((t1 - t0) * 1000)
                        counters["turns"] += 1
                else:
                    if time.perf_counter() <= stop:
                        counters["conversations"] += 1

        await asyncio.gather(*(caller() for _ in range(concurrency)))
    return LoadResult(
        concurrency=concurrency,
        duration_s=duration_s,
        turns=counters["turns"],
        conversations=counters["conversations"],
        errors=counters["errors"],
        conflicts=counters["conflicts"],
        turns_per_s=counters["turns"] / duration_s,
        p50_ms=_pct(latencies, 0.50),
        p95_ms=_pct(latencies, 0.95),
        p99_ms=_pct(latencies, 0.99),
    )


async def sweep(
    base_url: str, levels: list[int], duration_s: float, out: str | Path | None, label: str
) -> list[dict[str, Any]]:
    rows = []
    for level in levels:
        result = await run_load(base_url, level, duration_s)
        row = {"label": label, **result.as_dict()}
        print(json.dumps(row))
        rows.append(row)
    if out:
        await asyncio.to_thread(_write, Path(out), {"label": label, "rows": rows})
    return rows


def _write(path: Path, data: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(data, indent=2) + "\n")
