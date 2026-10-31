"""
orlog/eval/health.py — Orlog runtime health monitor.

Watches Orlog's own logs for the failure modes that static analysis misses:
- Perspective collapse: a council agent always agreeing with others (no signal)
- Router ossification: same lens chosen >75% of runs
- Score inflation: auto-scores clustering above 8.0 (scorer is too generous)
- Synthesis staleness: verdict text repeating across runs
- Silent zero: a component logging null/empty/neutral consistently

This is the "hour agent returning 0" detector for Orlog.
Run after any batch to catch drift before it becomes invisible.

Usage:
    from orlog.eval.health import HealthMonitor
    h = HealthMonitor()
    report = h.check()
    h.print_report(report)

    # Or from CLI:
    python3 -m orlog.eval.health
    python3 -m orlog.eval.health --council-log logs/orlog_council.json --last 50
"""

import json
import re
import os
from pathlib import Path
from datetime import datetime
from collections import Counter
from dotenv import load_dotenv

load_dotenv(Path(__file__).parent.parent.parent / ".env")

DEFAULT_COUNCIL_LOG = Path("logs/orlog_council.json")
DEFAULT_ROUTER_LOG  = Path("logs/orlog_router.json")
DEFAULT_SCORE_LOG   = Path("logs/modal_batch_results.json")

THRESHOLDS = {
    "perspective_agreement_max": 0.80,   # flag if one agent agrees with synthesis >80% of runs
    "router_lens_dominance_max": 0.75,   # flag if one lens >75% of selections
    "score_inflation_floor":     7.5,    # flag if avg score > this
    "score_variance_min":        0.5,    # flag if std dev < this (all scores bunched)
    "synthesis_repeat_sim":      0.85,   # flag if two verdicts share >85% bigrams
    "min_runs_for_check":        5,      # skip checks with fewer than N data points
}


# ── Text similarity (bigram Jaccard) ─────────────────────────────────────────

def _bigrams(text: str) -> set:
    words = re.findall(r"\w+", text.lower())
    return set(zip(words, words[1:])) if len(words) > 1 else set()


def _similarity(a: str, b: str) -> float:
    bg_a, bg_b = _bigrams(a), _bigrams(b)
    if not bg_a or not bg_b:
        return 0.0
    return len(bg_a & bg_b) / len(bg_a | bg_b)


# ── Council log checks ────────────────────────────────────────────────────────

def check_council(log_path: Path, last_n: int = 50) -> list[dict]:
    flags = []
    if not log_path.exists():
        return flags

    try:
        runs = json.loads(log_path.read_text())
    except Exception:
        return flags

    runs = runs[-last_n:]
    if len(runs) < THRESHOLDS["min_runs_for_check"]:
        return flags

    # Perspective collapse — does any agent's text look identical or near-identical?
    agent_texts: dict[str, list[str]] = {}
    for run in runs:
        for agent, text in run.get("perspectives", {}).items():
            agent_texts.setdefault(agent, []).append(text or "")

    for agent, texts in agent_texts.items():
        if len(texts) < THRESHOLDS["min_runs_for_check"]:
            continue
        # Check pairwise similarity of last N texts for this agent
        similar_count = 0
        pairs = 0
        for i in range(len(texts)):
            for j in range(i + 1, len(texts)):
                sim = _similarity(texts[i], texts[j])
                if sim > THRESHOLDS["synthesis_repeat_sim"]:
                    similar_count += 1
                pairs += 1
        if pairs > 0 and similar_count / pairs > 0.5:
            flags.append({
                "check":    "perspective_collapse",
                "severity": "P2",
                "agent":    agent,
                "detail":   f"{agent} perspective is >85% similar across {similar_count}/{pairs} run pairs — no signal differentiation",
                "fix":      f"Review {agent} system prompt — it may be averaging rather than applying its cognitive style",
            })

    # Synthesis staleness — verdicts repeating
    syntheses = [
        run.get("synthesis", "").split("\n")[0][:200]
        for run in runs
        if run.get("synthesis")
    ]
    if len(syntheses) >= THRESHOLDS["min_runs_for_check"]:
        stale_pairs = 0
        total_pairs = 0
        for i in range(len(syntheses)):
            for j in range(i + 1, len(syntheses)):
                sim = _similarity(syntheses[i], syntheses[j])
                if sim > THRESHOLDS["synthesis_repeat_sim"]:
                    stale_pairs += 1
                total_pairs += 1
        if total_pairs > 0 and stale_pairs / total_pairs > 0.3:
            flags.append({
                "check":    "synthesis_staleness",
                "severity": "P2",
                "detail":   f"Synthesis verdicts repeating: {stale_pairs}/{total_pairs} pairs >85% similar",
                "fix":      "Check if council question bank is too narrow — add more domain/task variety",
            })

    # Brain state coverage — is it always detecting the same state?
    states = [run.get("state", {}).get("name") for run in runs if run.get("state")]
    if states:
        counter = Counter(states)
        top_state, top_count = counter.most_common(1)[0]
        dominance = top_count / len(states)
        if dominance > 0.80:
            flags.append({
                "check":    "brain_state_ossification",
                "severity": "P3",
                "detail":   f"Brain state always detected as '{top_state}' ({dominance:.0%} of {len(states)} runs)",
                "fix":      "Check BrainState.detect() — context signals may not be varied enough",
            })

    return flags


# ── Router log checks ─────────────────────────────────────────────────────────

