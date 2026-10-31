"""
orlog/interface/cli.py — Command-line interface for Orlog.

Usage:
    python3 -m orlog.interface.cli run   --goal "..." --question "..." [--domain X] [--task-type strategy]
    python3 -m orlog.interface.cli build --goal "..." --run-id abc123 --out /tmp/scaffold
    python3 -m orlog.interface.cli score --goal "..." --run-id abc123 --signal revenue --value 340 --target 500
    python3 -m orlog.interface.cli stats
    python3 -m orlog.interface.cli pairs [--min-gap 1.0] [--out pairs.jsonl]
    python3 -m orlog.interface.cli schedule [--interval 30] [--domain X] [--once]
"""

import argparse
import json
import sys
from pathlib import Path


def cmd_run(args):
    from orlog.core import Orlog
    o      = Orlog(goal=args.goal, domain=args.domain)
    result = o.run(args.question, task_type=args.task_type, auto_score=True)
    s      = result["synthesis"]

    print(f"\n{'='*60}")
    print(f"  Orlog — {result['domain']} / {result['task_type']}")
    print(f"  Run ID: {result['id']}   Score: {result.get('orlog_score', 'n/a')}/10")
    print(f"{'='*60}")
    print(f"\nVERDICT\n{s.get('verdict', '')}\n")
    if s.get("key_tension"):
        print(f"KEY TENSION\n{s['key_tension']}\n")
    if s.get("blind_spot"):
        print(f"BLIND SPOT\n{s['blind_spot']}\n")
    print(f"Lenses: {', '.join(result.get('lenses', []))}")


def cmd_build(args):
    from orlog.core import Orlog
    o    = Orlog(goal=args.goal, domain=args.domain)
    runs = o._load_runs()
    run  = next((r for r in runs if r.get("id") == args.run_id), None)
    if not run:
        print(f"Run {args.run_id} not found in logs.")
        sys.exit(1)

    result = o.build(run, output_dir=args.out, heal=not args.no_heal)
    print(f"Built → {result['output_dir']}")
    print(f"Loop: {result['loop_report'].get('final_status', 'unknown')}")
    import os
    for root, _, files in os.walk(result["output_dir"]):
        for f in sorted(files):
            path = os.path.join(root, f)
            rel  = path.replace(result["output_dir"] + "/", "")
            size = os.path.getsize(path)
            print(f"  {rel} ({size} bytes)")


def cmd_score(args):
    from orlog.core import Orlog
    o     = Orlog(goal=args.goal, domain=args.domain)
    entry = o.record_outcome(
        run_id=args.run_id,
        signal_type=args.signal,
        value=args.value,
        baseline=args.baseline,
        target=args.target,
        window_days=args.window,
        notes=args.notes,
    )
    print(f"Scored run {entry['run_id']}: {entry['orlog_score']}/10 [{args.signal}]")


def cmd_stats(args):
    from orlog.brain.router import Router
    from orlog.scheduler.scheduler import scheduler_stats
    from orlog.eval.dpo import synthesis_pairs

    r = Router()
    print("\nRouter stats:")
    rs = r.stats()
    for k, v in rs.items():
        print(f"  {k}: {v}")

    print("\nScheduler stats:")
    ss = scheduler_stats()
    for k, v in ss.items():
        print(f"  {k}: {v}")

    print(f"\nDPO pairs available: {len(synthesis_pairs())}")


def cmd_pairs(args):
    from orlog.eval.dpo import prepare
    summary = prepare(out_path=args.out, min_gap=args.min_gap)
    print(f"Exported {summary['total_pairs']} pairs → {summary['out_path']}")
    for t, s in summary["by_type"].items():
        print(f"  {t}: {s['n']} pairs, avg gap {s['avg_gap']}")
    print(f"  {summary['note']}")


def cmd_schedule(args):
    from orlog.scheduler.scheduler import run_once, run_continuous
    if args.once:
        run_once(domain_filter=args.domain)
    else:
        run_continuous(
            interval_minutes=args.interval,
            domain_filter=args.domain,
            max_runs=args.max_runs,
        )


def cmd_health(args):
    from orlog.eval.health import HealthMonitor
    h      = HealthMonitor(last_n=args.last)
    report = h.check()
    h.print_report(report)
    sys.exit(0 if report["healthy"] else 1)


def main():
    parser = argparse.ArgumentParser(prog="orlog", description="Orlog CLI")
    sub    = parser.add_subparsers(dest="command")

    # run
    p_run = sub.add_parser("run", help="Run a question through the full Orlog pipeline")
    p_run.add_argument("--goal",      required=True)
    p_run.add_argument("--question",  required=True)
    p_run.add_argument("--domain",    default=None)
    p_run.add_argument("--task-type", default="general", dest="task_type")

    # build
    p_build = sub.add_parser("build", help="Generate project scaffolding from a run")
    p_build.add_argument("--goal",     required=True)
    p_build.add_argument("--domain",   default=None)
    p_build.add_argument("--run-id",   required=True, dest="run_id")
    p_build.add_argument("--out",      default="/tmp/orlog_scaffold")
    p_build.add_argument("--no-heal",  action="store_true", dest="no_heal")

    # score
    p_score = sub.add_parser("score", help="Label a run with a real-world outcome")
    p_score.add_argument("--goal",     required=True)
    p_score.add_argument("--domain",   default=None)
    p_score.add_argument("--run-id",   required=True, dest="run_id")
    p_score.add_argument("--signal",   required=True, choices=["revenue","conversion","retention","engagement","binary"])
    p_score.add_argument("--value",    required=True, type=float)
    p_score.add_argument("--baseline", default=0.0,   type=float)
    p_score.add_argument("--target",   default=1.0,   type=float)
    p_score.add_argument("--window",   default=7,     type=int)
    p_score.add_argument("--notes",    default="")

    # stats
    sub.add_parser("stats", help="Show router, scheduler, and DPO stats")

    # pairs
    p_pairs = sub.add_parser("pairs", help="Export DPO training pairs")
    p_pairs.add_argument("--out",     default="dpo_pairs.jsonl")
    p_pairs.add_argument("--min-gap", default=1.0, type=float, dest="min_gap")

    # schedule
    p_sched = sub.add_parser("schedule", help="Run Orlog autonomously on a cadence")
    p_sched.add_argument("--once",     action="store_true")
    p_sched.add_argument("--interval", default=30.0, type=float)
    p_sched.add_argument("--domain",   default=None)
    p_sched.add_argument("--max-runs", default=None, type=int, dest="max_runs")

    # health
    p_health = sub.add_parser("health", help="Check Orlog behavioral health (signal dropout, score inflation, etc.)")
    p_health.add_argument("--last", default=50, type=int, help="Analyze last N runs")

    args = parser.parse_args()

    dispatch = {
        "run":      cmd_run,
        "build":    cmd_build,
        "score":    cmd_score,
        "stats":    cmd_stats,
        "pairs":    cmd_pairs,
        "schedule": cmd_schedule,
        "health":   cmd_health,
    }

    if args.command not in dispatch:
        parser.print_help()
        sys.exit(1)

    dispatch[args.command](args)


if __name__ == "__main__":
    sys.path.insert(0, str(Path(__file__).parent.parent.parent))
    main()
