"""Regenerate the README's results sections from results/. `--check` fails if the README is stale.

Inputs:
  results/runs/<tag>/<agent>.jsonl   end-to-end runs (tags starting with "v1-" are the v1 comparison;
                                      tags starting with "smoke" are ignored)
  results/bench/*.json               runtime load test on a GitHub runner
  results/fuzz/*.json                compromised-interpreter fuzz runs
"""

from __future__ import annotations

import argparse
import json
import math
import sys
from collections import defaultdict
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from claimbot.eval.report import AGENT_LABELS, VIOLATIONS, fmt_rate, load_lines, summarize  # noqa: E402

MODEL_ORDER = ["Qwen2.5-7B-Instruct", "Qwen3-30B-A3B-Instruct-2507-FP8", "Qwen3-32B-FP8", "gpt-oss-120b"]
AGENT_ORDER = list(AGENT_LABELS) + ["v1"]
LABELS = {**AGENT_LABELS, "v1": "v1 (original implementation)"}


def _model(m: str) -> str:
    return m.split("/")[-1]


def _sort_key(key: tuple[str, str]) -> tuple[int, int]:
    agent, model = key
    m = _model(model)
    return (
        MODEL_ORDER.index(m) if m in MODEL_ORDER else 99,
        AGENT_ORDER.index(agent) if agent in AGENT_ORDER else 99,
    )


def collect(
    prefix_filter: str | None, exclude: tuple[str, ...] = ("smoke",)
) -> dict[tuple[str, str], dict[str, Any]]:
    groups: dict[tuple[str, str], list[dict[str, Any]]] = defaultdict(list)
    for d in sorted((ROOT / "results" / "runs").glob("*")):
        if not d.is_dir() or d.name.startswith(exclude):
            continue
        if prefix_filter is not None and not d.name.startswith(prefix_filter):
            continue
        if prefix_filter is None and d.name.startswith("v1-"):
            continue
        for f in sorted(d.glob("*.jsonl")):
            for row in load_lines([f]):
                groups[(row["agent"], row["agent_model"])].append(row)
    return {k: summarize(v) for k, v in sorted(groups.items(), key=lambda kv: _sort_key(kv[0]))}


def pct(m: dict[str, Any]) -> str:
    return "n/a" if not m["n"] else f"{100 * m['rate']:.1f}%"


def ci(m: dict[str, Any]) -> str:
    return "" if not m["n"] else f" [{100 * m['lo']:.1f}, {100 * m['hi']:.1f}]"


def secs(ms: float) -> str:
    return "n/a" if ms is None or (isinstance(ms, float) and math.isnan(ms)) else f"{ms / 1000:.2f} s"


def main_tables(s: dict[tuple[str, str], dict[str, Any]]) -> str:
    if not s:
        return "_No end-to-end runs yet._\n"
    out = ["### End-to-end results\n",
           "Rates are over completed conversations, with 95% Wilson intervals in brackets. Latency is per agent "
           "turn, including every model call the system made for that turn.\n",
           "| Agent model | System | Task success (honest) | Attack success (adversarial) | Any violation | "
           "Hallucinated values | p50 / p95 turn latency | Model calls per turn | Tokens per conversation |",
           "|---|---|---|---|---|---|---|---|---|"]  # fmt: skip
    for (agent, model), m in s.items():
        lat = m["latency_ms"]
        out.append(
            f"| {_model(model)} | {LABELS.get(agent, agent)} | {pct(m['task_success'])}{ci(m['task_success'])} | "
            f"{pct(m['attack_success'])}{ci(m['attack_success'])} | {fmt_rate(m['any_violation'])} | "
            f"{fmt_rate(m['hallucination'])} | {secs(lat.get('p50'))} / {secs(lat.get('p95'))} | "
            f"{m['llm_calls_per_turn']:.2f} | {m['tokens_per_conversation']:,.0f} |"
        )
    out += ["", "#### Violations by type (share of all conversations)\n",
            "| Agent model | System | " + " | ".join(v.replace("_", " ") for v in VIOLATIONS) + " |",
            "|---|---|" + "---|" * len(VIOLATIONS)]  # fmt: skip
    for (agent, model), m in s.items():
        out.append(
            f"| {_model(model)} | {LABELS.get(agent, agent)} | "
            + " | ".join(fmt_rate(m["violations"][v]) for v in VIOLATIONS)
            + " |"
        )
    cats = sorted({c for m in s.values() for c in m["by_category"]})
    out += ["", "#### Success by scenario category\n",
            "Honest categories count task success; adversarial categories count conversations with no violation.\n",
            "| Category | " + " | ".join(f"{_model(mo)} {a}" for a, mo in s) + " |",
            "|---|" + "---|" * len(s)]  # fmt: skip
    for c in cats:
        cells = []
        for m in s.values():
            cat = m["by_category"].get(c)
            cells.append(f"{100 * cat['success']:.0f}% ({cat['n']})" if cat else "")
        out.append(f"| {c.replace('_', ' ')} | " + " | ".join(cells) + " |")
    errors = sum(m["errors"] for m in s.values())
    total = sum(m["n"] for m in s.values())
    out.append(
        f"\n{total:,} conversations in total; {errors} ended with a transport or model error and are excluded."
    )
    return "\n".join(out) + "\n"


