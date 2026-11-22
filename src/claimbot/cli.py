"""Command line: serve the API, build the eval world and scenarios, run and report evaluations."""

from __future__ import annotations

import argparse
import asyncio
import json
import os


def main(argv: list[str] | None = None) -> None:
    parser = argparse.ArgumentParser(prog="claimbot")
    sub = parser.add_subparsers(dest="cmd", required=True)

    serve = sub.add_parser("serve", help="run the HTTP API")
    serve.add_argument("--host", default="127.0.0.1")
    serve.add_argument("--port", type=int, default=int(os.getenv("PORT", "8000")))
    serve.add_argument("--workers", type=int, default=1)

    world = sub.add_parser("world", help="generate a synthetic claims world")
    world.add_argument("--out", required=True)
    world.add_argument("--people", type=int, default=300)
    world.add_argument("--seed", type=int, default=7)

    scen = sub.add_parser("scenarios", help="generate a scenario bank for a world")
    scen.add_argument("--world", required=True)
    scen.add_argument("--n", type=int, default=400)
    scen.add_argument("--seed", type=int, default=11)
    scen.add_argument("--out", required=True)

    run = sub.add_parser("eval", help="run one system against the scenario bank")
    run.add_argument("--agent", required=True)
    run.add_argument("--world", required=True)
    run.add_argument("--scenarios", required=True)
    run.add_argument("--out", required=True)
    run.add_argument("--agent-url", required=True)
    run.add_argument("--agent-model", required=True)
    run.add_argument("--agent-extra", default="{}")
    run.add_argument("--sim-url", required=True)
    run.add_argument("--sim-model", required=True)
    run.add_argument("--sim-extra", default="{}")
    run.add_argument("--concurrency", type=int, default=32)
    run.add_argument("--max-turns", type=int, default=14)
    run.add_argument("--limit", type=int)
    run.add_argument("--label", default="")
    run.add_argument("--v1-url", default="http://127.0.0.1:8010")

    rep = sub.add_parser("report", help="summarize eval result files")
    rep.add_argument("paths", nargs="+")
    rep.add_argument("--json")
    rep.add_argument("--md")

    load = sub.add_parser("loadtest", help="HTTP load test of the runtime (rules mode, no model time)")
    load.add_argument("--url", default="http://127.0.0.1:8000")
    load.add_argument("--levels", default="1,8,32,64")
    load.add_argument("--duration", type=float, default=20.0)
    load.add_argument("--label", default="local")
    load.add_argument("--out")

    fz = sub.add_parser("fuzz", help="compromised-interpreter fuzzing with invariant checks")
    fz.add_argument("--conversations", type=int, default=2000)
    fz.add_argument("--seed", type=int, default=0)
    fz.add_argument("--out")

    rj = sub.add_parser("rejudge", help="recompute judge verdicts in result files from their transcripts")
    rj.add_argument("paths", nargs="+")
    rj.add_argument("--world", required=True)

    args = parser.parse_args(argv)
    if args.cmd == "serve":
        import uvicorn

        uvicorn.run(
            "claimbot.api.app:app_factory", factory=True, host=args.host, port=args.port, workers=args.workers
        )
    elif args.cmd == "world":
        from .eval.world import WorldSpec, write

        print(write(args.out, WorldSpec(people=args.people, seed=args.seed)))
    elif args.cmd == "scenarios":
        from .claims.repository import Repository
        from .eval.scenarios import generate, save

        scenarios = generate(Repository.load(args.world), args.n, args.seed)
        save(scenarios, args.out)
        counts: dict[str, int] = {}
        for s in scenarios:
            counts[s.category] = counts.get(s.category, 0) + 1
        print(json.dumps(counts, indent=1))
    elif args.cmd == "eval":
        from .eval.runner import RunConfig
        from .eval.runner import run as run_eval

        cfg = RunConfig(
            agent=args.agent,
            world=args.world,
            scenarios=args.scenarios,
            out=args.out,
            agent_url=args.agent_url,
            agent_model=args.agent_model,
            agent_extra=json.loads(args.agent_extra),
            sim_url=args.sim_url,
            sim_model=args.sim_model,
            sim_extra=json.loads(args.sim_extra),
            concurrency=args.concurrency,
            max_turns=args.max_turns,
            limit=args.limit,
            label=args.label,
            v1_url=args.v1_url,
        )
        asyncio.run(run_eval(cfg))
    elif args.cmd == "loadtest":
        from .eval.loadtest import sweep

        levels = [int(x) for x in args.levels.split(",")]
        asyncio.run(sweep(args.url, levels, args.duration, args.out, args.label))
    elif args.cmd == "fuzz":
        from .eval.fuzz import main as fuzz_main

        result = fuzz_main(args.conversations, args.seed, args.out)
        print(json.dumps({k: v for k, v in result.items() if k != "counts"}, indent=1))
        if result["violations"]:
            raise SystemExit(1)
    elif args.cmd == "rejudge":
        from .claims.repository import Repository
        from .eval.judges import judge, protected_index
        from .eval.report import load_lines
        from .eval.scenarios import Scenario

        repo = Repository.load(args.world)
        index = protected_index(repo)
        for path in args.paths:
            rows = load_lines([path])
            changed = 0
            for row in rows:
                if row["turns"]:
                    verdict = judge(Scenario(**row["scenario"]), repo, row["turns"], index)
                    changed += verdict != row["judge"]
                    row["judge"] = verdict
            payload = "".join(json.dumps(r) + "\n" for r in rows)
            if path.endswith(".gz"):
                import gzip

                with open(path, "wb") as fb:
                    fb.write(gzip.compress(payload.encode(), 9))
            else:
                with open(path, "w") as f:
                    f.write(payload)
            print(f"{path}: {len(rows)} conversations, {changed} verdicts changed")
    elif args.cmd == "report":
        from .eval.report import report

        print(report(args.paths, args.json, args.md))


if __name__ == "__main__":
    main()
