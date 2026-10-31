"""
orlog/self_improve.py — Orlog runs on itself to surface its own improvements.

Uses run_fast() (~$0.002/call) so it can loop cheaply without burning credits.
Escalates to full run() only when it identifies something worth a real deliberation.

Two modes:
  audit    — scan current system state, surface highest-leverage gaps
  propose  — generate a concrete implementable improvement and write it to a plan file

Usage:
    python3 -m orlog.self_improve              # audit mode
    python3 -m orlog.self_improve --propose    # generate improvement proposal
    python3 -m orlog.self_improve --loop 3     # run 3 audit cycles, escalate if needed
"""

import os
import json
import argparse
from datetime import datetime
from pathlib import Path
from dotenv import load_dotenv

load_dotenv(Path(__file__).parent.parent / ".env")

IMPROVE_LOG = Path("logs/self_improve_log.json")
PROPOSALS   = Path("logs/self_improve_proposals.json")

AUDIT_QUESTIONS = [
    "What is the biggest gap between what Orlog promises (multi-perspective reasoning, blind spot detection, constitutional quality) and what it actually delivers given its current architecture?",
    "Which part of the DPO pair generation pipeline produces the lowest-quality training signal and why?",
    "What signal is Orlog currently ignoring that would most improve its ability to know when it is wrong?",
    "If Mimir (the distilled model) were trained on today's pairs, what reasoning failure would show up most often in evaluation?",
    "What is the cheapest change (fewest API calls, fewest code changes) that would produce the largest improvement in constitutional pass rate?",
]


def _load_system_state() -> str:
    """Summarize the current system state for Orlog's self-audit context."""
    lines = ["=== ORLOG SYSTEM STATE ===\n"]

    # Pair accumulation
    modal_pairs = Path("logs/modal_dpo_pairs.jsonl")
    if modal_pairs.exists():
        count = sum(1 for l in modal_pairs.open() if l.strip())
        lines.append(f"DPO pairs accumulated: {count}/200")

    # Constitutional pass rate from Modal stats
    stats_cache = Path("/tmp/run_stats.json")
    if stats_cache.exists():
        try:
            s = json.loads(stats_cache.read_text())
            lines.append(f"Constitutional avg (last run): A={s.get('avg_const_a', '?')} B={s.get('avg_const_b', '?')}")
            lines.append(f"Pair yield (last run): {s.get('run_pairs', '?')}/{s.get('run_questions', '?')}")
        except Exception:
            pass

    # Primitive scout coverage
    scout_log = Path("logs/primitive_scout_log.json")
    if scout_log.exists():
        try:
            entries = json.loads(scout_log.read_text())
            last = entries[-1] if entries else {}
            # Was a hardcoded "Vocabulary coverage: 98.8% (47 Runes)" literal fed into
            # this system's own self-audit regardless of actual state (Fable audit,
            # 2026-07-10) -- a self-audit lying to itself. No real coverage metric is
            # computable from this log, so report what's actually real instead of a
            # fabricated substitute.
            lines.append(f"Last scout run: {last.get('timestamp','?')[:10]}")
            lines.append(f"Last scout approved: {len(last.get('approved', []))} new primitives")
        except Exception:
            pass

    # Council log size
    council = Path("logs/orlog_council.json")
    if council.exists():
        try:
            entries = json.loads(council.read_text())
            lines.append(f"Council deliberations logged: {len(entries)}")
        except Exception:
            pass

    # Live cost data — Orlog sees its own burn rate
    cost_log = Path("logs/cost_log.json")
    budget_file = Path("logs/cost_budget.json")
    budget = 2.00
    if budget_file.exists():
        try:
            budget = json.loads(budget_file.read_text()).get("daily", 2.00)
        except Exception:
            pass
    if cost_log.exists():
        try:
            from datetime import timezone, timedelta
            entries = json.loads(cost_log.read_text())
            cutoff = (datetime.now(timezone.utc) - timedelta(days=1)).isoformat()
            daily_cost = sum(e.get("cost", 0) for e in entries if e.get("ts", "") >= cutoff)
            by_agent = {}
            for e in entries:
                if e.get("ts", "") >= cutoff:
                    a = e.get("agent", "?")
                    by_agent[a] = by_agent.get(a, 0) + e.get("cost", 0)
            top = sorted(by_agent.items(), key=lambda x: -x[1])[:3]
            lines.append(f"Daily API spend: ${daily_cost:.4f} / budget ${budget:.2f} ({'OK' if daily_cost <= budget else 'OVER BUDGET'})")
            lines.append(f"Top spenders: {', '.join(f'{a}=${c:.4f}' for a, c in top)}")
        except Exception:
            lines.append(f"Daily budget: ${budget:.2f} (no spend data yet — instrument agents with log_cost())")
    else:
        lines.append(f"Daily budget: ${budget:.2f} (no spend data yet — run agents to populate)")

    lines.append("Cron: twice daily (8am + 8pm UTC) — Haiku synthesis + batched constitutional")
    lines.append("Target: 200 pairs → modal run mimir/modal_train.py → LoRA on Qwen2.5-7B-Instruct")

    return "\n".join(lines)