def check_router(log_path: Path, last_n: int = 50) -> list[dict]:
    flags = []
    if not log_path.exists():
        return flags

    try:
        runs = json.loads(log_path.read_text())
    except Exception:
        return flags

    runs = runs[-last_n:]
    if len(runs) < THRESHOLDS["min_runs_for_check"]:
        return flags

    # Lens dominance
    all_lenses = []
    for run in runs:
        lenses = run.get("lenses_selected", [])
        all_lenses.extend(lenses)

    if all_lenses:
        counter = Counter(all_lenses)
        top_lens, top_count = counter.most_common(1)[0]
        dominance = top_count / len(all_lenses)
        if dominance > THRESHOLDS["router_lens_dominance_max"]:
            flags.append({
                "check":    "router_ossification",
                "severity": "P2",
                "detail":   f"Lens '{top_lens}' selected {dominance:.0%} of the time across {len(runs)} runs",
                "fix":      "Check TASK_LENS_MODIFIERS in router.py — one lens may have too broad a condition",
            })

    # Task type narrowness
    task_types = [run.get("task_type_raw") or run.get("task_type") for run in runs if run.get("task_type_raw") or run.get("task_type")]
    if task_types:
        counter = Counter(task_types)
        top_type, top_count = counter.most_common(1)[0]
        dominance = top_count / len(task_types)
        if dominance > 0.70:
            flags.append({
                "check":    "task_type_narrowness",
                "severity": "P3",
                "detail":   f"Task type '{top_type}' used {dominance:.0%} of runs — very narrow input distribution",
                "fix":      "Expand question bank to cover more task types",
            })

    return flags


# ── Score log checks ──────────────────────────────────────────────────────────

def check_scores(log_path: Path, last_n: int = 50) -> list[dict]:
    flags = []
    if not log_path.exists():
        return flags

    try:
        results = json.loads(log_path.read_text())
    except Exception:
        return flags

    scores = [r.get("orlog_score", 0) for r in results[-last_n:] if r.get("orlog_score")]
    if len(scores) < THRESHOLDS["min_runs_for_check"]:
        return flags

    avg = sum(scores) / len(scores)
    variance = sum((s - avg) ** 2 for s in scores) / len(scores)
    std_dev = variance ** 0.5

    if avg > THRESHOLDS["score_inflation_floor"]:
        flags.append({
            "check":    "score_inflation",
            "severity": "P2",
            "detail":   f"Avg score {avg:.2f} > {THRESHOLDS['score_inflation_floor']} — scorer is too generous ({len(scores)} runs)",
            "fix":      "Add calibration anchors to score prompt: 'Most outputs score 5-7. Reserve 8+ for genuinely surprising insights.'",
        })

    if std_dev < THRESHOLDS["score_variance_min"]:
        flags.append({
            "check":    "score_no_variance",
            "severity": "P2",
            "detail":   f"Score std dev {std_dev:.2f} < {THRESHOLDS['score_variance_min']} — all runs scoring identically",
            "fix":      "Scorer can't discriminate quality. DPO pairs will be useless. Fix scoring rubric.",
        })

    return flags


# ── Main health check ─────────────────────────────────────────────────────────

class HealthMonitor:
    def __init__(
        self,
        council_log: str = None,
        router_log:  str = None,
        score_log:   str = None,
        last_n:      int = 50,
    ):
        self.council_log = Path(council_log or DEFAULT_COUNCIL_LOG)
        self.router_log  = Path(router_log  or DEFAULT_ROUTER_LOG)
        self.score_log   = Path(score_log   or DEFAULT_SCORE_LOG)
        self.last_n      = last_n

    def check(self) -> dict:
        flags = []
        flags += check_council(self.council_log, self.last_n)
        flags += check_router(self.router_log,   self.last_n)
        flags += check_scores(self.score_log,    self.last_n)

        p1 = [f for f in flags if f.get("severity") == "P1"]
        p2 = [f for f in flags if f.get("severity") == "P2"]
        p3 = [f for f in flags if f.get("severity") == "P3"]

        return {
            "timestamp": datetime.now().isoformat(),
            "total":     len(flags),
            "p1":        p1,
            "p2":        p2,
            "p3":        p3,
            "flags":     flags,
            "healthy":   len(p1) == 0 and len(p2) == 0,
        }

    def print_report(self, report: dict = None):
        if report is None:
            report = self.check()

        print("=" * 56)
        print("  Orlog Health Monitor")
        print(f"  {report['timestamp'][:19]}")
        print("=" * 56)

        if report["healthy"]:
            print("  All checks passed — no P1/P2 flags.")
        else:
            for flag in report["flags"]:
                sev = flag.get("severity", "P?")
                print(f"\n  [{sev}] {flag.get('check', '?')}")
                if flag.get("agent"):
                    print(f"    Agent : {flag['agent']}")
                print(f"    Issue : {flag.get('detail', '')}")
                print(f"    Fix   : {flag.get('fix', '')}")

        print(f"\n  P1: {len(report['p1'])}  P2: {len(report['p2'])}  P3: {len(report['p3'])}")
        print("=" * 56)
        return report


if __name__ == "__main__":
    import sys
    council_log = None
    last_n = 50
    args = sys.argv[1:]
    if "--council-log" in args:
        council_log = args[args.index("--council-log") + 1]
    if "--last" in args:
        last_n = int(args[args.index("--last") + 1])

    h = HealthMonitor(council_log=council_log, last_n=last_n)
    report = h.check()
    h.print_report(report)
    sys.exit(0 if report["healthy"] else 1)
