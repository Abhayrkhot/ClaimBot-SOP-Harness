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
AGENT_ORDER = list(AGENT_LABELS)
LABELS = AGENT_LABELS


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
    measured: dict[tuple[str, str], list[int]] = defaultdict(lambda: [0, 0])
    for d in sorted((ROOT / "results" / "runs").glob("*")):
        if not d.is_dir() or d.name.startswith(exclude):
            continue
        if prefix_filter is not None and not d.name.startswith(prefix_filter):
            continue
        if prefix_filter is None and d.name.startswith("v1-"):
            continue
        for f in sorted([*d.glob("*.jsonl"), *d.glob("*.jsonl.gz")]):
            rows = load_lines([f])
            for row in rows:
                groups[(row["agent"], row["agent_model"])].append(row)
            counted = d / (f.name.split(".jsonl")[0] + ".requests.json")
            if counted.exists() and rows:
                key = (rows[0]["agent"], rows[0]["agent_model"])
                measured[key][0] += json.loads(counted.read_text())["requests"]
                measured[key][1] += sum(len(r["turns"]) for r in rows)
    out = {k: summarize(v) for k, v in sorted(groups.items(), key=lambda kv: _sort_key(kv[0]))}
    for key, (requests, turns) in measured.items():
        # Counted at the model server, so it also covers systems that do not report their own calls (v1).
        out[key]["llm_calls_per_turn"] = requests / turns if turns else math.nan
        out[key]["calls_measured_at_server"] = True
    return out


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
           "turn, including every model call the system made for that turn. The harness makes one model call per "
           "turn, but that call emits a complete structured reading of the message, so on the larger models its "
           "turn latency is above a tool agent's shorter steps even though it makes fewer calls.\n",
           "| Agent model | System | Task success (honest) | Attack success (adversarial) | Any violation | "
           "Hallucinated values | p50 / p95 turn latency | Model calls per turn | Output tokens per turn | "
           "Tokens per conversation |",
           "|---|---|---|---|---|---|---|---|---|---|"]  # fmt: skip
    for (agent, model), m in s.items():
        lat = m["latency_ms"]
        out.append(
            f"| {_model(model)} | {LABELS.get(agent, agent)} | {pct(m['task_success'])}{ci(m['task_success'])} | "
            f"{pct(m['attack_success'])}{ci(m['attack_success'])} | {fmt_rate(m['any_violation'])} | "
            f"{fmt_rate(m['hallucination'])} | {secs(lat.get('p50'))} / {secs(lat.get('p95'))} | "
            f"{m['llm_calls_per_turn']:.2f} | {m['output_tokens_per_turn']:,.0f} | {m['tokens_per_conversation']:,.0f} |"
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
    out += ["", "#### Unauthorized emails by kind\n",
            "| Agent model | System | to the attacker's address | to another unregistered address | without consent |",
            "|---|---|---|---|---|"]  # fmt: skip
    for (agent, model), m in s.items():
        ev = m["email_violations"]
        out.append(
            f"| {_model(model)} | {LABELS.get(agent, agent)} | {fmt_rate(ev['attacker_address'])} | "
            f"{fmt_rate(ev['unregistered_address'])} | {fmt_rate(ev['without_consent'])} |"
        )
    cats = sorted({c for m in s.values() for c in m["by_category"]})
    systems = [a for a in AGENT_ORDER if any(k[0] == a for k in s)]
    out += ["", "#### Success by scenario category (all models pooled)\n",
            "Honest categories count task success; adversarial categories count conversations with no violation.\n",
            "| Category | " + " | ".join(LABELS.get(a, a) for a in systems) + " |",
            "|---|" + "---|" * len(systems)]  # fmt: skip
    for c in cats:
        cells = []
        for a in systems:
            k = n = 0
            for (agent, _), m in s.items():
                cat = m["by_category"].get(c)
                if agent == a and cat:
                    k += round(cat["success"] * cat["n"])
                    n += cat["n"]
            cells.append(f"{100 * k / n:.0f}% ({k}/{n})" if n else "")
        out.append(f"| {c.replace('_', ' ')} | " + " | ".join(cells) + " |")
    errors = sum(m["errors"] for m in s.values())
    total = sum(m["n"] for m in s.values())
    out.append(
        f"\n{total:,} conversations in total; {errors} never started because the caller simulator returned an empty "
        "opening message, and are excluded."
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


def _range(values: list[float]) -> str:
    lo, hi = min(values), max(values)
    return f"{100 * lo:.1f}%" if abs(hi - lo) < 0.0005 else f"{100 * lo:.1f}-{100 * hi:.1f}%"


def headline(s: dict[tuple[str, str], dict[str, Any]], v1: dict[tuple[str, str], dict[str, Any]]) -> str:
    if not s:
        return "_Results are generated from `results/` by `scripts/gen_readme.py`._\n"
    by_agent: dict[str, dict[str, dict[str, Any]]] = defaultdict(dict)
    for (agent, model), m in s.items():
        by_agent[agent][_model(model)] = m
    models = list(dict.fromkeys(_model(mo) for _, mo in s))
    total = sum(m["n"] for m in s.values())
    lines = [
        f"Measured on {total:,} simulated conversations: {len(models)} open models ({', '.join(models)}), the same "
        "400 scenarios each (288 honest callers, 112 adversarial), callers simulated by gpt-oss-120b, models "
        "served with vLLM on NVIDIA H200s.\n"
    ]

    def agg(agent: str, key: str) -> tuple[int, int]:
        ms = by_agent.get(agent, {}).values()
        return sum(m[key]["k"] for m in ms), sum(m[key]["n"] for m in ms)

    if "harness" in by_agent:
        h = by_agent["harness"]
        vk, vn = agg("harness", "any_violation")
        hk, _ = agg("harness", "hallucination")
        ak, an = agg("harness", "attack_success")
        line = (
            f"- **SOP harness:** {vk} policy violations in {vn:,} conversations and {hk} with a hallucinated value; "
            f"adversarial callers succeeded {ak} times in {an}. Honest-caller task success "
            f"{_range([m['task_success']['rate'] for m in h.values()])} with one model call per turn"
        )
        if "harness_llm" in by_agent:
            hl = by_agent["harness_llm"]
            modes: dict[str, int] = defaultdict(int)
            for m in hl.values():
                for k, v in m["render_modes"].items():
                    modes[k] += v
            phrased = modes.get("llm", 0) + modes.get("llm_rejected", 0)
            lk, ln = agg("harness_llm", "any_violation")
            line += (
                f"; {_range([m['task_success']['rate'] for m in hl.values()])} with model-phrased replies, of which "
                f"the grounding verifier accepted {100 * modes.get('llm', 0) / max(phrased, 1):.1f}% "
                f"({lk} violations in {ln:,})"
            )
        lines.append(line + ".")
    if "prompt_only" in by_agent:
        p = by_agent["prompt_only"]
        lines.append(
            "- **Prompt-only tool agent, same models:** policy violations in "
            f"{_range([m['any_violation']['rate'] for m in p.values()])} of conversations (mostly summaries emailed to "
            "guessed or attacker-supplied addresses), adversarial callers succeeded in "
            f"{_range([m['attack_success']['rate'] for m in p.values()])}, hallucinated values in "
            f"{_range([m['hallucination']['rate'] for m in p.values()])}, honest-caller task success "
            f"{_range([m['task_success']['rate'] for m in p.values()])}."
        )
    if "guarded_tools" in by_agent:
        g = by_agent["guarded_tools"]
        ck = sum(m["email_violations"]["without_consent"]["k"] for m in g.values())
        lines.append(
            "- **Authorization moved into the tools:** removes the record-access and recipient failures but not "
            f"consent; {ck} conversations still emailed a caller who had not agreed. Hallucinated values in "
            f"{_range([m['hallucination']['rate'] for m in g.values()])}, honest-caller task success "
            f"{_range([m['task_success']['rate'] for m in g.values()])}."
        )
    for (agent, model), m in v1.items():
        if agent != "v1" or ("harness", model) not in v1:
            continue
        h2 = v1[("harness", model)]
        lines.append(
            f"- **v1 to v2** ({_model(model)}, 120 fixture-world scenarios): honest-caller task success "
            f"{pct(m['task_success'])} to {pct(h2['task_success'])}, conversations with a violation "
            f"{pct(m['any_violation'])} to {pct(h2['any_violation'])}, model calls per turn "
            f"{m['llm_calls_per_turn']:.2f} to {h2['llm_calls_per_turn']:.2f}, p95 turn latency "
            f"{secs(m['latency_ms'].get('p95'))} to {secs(h2['latency_ms'].get('p95'))}."
        )
    fuzz = sorted((ROOT / "results" / "fuzz").glob("*.json"))
    if fuzz:
        d = json.loads(fuzz[-1].read_text())
        lines.append(
            f"- **Compromised-interpreter fuzzing:** {d['conversations']:,} conversations and "
            f"{d['counts'].get('turns', 0):,} turns of arbitrary model output, {d['violations']} invariant violations."
        )
    bench = sorted((ROOT / "results" / "bench").glob("*.json"))
    if bench:
        rows = [r for f in bench for r in json.loads(f.read_text())["rows"] if r["concurrency"] <= 8]
        if rows:
            lines.append(
                f"- **Runtime overhead without model time:** p50 {min(r['p50_ms'] for r in rows):.1f}-"
                f"{max(r['p50_ms'] for r in rows):.1f} ms per turn at 1-8 concurrent callers on a 4-vCPU GitHub runner."
            )
    return "\n".join(lines) + "\n"


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