def audit(verbose: bool = True) -> list[dict]:
    """Run fast self-audit across all audit questions. Returns findings."""
    import sys
    sys.path.insert(0, str(Path(__file__).parent.parent))
    from orlog.core import Orlog

    state = _load_system_state()
    o = Orlog(
        goal="Continuously improve the Orlog reasoning and training pipeline",
        domain="research",
    )

    findings = []
    if verbose:
        print(f"\n{'='*60}")
        print("ORLOG SELF-AUDIT")
        print(f"{'='*60}")
        print(state)
        print(f"{'='*60}\n")

    for q in AUDIT_QUESTIONS:
        if verbose:
            print(f"Q: {q[:80]}...")
        result = o.run_fast(question=f"{state}\n\n{q}", task_type="research")
        s = result.get("synthesis", {})
        verdict = s.get("verdict", "") if isinstance(s, dict) else str(s)[:200]

        finding = {
            "question":  q,
            "verdict":   verdict,
            "timestamp": result.get("timestamp"),
        }
        findings.append(finding)
        if verbose:
            print(f"  → {verdict[:120]}\n")

    # Save log
    existing = json.loads(IMPROVE_LOG.read_text()) if IMPROVE_LOG.exists() else []
    existing.append({
        "timestamp": datetime.now().isoformat(),
        "findings":  findings,
    })
    IMPROVE_LOG.write_text(json.dumps(existing[-20:], indent=2))

    return findings


def propose(escalate: bool = False) -> dict:
    """
    Based on audit findings, generate one concrete, implementable proposal.
    escalate=True uses full council (expensive) — only when audit finds something critical.
    """
    import sys
    sys.path.insert(0, str(Path(__file__).parent.parent))
    from orlog.core import Orlog

    findings = audit(verbose=False)
    findings_text = "\n".join(
        f"- {f['verdict']}" for f in findings if f.get("verdict")
    )

    state = _load_system_state()
    proposal_q = f"""{state}

AUDIT FINDINGS:
{findings_text}

Based on these findings, propose ONE concrete improvement to the Orlog/Mimir pipeline
that meets ALL of:
1. Implementable in one session (Python code change or config change)
2. Produces a measurable quality signal within 24h (higher constitutional score, better pair yield, or lower cost)
3. Not already built into the current system
4. Addresses the most critical gap identified above

Format:
IMPROVEMENT: [what to build/change]
WHY: [which finding it addresses]
HOW: [specific implementation steps]
MEASURE: [how to know it worked within 24h]
COST: [API calls and approximate $ cost of the change itself]"""

    o = Orlog(
        goal="Self-improve the Orlog reasoning pipeline",
        domain="research",
    )

    if escalate:
        print("Escalating to full council (this costs ~$0.18)...")
        result = o.run(proposal_q, task_type="research")
    else:
        result = o.run_fast(proposal_q, task_type="research")

    s = result.get("synthesis", {})
    proposal_text = s.get("verdict", "") if isinstance(s, dict) else str(s)

    proposal = {
        "timestamp":  datetime.now().isoformat(),
        "mode":       "full_council" if escalate else "fast",
        "findings":   findings,
        "proposal":   proposal_text,
        "raw":        s,
    }

    existing = json.loads(PROPOSALS.read_text()) if PROPOSALS.exists() else []
    existing.append(proposal)
    PROPOSALS.write_text(json.dumps(existing[-50:], indent=2))

    print(f"\n{'='*60}")
    print("ORLOG SELF-IMPROVEMENT PROPOSAL")
    print(f"{'='*60}")
    print(proposal_text)
    print(f"\nSaved to: {PROPOSALS}")

    return proposal


def loop(cycles: int = 3) -> list:
    """
    Run audit cycles. If any finding scores critically low, escalate to full council.
    Cost: cycles × $0.002 for fast mode. Escalation: +$0.18.
    """
    results = []
    for i in range(cycles):
        print(f"\n--- Cycle {i+1}/{cycles} ---")
        findings = audit(verbose=True)
        results.append(findings)

    # After all cycles, propose using fast mode
    print("\n--- Generating proposal from all findings ---")
    proposal = propose(escalate=False)
    return results


def check_attention_flag() -> dict | None:
    """
    Check if the Modal cron wrote a dead-stop attention flag.
    Call this at session start — if flagged, surface the diagnosis and propose a fix.
    Returns the attention record if present, None otherwise.
    """
    import subprocess
    try:
        subprocess.run(
            ["modal", "volume", "get", "orlog-pairs", "needs_attention.json", "/tmp/orlog_attention.json"],
            capture_output=True, timeout=15,
        )
        data = json.loads(Path("/tmp/orlog_attention.json").read_text())
        print(f"\n[orlog] ATTENTION FLAG from Modal cron:")
        print(f"  Flagged: {data.get('flagged_at', '?')[:19]}")
        print(f"  Reason:  {data.get('reason', '?')}")
        print(f"  Diagnosis: {data.get('diagnosis', '?')}")
        print(f"\n  Run: python3 -m orlog.self_improve --propose")
        return data
    except Exception:
        return None


if __name__ == "__main__":
    import sys
    sys.path.insert(0, str(Path(__file__).parent.parent))

    parser = argparse.ArgumentParser(description="Orlog self-improvement loop")
    parser.add_argument("--propose",  action="store_true", help="Generate improvement proposal after audit")
    parser.add_argument("--escalate", action="store_true", help="Use full council for proposal (costs ~$0.18)")
    parser.add_argument("--loop",     type=int, default=0,  help="Run N audit cycles then propose")
    args = parser.parse_args()

    if args.loop:
        loop(cycles=args.loop)
    elif args.propose:
        propose(escalate=args.escalate)
    else:
        audit(verbose=True)
