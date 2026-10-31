"""
orlog/scheduler/scheduler.py — Autonomous run scheduler.

Runs Orlog internally on a cadence without human triggers.

Cadence (at default 30min interval):
  Every run  — self-play question → DPO pair accumulated (~70 pairs/week)
  Every 5    — session_dpo: convert new site thumbs → DPO pairs
  Every 10   — behavioral health check (P1/P2 flags)
  Every 20   — Hermes research digest (pulls latest AI/reasoning news into brain)
  Every 48   — Roadmap Agent run (updates logs/roadmap.md with ordered queue)

Usage:
    # Start the scheduler (leave running in background terminal)
    python3 -m orlog.scheduler.scheduler --interval 30

    # Run one question and exit
    python3 -m orlog.scheduler.scheduler --once

    # Lock to a domain
    python3 -m orlog.scheduler.scheduler --interval 30 --domain software

    # Check stats
    python3 -m orlog.scheduler.scheduler --stats

    # Dry run — show next question without running
    python3 -m orlog.scheduler.scheduler --dry-run
"""

import time
import json
import signal
import argparse
from datetime import datetime
from pathlib import Path

SCHEDULER_LOG = Path("logs/orlog_scheduler.json")
STATE_FILE    = Path("logs/orlog_scheduler_state.json")


def _load_state() -> dict:
    if STATE_FILE.exists():
        try:
            return json.loads(STATE_FILE.read_text())
        except Exception:
            pass
    return {"domain_cursor": 0, "question_cursor": 0, "total_runs": 0}


def _save_state(state: dict):
    STATE_FILE.parent.mkdir(exist_ok=True)
    STATE_FILE.write_text(json.dumps(state, indent=2))


def _log_run(entry: dict):
    SCHEDULER_LOG.parent.mkdir(exist_ok=True)
    entries = []
    if SCHEDULER_LOG.exists():
        try:
            entries = json.loads(SCHEDULER_LOG.read_text())
        except Exception:
            pass
    entries.append(entry)
    SCHEDULER_LOG.write_text(json.dumps(entries[-500:], indent=2))


def next_question(domain_filter: str = None) -> tuple[str, str, str, str] | None:
    """
    Return the next (domain, task_type, goal, question) from the bank.
    Advances cursor so repeated calls cycle through all questions.
    """
    from orlog.eval.batch_runner import QUESTION_BANK

    bank = QUESTION_BANK
    if domain_filter:
        bank = [q for q in bank if q[0] == domain_filter]
    if not bank:
        return None

    state = _load_state()
    idx   = state.get("question_cursor", 0) % len(bank)
    q     = bank[idx]

    state["question_cursor"] = (idx + 1) % len(bank)
    _save_state(state)

    return q


def run_once(domain_filter: str = None, log_dir: str = "logs") -> dict | None:
    """
    Run one question through Orlog, auto-score, log result.
    Returns the run result dict or None on failure.
    """
    from orlog.core import Orlog

    item = next_question(domain_filter)
    if not item:
        return None

    domain, task_type, goal, question = item

    print(f"  [{datetime.now().strftime('%H:%M:%S')}] {domain}/{task_type} — {question[:70]}...")

    try:
        o      = Orlog(goal=goal, domain=domain, log_dir=log_dir)
        result = o.run(question, task_type=task_type, auto_score=True)

        state = _load_state()
        state["total_runs"] = state.get("total_runs", 0) + 1
        _save_state(state)

        entry = {
            "run_id":      result["id"],
            "domain":      domain,
            "task_type":   task_type,
            "orlog_score": result.get("orlog_score"),
            "scheduled_at": datetime.now().isoformat(),
        }
        _log_run(entry)

        print(f"    score={result.get('orlog_score')} id={result['id']}")
        return result

    except Exception as e:
        print(f"    ERROR: {e}")
        _log_run({"error": str(e), "domain": domain, "scheduled_at": datetime.now().isoformat()})
        return None


HEALTH_CHECK_EVERY  = 10   # run health check every N self-play runs
HERMES_EVERY        = 20   # run Hermes research every N runs (~weekly at 30min interval)
ROADMAP_EVERY       = 48   # run Roadmap Agent every N runs (~daily at 30min interval)
SESSION_DPO_EVERY   = 5    # check for new thumbed sessions every N runs


def _run_health_check():
    try:
        from orlog.eval.health import HealthMonitor
        report = HealthMonitor().check()
        p1p2 = report["p1"] + report["p2"]
        if p1p2:
            print(f"\n  [HEALTH] {len(p1p2)} flag(s) detected:")
            for flag in p1p2:
                print(f"    [{flag['severity']}] {flag['check']}: {flag['detail'][:80]}")
            print(f"    Run `python3 -m orlog.eval.health` for full report.\n")
        else:
            print(f"  [HEALTH] all checks passed.")
    except Exception as e:
        print(f"  [HEALTH] check failed: {e}")


def _run_hermes():
    """Pull latest reasoning/AI research into the Orlog brain."""
    print(f"\n  [HERMES] running weekly research digest...")
    try:
        import sys
        sys.path.insert(0, str(Path(__file__).parent.parent.parent))
        from orlog.hermes.hermes import Hermes
        h = Hermes()
        topics = [
            "recent advances in language model reasoning techniques",
            "DPO training improvements 2025 2026",
            "AI decision making frameworks",
        ]
        for topic in topics:
            try:
                result = h.research(topic)
                lesson = result.get("lesson", "")
                if lesson:
                    print(f"    learned: {lesson[:80]}...")
            except Exception as inner:
                print(f"    topic failed ({topic[:40]}): {inner}")
        print(f"  [HERMES] done.\n")
    except Exception as e:
        print(f"  [HERMES] failed: {e}")


