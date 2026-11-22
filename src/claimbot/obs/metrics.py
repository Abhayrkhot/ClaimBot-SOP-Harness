"""Prometheus metrics. Labels are low-cardinality by construction (phases, stages, outcomes)."""

from __future__ import annotations

from typing import Any

from prometheus_client import Counter, Histogram

TURN_LATENCY = Histogram(
    "claimbot_turn_latency_seconds",
    "End-to-end turn latency, including model calls.",
    ["phase", "render_mode"],
    buckets=(0.005, 0.01, 0.025, 0.05, 0.1, 0.25, 0.5, 1, 2, 4, 8, 16, 32),
)
TURNS = Counter("claimbot_turns_total", "Turns processed.", ["phase", "render_mode"])
TRANSITIONS = Counter("claimbot_phase_transitions_total", "Phase transitions.", ["source", "target"])
DISCLOSURES = Counter("claimbot_facts_disclosed_total", "Facts disclosed, by fact class.", ["cls"])
BLOCKED = Counter(
    "claimbot_disclosures_blocked_total", "Disclosures the policy enforcer refused.", ["cls", "reason"]
)
ACTIONS = Counter("claimbot_actions_total", "Side effects authorized and queued.", ["action"])
FALLBACKS = Counter("claimbot_interpretation_failures_total", "Turns answered with recovery.", ["stage"])
VERIFIER = Counter("claimbot_verifier_results_total", "Grounding verifier outcomes.", ["outcome"])
LLM_CALLS = Counter(
    "claimbot_llm_calls_total", "LLM calls by provider, stage and outcome.", ["provider", "stage", "outcome"]
)
LLM_LATENCY = Histogram(
    "claimbot_llm_call_latency_seconds",
    "Latency of single LLM calls.",
    ["provider", "stage"],
    buckets=(0.05, 0.1, 0.25, 0.5, 1, 2, 4, 8, 16, 32),
)
LLM_TOKENS = Counter("claimbot_llm_tokens_total", "Tokens by direction.", ["provider", "direction"])
OUTBOX = Counter("claimbot_outbox_deliveries_total", "Outbox delivery attempts.", ["outcome"])


def _reason_label(reason: str) -> str:
    if reason.startswith("class "):
        return "phase"
    if reason.startswith("guard "):
        return reason.split()[1]
    return reason.replace(" ", "_")[:40]


def observe_turn(phase_before: str, phase_after: str, render_mode: str, latency_ms: float, ctx: Any) -> None:
    TURNS.labels(phase=phase_before, render_mode=render_mode).inc()
    TURN_LATENCY.labels(phase=phase_before, render_mode=render_mode).observe(latency_ms / 1000)
    for event in ctx.events:
        if event.kind == "phase_transition":
            TRANSITIONS.labels(source=event.data["source"], target=event.data["target"]).inc()
        elif event.kind == "fact_disclosed":
            DISCLOSURES.labels(cls=event.data["cls"].split(".")[0]).inc()
        elif event.kind == "disclosure_blocked":
            BLOCKED.labels(
                cls=event.data["cls"].split(".")[0], reason=_reason_label(event.data["reason"])
            ).inc()
        elif event.kind == "action_queued":
            ACTIONS.labels(action=event.data["action"]).inc()
