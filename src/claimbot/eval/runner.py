"""Run scenarios end to end: simulated caller <-> system under test, judged per conversation.

Results stream to a JSONL file (one conversation per line) so an interrupted run resumes where
it stopped. Every line carries the transcript with per-turn latency, model calls and tokens,
the side effects, and the judge's verdict.
"""

from __future__ import annotations

import asyncio
import json
import platform
import time
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Any

from openai import AsyncOpenAI

from ..claims.repository import Repository
from ..store.sqlite import SQLiteStore
from .agents import AgentTurn, HarnessAgent, ToolAgent
from .judges import judge, protected_index
from .scenarios import Scenario
from .scenarios import load as load_scenarios
from .simulator import CallerSimulator

AGENTS = ("harness", "harness_llm", "prompt_only", "guarded_tools")


@dataclass
class RunConfig:
    agent: str
    world: str
    scenarios: str
    out: str
    agent_url: str
    agent_model: str
    sim_url: str
    sim_model: str
    agent_extra: dict[str, Any] = field(default_factory=dict)
    sim_extra: dict[str, Any] = field(default_factory=dict)
    concurrency: int = 32
    max_turns: int = 14
    limit: int | None = None
    label: str = ""


def _completed(out: Path) -> set[str]:
    if not out.exists():
        return set()
    return {json.loads(line)["scenario"]["id"] for line in out.read_text().splitlines() if line.strip()}


def _append(out: Path, line: str) -> None:
    with out.open("a") as f:
        f.write(line + "\n")


async def run(cfg: RunConfig, http_client: Any = None) -> Path:
    """Run one configuration. ``http_client`` replaces the network for tests (one client serves all)."""
    if cfg.agent not in AGENTS:
        raise ValueError(f"agent must be one of {AGENTS}")
    repo = Repository.load(cfg.world)
    scenarios = load_scenarios(cfg.scenarios)[: cfg.limit]
    index = protected_index(repo)
    out = Path(cfg.out)
    out.parent.mkdir(parents=True, exist_ok=True)
    done = await asyncio.to_thread(_completed, out)
    todo = [s for s in scenarios if s.id not in done]
    sim_client = AsyncOpenAI(
        base_url=cfg.sim_url, api_key="eval", timeout=180, max_retries=3, http_client=http_client
    )
    agent_client = AsyncOpenAI(
        base_url=cfg.agent_url, api_key="eval", timeout=180, max_retries=3, http_client=http_client
    )
    simulator = CallerSimulator(sim_client, cfg.sim_model, repo, cfg.sim_extra)
    store = await SQLiteStore(":memory:").open()
    engine = None
    if cfg.agent.startswith("harness"):
        engine = HarnessAgent.build(
            repo,
            cfg.agent_url,
            cfg.agent_model,
            store,
            cfg.agent_extra,
            phrasing=cfg.agent == "harness_llm",
            http_client=http_client,
        )
    meta = {
        "agent": cfg.agent,
        "agent_model": cfg.agent_model,
        "sim_model": cfg.sim_model,
        "label": cfg.label,
        "host": platform.node(),
    }
    sem = asyncio.Semaphore(cfg.concurrency)
    lock = asyncio.Lock()
    started = time.time()

    def make_agent() -> Any:
        if engine is not None:
            return HarnessAgent(engine)
        return ToolAgent(agent_client, cfg.agent_model, repo, cfg.agent == "guarded_tools", cfg.agent_extra)

    async def one(s: Scenario) -> None:
        async with sem:
            agent = make_agent()
            turns: list[dict[str, Any]] = []
            error = None
            sim_tokens = 0
            t0 = time.perf_counter()
            try:
                for _ in range(cfg.max_turns):
                    caller = await simulator.next(s, turns)
                    sim_tokens += caller.input_tokens + caller.output_tokens
                    if not caller.message:
                        break
                    reply: AgentTurn = await agent.reply(caller.message)
                    row = asdict(reply)
                    row["caller"] = caller.message
                    row["agent"] = row.pop("text")
                    turns.append(row)
                    if reply.ended or caller.done:
                        break
            except Exception as exc:  # recorded and excluded from rates, never silently dropped
                error = f"{type(exc).__name__}: {str(exc)[:300]}"
            verdict = judge(s, repo, turns, index) if turns else None
            line = {
                **meta,
                "scenario": s.to_json(),
                "turns": turns,
                "judge": verdict,
                "error": error,
                "sim_tokens": sim_tokens,
                "wall_s": round(time.perf_counter() - t0, 2),
            }
            async with lock:
                await asyncio.to_thread(_append, out, json.dumps(line))

    await asyncio.gather(*(one(s) for s in todo))
    await store.close()
    print(
        f"{cfg.agent} {cfg.agent_model}: {len(todo)} conversations in {time.time() - started:.0f}s -> {out}"
    )
    return out