def _run_roadmap():
    """Update the roadmap queue — writes logs/roadmap.md."""
    print(f"\n  [ROADMAP] updating build queue...")
    try:
        import sys
        sys.path.insert(0, str(Path(__file__).parent.parent.parent))
        from agents.roadmap_agent import run_roadmap
        output_path = Path(__file__).parent.parent.parent / "logs" / "roadmap.md"
        run_roadmap(output_path=output_path)
        print(f"  [ROADMAP] saved to logs/roadmap.md\n")
    except Exception as e:
        print(f"  [ROADMAP] failed: {e}")


def _run_session_dpo():
    """Convert new thumbed site sessions into DPO training pairs."""
    print(f"\n  [SESSION DPO] checking for new thumbed sessions...")
    try:
        import sys
        sys.path.insert(0, str(Path(__file__).parent.parent.parent))
        from orlog.agents.session_dpo import run as session_dpo_run
        result = session_dpo_run(dry_run=False, min_length=50)
        n = result.get("new_pairs", 0)
        print(f"  [SESSION DPO] {n} new pair(s) added.\n")
    except Exception as e:
        print(f"  [SESSION DPO] failed: {e}")


def run_continuous(
    interval_minutes: float = 30.0,
    domain_filter: str      = None,
    log_dir: str            = "logs",
    max_runs: int           = None,
):
    """
    Run Orlog on a continuous cadence until interrupted or max_runs reached.
    Handles SIGINT gracefully.
    """
    _stop = {"flag": False}

    def _handle_sigint(sig, frame):
        print("\nScheduler stopping gracefully...")
        _stop["flag"] = True

    signal.signal(signal.SIGINT, _handle_sigint)

    print(f"Orlog scheduler started — interval={interval_minutes}min domain={domain_filter or 'all'}")
    runs = 0

    while not _stop["flag"]:
        if max_runs and runs >= max_runs:
            print(f"Max runs ({max_runs}) reached.")
            break

        run_once(domain_filter=domain_filter, log_dir=log_dir)
        runs += 1

        if runs % HEALTH_CHECK_EVERY == 0:
            _run_health_check()

        if runs % HERMES_EVERY == 0:
            _run_hermes()

        if runs % ROADMAP_EVERY == 0:
            _run_roadmap()

        if runs % SESSION_DPO_EVERY == 0:
            _run_session_dpo()

        if max_runs and runs >= max_runs:
            break

        sleep_secs = interval_minutes * 60
        print(f"    Next run in {interval_minutes:.0f} min. Ctrl+C to stop.")
        for _ in range(int(sleep_secs)):
            if _stop["flag"]:
                break
            time.sleep(1)


def scheduler_stats() -> dict:
    """Summary of all scheduled runs."""
    if not SCHEDULER_LOG.exists():
        return {"total": 0}

    entries = json.loads(SCHEDULER_LOG.read_text())
    scored  = [e for e in entries if e.get("orlog_score") is not None]
    errors  = [e for e in entries if "error" in e]

    by_domain: dict[str, list] = {}
    for e in scored:
        d = e.get("domain", "unknown")
        by_domain.setdefault(d, []).append(e["orlog_score"])

    state = _load_state()

    return {
        "total_runs":   state.get("total_runs", 0),
        "scored":       len(scored),
        "errors":       len(errors),
        "avg_score":    round(sum(e["orlog_score"] for e in scored) / max(len(scored), 1), 2),
        "by_domain":    {d: {"n": len(s), "avg": round(sum(s)/len(s), 2)} for d, s in by_domain.items()},
    }


def main():
    parser = argparse.ArgumentParser(description="Orlog autonomous scheduler")
    parser.add_argument("--once",     action="store_true", help="Run one question and exit")
    parser.add_argument("--interval", type=float, default=30, help="Minutes between runs (continuous mode)")
    parser.add_argument("--domain",   help="Lock to a specific domain")
    parser.add_argument("--max-runs", type=int,   help="Stop after N runs")
    parser.add_argument("--log-dir",  default="logs")
    parser.add_argument("--dry-run",  action="store_true", help="Show next question without running")
    parser.add_argument("--stats",    action="store_true", help="Print scheduler stats and exit")
    args = parser.parse_args()

    if args.stats:
        stats = scheduler_stats()
        print("Orlog scheduler stats:")
        for k, v in stats.items():
            print(f"  {k}: {v}")
        return

    if args.dry_run:
        item = next_question(args.domain)
        if item:
            domain, task_type, goal, question = item
            print(f"Next: [{domain}/{task_type}] {question}")
        else:
            print("No questions available.")
        return

    if args.once:
        run_once(domain_filter=args.domain, log_dir=args.log_dir)
    else:
        run_continuous(
            interval_minutes=args.interval,
            domain_filter=args.domain,
            log_dir=args.log_dir,
            max_runs=args.max_runs,
        )


if __name__ == "__main__":
    import sys
    sys.path.insert(0, str(Path(__file__).parent.parent.parent))
    main()
