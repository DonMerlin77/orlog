"""
orlog/eval/batch_runner.py — Systematic DPO pair generator.

Runs Orlog on a pre-defined question bank across domains and task types,
auto-scores each run, accumulates labeled pairs for DPO training.

Strategy:
  - Each domain gets 2-3 questions at different task types
  - Auto-score labels every run immediately
  - Pairs emerge naturally from within-domain score variation
  - Run repeatedly over days/weeks to hit 200+ pairs

Usage:
    python3 -m orlog.eval.batch_runner                    # all domains
    python3 -m orlog.eval.batch_runner --domain software  # one domain
    python3 -m orlog.eval.batch_runner --limit 10         # first N questions
    python3 -m orlog.eval.batch_runner --dry-run          # list questions only
"""

import argparse
import json
import time
from datetime import datetime
from pathlib import Path

# ── Question bank ─────────────────────────────────────────────────────────────
# Format: (domain, task_type, goal_summary, question)
# Goals are short — Orlog fills in domain context from the goal layer.

QUESTION_BANK = [
    # store
    ("store", "strategy", "Curated gift store selling dropshipped products to cat lovers and couples",
     "What is the single highest-leverage action to take before spending any money on ads?"),
    ("store", "launch",   "Handmade ceramic jewelry Shopify store, solo founder, $500 launch budget",
     "What must be true on day one for the first sale to happen within 30 days?"),
    ("store", "ops",      "Dropshipping store with 400 products, 2 suppliers, no orders yet",
     "Which operational process should be automated first and why?"),

    # software
    ("software", "strategy", "SaaS tool for freelancers to manage client invoices and contracts",
     "What is the riskiest assumption in the product that must be validated before writing code?"),
    ("software", "build",    "API-first platform with a React frontend and Postgres backend",
     "What should the data model for user permissions look like to avoid a painful refactor at scale?"),
    ("software", "debug",    "Backend service that randomly drops 3% of webhook events under load",
     "What is the most likely root cause and what is the fastest way to confirm it?"),

    # game_dev
    ("game_dev", "strategy", "Open world survival game where one player occupies each region server-enforced",
     "What is the core loop that must feel right before any other system is worth building?"),
    ("game_dev", "build",    "Godot 4 multiplayer game using Supabase for persistence",
     "How should region ownership be enforced without introducing race conditions?"),
    ("game_dev", "launch",   "Indie game in early access on Steam, 200 wishlists, no marketing budget",
     "What is the minimum content bar to clear before a public demo makes sense?"),

    # content
    ("content", "strategy", "Solo creator writing about personal finance for people in their 30s",
     "What content format compounds the most over 12 months with the least ongoing effort?"),
    ("content", "copy",     "Newsletter with 800 subscribers, 28% open rate, zero paid products yet",
     "Write a subject line framework for a launch email that converts readers to buyers for the first time."),
    ("content", "launch",   "Digital course teaching freelancers to raise their rates",
     "What must the sales page accomplish in the first 10 seconds to prevent the back-click?"),

    # finance
    ("finance", "strategy", "Individual investor building a long-term portfolio on $2k/month savings",
     "What allocation decision has the highest impact on 20-year outcome and why?"),
    ("finance", "research", "Small business owner deciding between an S-corp election and staying as LLC",
     "What are the real break-even conditions where S-corp saves more than it costs?"),

    # creative_writing
    ("creative_writing", "strategy", "Sci-fi novel about consciousness as time travel, first-time novelist",
     "What structural decision made at the outline stage most determines whether the book gets finished?"),
    ("creative_writing", "build",    "Three-act thriller with an unreliable narrator and a mid-story identity reveal",
     "Where should the identity reveal land relative to the act breaks and why?"),

    # mlops
    ("mlops", "strategy", "ML team shipping a recommendation model to production for the first time",
     "What monitoring setup catches the failure modes that kill recommendation systems in production?"),
    ("mlops", "debug",    "Model accuracy dropped 8 points after a data pipeline change last week",
     "What is the debugging sequence that isolates whether this is a data problem or a model problem?"),

    # research
    ("research", "strategy", "Academic researcher studying the long-term effects of remote work on cognition",
     "What study design decisions made at the start are hardest to fix later?"),
    ("research", "research", "Startup trying to understand why their churn rate doubled after a pricing change",
     "What is the fastest way to distinguish price sensitivity from product dissatisfaction in the churn data?"),

    # social_media
    ("social_media", "strategy", "Brand account on Instagram with 1,200 followers and 0 product sales from social",
     "What is the structural reason most brand accounts never convert followers to buyers?"),
    ("social_media", "ops",      "Solo founder posting daily across Instagram, TikTok, and Pinterest",
     "What is the minimum viable content system that prevents creative burnout at 90 days?"),

    # education
    ("education", "strategy", "EdTech startup teaching coding to adult career changers via cohort model",
     "What is the single metric that best predicts whether a student will complete and get hired?"),
    ("education", "build",    "Online course platform deciding between cohort-based and self-paced formats",
     "Under what conditions does cohort format produce better outcomes and when does it destroy them?"),

    # healthcare
    ("healthcare", "strategy", "Digital health app helping patients manage chronic pain with CBT exercises",
     "What is the retention pattern that separates apps that help from apps that get deleted?"),

    # real_estate
    ("real_estate", "strategy", "First-time landlord buying a duplex in a mid-sized city with 20% down",
     "What due diligence step is most commonly skipped that causes the biggest regrets?"),

    # nonprofit
    ("nonprofit", "strategy", "Community org running a food pantry, 3 staff, 200 volunteers, $400k annual budget",
     "What is the operational bottleneck that limits how many families you can serve?"),

    # manufacturing
    ("manufacturing", "ops", "Small batch ceramics studio moving from handmade to semi-automated production",
     "At what production volume does automation save more time than it costs to set up and maintain?"),

    # music
    ("music", "launch", "Independent musician releasing first EP with no label, 800 Instagram followers",
     "What release sequence maximizes streams and keeps listeners engaged past the first week?"),
    ("music", "strategy", "Producer building a catalog of beats for licensing to indie filmmakers",
     "What catalog size and tagging strategy makes the licensing income meaningfully passive?"),
]