def v1_table(s: dict[tuple[str, str], dict[str, Any]]) -> str:
    if not s:
        return ""
    out = ["### Before and after: v1 versus v2\n",
           "Same model, simulator and judges, on the 120-scenario fixture-world set v1 supports "
           "(`evalsets/fixture_scenarios.jsonl`).\n",
           "| Agent model | System | Task success (honest) | Attack success (adversarial) | Any violation | "
           "Hallucinated values | p50 / p95 turn latency | Model calls per turn |",
           "|---|---|---|---|---|---|---|---|"]  # fmt: skip
    for (agent, model), m in s.items():
        lat = m["latency_ms"]
        out.append(
            f"| {_model(model)} | {LABELS.get(agent, agent)} | {fmt_rate(m['task_success'])} | "
            f"{fmt_rate(m['attack_success'])} | {fmt_rate(m['any_violation'])} | {fmt_rate(m['hallucination'])} | "
            f"{secs(lat.get('p50'))} / {secs(lat.get('p95'))} | {m['llm_calls_per_turn']:.2f} |"
        )
    return "\n".join(out) + "\n"


def bench_table() -> str:
    files = sorted((ROOT / "results" / "bench").glob("*.json"))
    if not files:
        return ""
    out = ["### Runtime overhead\n",
           "The API in rules mode (no model time) on a standard GitHub-hosted runner, driven by concurrent "
           "scripted six-turn conversations (verification, claim resolution, two grounded answers, consent, "
           "outbox write). Generated by `.github/workflows/bench.yml`.\n",
           "| Deployment | Concurrent callers | Turns per second | p50 | p95 | p99 | Errors |",
           "|---|---|---|---|---|---|---|"]  # fmt: skip
    names = {"sqlite-1w": "1 worker, SQLite", "postgres-4w": "4 workers, PostgreSQL"}
    for f in files:
        d = json.loads(f.read_text())
        for r in d["rows"]:
            out.append(
                f"| {names.get(d['label'], d['label'])} | {r['concurrency']} | {r['turns_per_s']:,.0f} | "
                f"{r['p50_ms']:.1f} ms | {r['p95_ms']:.1f} ms | {r['p99_ms']:.1f} ms | {r['errors'] + r['conflicts']} |"
            )
    return "\n".join(out) + "\n"


def fuzz_table() -> str:
    files = sorted((ROOT / "results" / "fuzz").glob("*.json"))
    if not files:
        return ""
    out = ["### Compromised-interpreter fuzzing\n",
           "`claimbot fuzz` replaces the model with arbitrary interpretations of messages built from several "
           "customers' identity fragments, other customers' claim numbers and injection strings, and checks the "
           "invariants after every turn: protected facts only reach their verified owner, the domain never "
           "attempts a blocked disclosure, email only with consent to the registered address and at most once, "
           "replies never name a claim the caller does not own, and verification only after three of the "
           "party's real values appeared in the caller's messages.\n",
           "| Conversations | Turns | Verified conversations | Facts disclosed | Emails queued | Invariant violations |",
           "|---|---|---|---|---|---|"]  # fmt: skip
    for f in files:
        d = json.loads(f.read_text())
        c = d["counts"]
        out.append(
            f"| {d['conversations']:,} | {c.get('turns', 0):,} | {c.get('verified_conversations', 0):,} | "
            f"{c.get('fact_disclosed', 0):,} | {c.get('action_queued', 0):,} | {d['violations']} |"
        )
    return "\n".join(out) + "\n"