def run_batch(
    domains: list[str]   = None,
    limit: int           = None,
    delay_seconds: float = 1.5,
    log_dir: str         = "logs",
    dry_run: bool        = False,
) -> dict:
    """
    Run the question bank through Orlog, auto-score each run.
    Returns summary of runs completed and pairs available.

    delay_seconds: pause between API calls (respect rate limits)
    """
    from orlog.core import Orlog

    questions = QUESTION_BANK
    if domains:
        questions = [q for q in questions if q[0] in domains]
    if limit:
        questions = questions[:limit]

    if dry_run:
        print(f"Dry run — {len(questions)} questions:")
        for domain, task, goal, question in questions:
            print(f"  [{domain}/{task}] {question[:80]}")
        return {"dry_run": True, "count": len(questions)}

    results = []
    errors  = []

    for i, (domain, task_type, goal, question) in enumerate(questions):
        print(f"  [{i+1}/{len(questions)}] {domain}/{task_type} — {question[:60]}...")
        try:
            o = Orlog(goal=goal, domain=domain, log_dir=log_dir)
            result = o.run(question, task_type=task_type, auto_score=True)
            score  = result.get("orlog_score")
            results.append({
                "id":        result["id"],
                "domain":    domain,
                "task_type": task_type,
                "score":     score,
            })
            print(f"    score={score}")
        except Exception as e:
            errors.append({"domain": domain, "task_type": task_type, "error": str(e)})
            print(f"    ERROR: {e}")

        if i < len(questions) - 1:
            time.sleep(delay_seconds)

    # Report available pairs after this batch
    from orlog.eval.dpo import synthesis_pairs
    pairs = synthesis_pairs(str(Path(log_dir) / "orlog_runs.json"), min_gap=1.0)

    summary = {
        "completed_at": datetime.now().isoformat(),
        "runs":         len(results),
        "errors":       len(errors),
        "avg_score":    round(sum(r["score"] for r in results if r["score"]) / max(len(results), 1), 2),
        "dpo_pairs_available": len(pairs),
        "results":      results,
    }

    # Save batch log
    batch_log = Path(log_dir) / "batch_runner_log.json"
    existing  = []
    if batch_log.exists():
        try:
            existing = json.loads(batch_log.read_text())
        except Exception:
            pass
    existing.append(summary)
    batch_log.write_text(json.dumps(existing, indent=2))

    return summary


def main():
    parser = argparse.ArgumentParser(description="Run Orlog question bank for DPO pair generation")
    parser.add_argument("--domain",  nargs="+", help="Filter to specific domains")
    parser.add_argument("--limit",   type=int,  help="Max questions to run")
    parser.add_argument("--delay",   type=float, default=1.5, help="Seconds between API calls")
    parser.add_argument("--log-dir", default="logs")
    parser.add_argument("--dry-run", action="store_true", help="List questions without running")
    args = parser.parse_args()

    print(f"Orlog batch runner — {datetime.now().strftime('%Y-%m-%d %H:%M')}")
    summary = run_batch(
        domains=args.domain,
        limit=args.limit,
        delay_seconds=args.delay,
        log_dir=args.log_dir,
        dry_run=args.dry_run,
    )

    if not args.dry_run:
        print(f"\nBatch complete:")
        print(f"  Runs:        {summary['runs']}")
        print(f"  Errors:      {summary['errors']}")
        print(f"  Avg score:   {summary['avg_score']}")
        print(f"  DPO pairs:   {summary['dpo_pairs_available']}")


if __name__ == "__main__":
    import sys
    sys.path.insert(0, str(Path(__file__).parent.parent.parent))
    main()