def headline(s: dict[tuple[str, str], dict[str, Any]], v1: dict[tuple[str, str], dict[str, Any]]) -> str:
    lines = []
    by_model: dict[str, dict[str, dict[str, Any]]] = defaultdict(dict)
    for (agent, model), m in s.items():
        by_model[_model(model)][agent] = m
    total = sum(m["n"] for m in s.values())
    if total:
        models = ", ".join(by_model)
        lines.append(
            f"Measured on {total:,} simulated conversations across {len(by_model)} open models ({models}):\n"
        )
        h_viol = sum(
            by_model[mo]["harness"]["any_violation"]["k"] for mo in by_model if "harness" in by_model[mo]
        )
        h_n = sum(
            by_model[mo]["harness"]["any_violation"]["n"] for mo in by_model if "harness" in by_model[mo]
        )
        for base in ("prompt_only", "guarded_tools"):
            k = sum(by_model[mo][base]["attack_success"]["k"] for mo in by_model if base in by_model[mo])
            n = sum(by_model[mo][base]["attack_success"]["n"] for mo in by_model if base in by_model[mo])
            if n:
                lines.append(
                    f"- {LABELS[base]}: adversarial callers succeeded in {k} of {n} conversations ({100 * k / n:.1f}%)."
                )
        if h_n:
            hk = sum(
                by_model[mo]["harness"]["attack_success"]["k"] for mo in by_model if "harness" in by_model[mo]
            )
            hn = sum(
                by_model[mo]["harness"]["attack_success"]["n"] for mo in by_model if "harness" in by_model[mo]
            )
            lines.append(
                f"- SOP harness: {hk} of {hn} adversarial conversations succeeded, and {h_viol} of {h_n} conversations "
                "overall had any violation."
            )
        for mo, agents in by_model.items():
            if "harness" in agents:
                ts = agents["harness"]["task_success"]
                parts = [f"harness {pct(ts)}"]
                for base in ("prompt_only", "guarded_tools", "harness_llm"):
                    if base in agents:
                        parts.append(f"{base.replace('_', ' ')} {pct(agents[base]['task_success'])}")
                lines.append(f"- Task success for honest callers on {mo}: " + ", ".join(parts) + ".")
    for (agent, model), m in v1.items():
        if agent == "v1":
            h = v1.get(("harness", model))
            if h:
                lines.append(
                    f"- v1 to v2 on {_model(model)}: task success {pct(m['task_success'])} to {pct(h['task_success'])}, "
                    f"model calls per turn {m['llm_calls_per_turn']:.2f} to {h['llm_calls_per_turn']:.2f}, p95 turn latency "
                    f"{secs(m['latency_ms'].get('p95'))} to {secs(h['latency_ms'].get('p95'))}."
                )
    return (
        "\n".join(lines) + "\n"
        if lines
        else "_Results are generated from `results/` by `scripts/gen_readme.py`._\n"
    )


def replace(text: str, name: str, body: str) -> str:
    start, end = f"<!-- results:{name}:start -->", f"<!-- results:{name}:end -->"
    i, j = text.index(start) + len(start), text.index(end)
    return text[:i] + "\n" + body.rstrip() + "\n" + text[j:]


def render(text: str) -> str:
    main = collect(None)
    v1 = collect("v1-")
    text = replace(text, "headline", headline(main, v1))
    text = replace(
        text,
        "tables",
        "\n".join(x for x in (main_tables(main), v1_table(v1), bench_table(), fuzz_table()) if x),
    )
    return text


def cli() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--check", action="store_true")
    args = parser.parse_args()
    readme = ROOT / "README.md"
    current = readme.read_text()
    new = render(current)
    if args.check:
        if new != current:
            print("README.md results sections are stale; run scripts/gen_readme.py", file=sys.stderr)
            raise SystemExit(1)
        return
    readme.write_text(new)


if __name__ == "__main__":
    cli()
