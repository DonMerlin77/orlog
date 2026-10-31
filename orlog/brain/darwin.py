"""
orlog/brain/darwin.py — Evolutionary lens pruning.

Named for the mechanism, not the man: survival of the useful.
Living brains prune or they degrade.

Reads Router decision logs + Orlog scores → identifies lenses that:
  1. Never fire (usage = 0 across N runs)
  2. Consistently correlate with low scores (dead weight)
  3. Overlap heavily with another lens (redundancy)

Does NOT modify the brain file automatically. Produces a pruning report.
A human (or future Darwin v2) reviews and approves changes.

Usage:
    from orlog.brain.darwin import Darwin

    d = Darwin()
    report = d.analyze()
    d.print_report(report)

CLI:
    python3 orlog/brain/darwin.py
    python3 orlog/brain/darwin.py --min-runs 20 --low-score-threshold 6.0
"""

import json
import argparse
from datetime import datetime
from pathlib import Path
from dotenv import load_dotenv

load_dotenv(Path(__file__).parent.parent.parent / ".env")

ROUTER_LOG   = Path("logs/router_decisions.json")
ORLOG_RUNS   = Path("logs/orlog_runs.json")
DARWIN_LOG   = Path("logs/darwin_reports.json")

# Thresholds — tuneable
MIN_RUNS_FOR_PRUNING   = 10   # don't prune until we have enough data
LOW_SCORE_THRESHOLD    = 6.0  # lenses that only appear in runs scoring below this
REDUNDANCY_OVERLAP_PCT = 0.8  # if lens A fires whenever lens B fires, one is redundant


def load_router_log() -> list:
    if ROUTER_LOG.exists():
        try:
            return json.loads(ROUTER_LOG.read_text())
        except Exception:
            pass
    return []


def load_orlog_runs() -> list:
    if ORLOG_RUNS.exists():
        try:
            return json.loads(ORLOG_RUNS.read_text())
        except Exception:
            pass
    return []


def _build_lens_stats(router_log: list, orlog_runs: list) -> dict:
    """
    For each lens: count usage, collect associated Orlog scores.
    Joins Router decisions to Orlog runs via routing_id.
    """
    run_scores = {r["routing_id"]: r.get("orlog_score") for r in orlog_runs if r.get("routing_id")}

    lens_stats = {}
    for decision in router_log:
        score = run_scores.get(decision.get("id"))
        for lens in decision.get("lenses_selected", []):
            lid = lens["id"] if isinstance(lens, dict) else lens
            if lid not in lens_stats:
                lens_stats[lid] = {"usage": 0, "scores": [], "domains": set()}
            lens_stats[lid]["usage"] += 1
            if score is not None:
                lens_stats[lid]["scores"].append(score)
            lens_stats[lid]["domains"].add(decision.get("domain", "unknown"))

    for lid in lens_stats:
        lens_stats[lid]["domains"] = list(lens_stats[lid]["domains"])

    return lens_stats


def _detect_unused(lens_stats: dict, all_lenses: list) -> list:
    """Lenses in the catalogue that have never been selected."""
    return [lid for lid in all_lenses if lid not in lens_stats or lens_stats[lid]["usage"] == 0]


def _detect_low_performers(lens_stats: dict, threshold: float, min_scores: int = 3) -> list:
    """Lenses whose average score (when used) is below threshold."""
    flagged = []
    for lid, stats in lens_stats.items():
        scores = stats["scores"]
        if len(scores) >= min_scores:
            avg = sum(scores) / len(scores)
            if avg < threshold:
                flagged.append({
                    "lens":       lid,
                    "avg_score":  round(avg, 2),
                    "n_scores":   len(scores),
                    "threshold":  threshold,
                })
    return flagged


def _detect_redundant(router_log: list) -> list:
    """
    Lenses that always co-occur with another lens.
    If lens A fires in 90%+ of runs where lens B fires, A may be redundant.
    """
    cooccurrence = {}
    totals       = {}

    for decision in router_log:
        lenses = [
            (l["id"] if isinstance(l, dict) else l)
            for l in decision.get("lenses_selected", [])
        ]
        for lid in lenses:
            totals[lid] = totals.get(lid, 0) + 1
            for other in lenses:
                if other != lid:
                    key = (lid, other)
                    cooccurrence[key] = cooccurrence.get(key, 0) + 1

    redundant = []
    checked   = set()
    for (a, b), count in cooccurrence.items():
        if (b, a) in checked:
            continue
        checked.add((a, b))
        if totals.get(a, 0) == 0 or totals.get(b, 0) == 0:
            continue
        pct_a_with_b = count / totals[a]
        pct_b_with_a = count / totals.get(b, 1)
        if pct_a_with_b >= REDUNDANCY_OVERLAP_PCT and pct_b_with_a >= REDUNDANCY_OVERLAP_PCT:
            redundant.append({
                "lens_a":        a,
                "lens_b":        b,
                "overlap_pct":   round(min(pct_a_with_b, pct_b_with_a), 3),
                "co_occurrences": count,
            })

    return redundant


class Darwin:
    def __init__(
        self,
        router_log_path: str = None,
        runs_log_path: str = None,
        report_path: str = None,
        min_runs: int = MIN_RUNS_FOR_PRUNING,
        low_score_threshold: float = LOW_SCORE_THRESHOLD,
    ):
        self.router_log_path    = Path(router_log_path or ROUTER_LOG)
        self.runs_log_path      = Path(runs_log_path or ORLOG_RUNS)
        self.report_path        = Path(report_path or DARWIN_LOG)
        self.min_runs           = min_runs
        self.low_score_threshold = low_score_threshold

    def analyze(self) -> dict:
        router_log = load_router_log() if self.router_log_path == ROUTER_LOG else (
            json.loads(self.router_log_path.read_text()) if self.router_log_path.exists() else []
        )
        orlog_runs = load_orlog_runs() if self.runs_log_path == ORLOG_RUNS else (
            json.loads(self.runs_log_path.read_text()) if self.runs_log_path.exists() else []
        )

        labeled_runs = len(router_log)

        if labeled_runs < self.min_runs:
            report = {
                "status":        "insufficient_data",
                "runs_needed":   self.min_runs,
                "runs_available": labeled_runs,
                "message":       f"Need {self.min_runs} runs before pruning. Have {labeled_runs}.",
                "timestamp":     datetime.now().isoformat(),
            }
            self._save_report(report)
            return report

        from orlog.brain.router import LENSES
        all_lens_ids  = list(LENSES.keys())
        lens_stats    = _build_lens_stats(router_log, orlog_runs)

        unused        = _detect_unused(lens_stats, all_lens_ids)
        low_perf      = _detect_low_performers(lens_stats, self.low_score_threshold)
        redundant     = _detect_redundant(router_log)

        top_lenses = sorted(
            [(lid, stats) for lid, stats in lens_stats.items() if stats["scores"]],
            key=lambda x: sum(x[1]["scores"]) / len(x[1]["scores"]),
            reverse=True,
        )[:5]

        report = {
            "status":          "analyzed",
            "runs_analyzed":   labeled_runs,
            "lenses_tracked":  len(lens_stats),
            "unused_lenses":   unused,
            "low_performers":  low_perf,
            "redundant_pairs": redundant,
            "top_lenses":      [
                {"lens": lid, "avg_score": round(sum(s["scores"]) / len(s["scores"]), 2), "usage": s["usage"]}
                for lid, s in top_lenses
            ],
            "lens_stats":      {
                lid: {
                    "usage":     s["usage"],
                    "avg_score": round(sum(s["scores"]) / len(s["scores"]), 2) if s["scores"] else None,
                    "n_scored":  len(s["scores"]),
                    "domains":   s["domains"],
                }
                for lid, s in lens_stats.items()
            },
            "recommendations": self._build_recommendations(unused, low_perf, redundant),
            "timestamp":       datetime.now().isoformat(),
        }

        self._save_report(report)
        return report

    def _build_recommendations(self, unused: list, low_perf: list, redundant: list) -> list:
        recs = []
        for lid in unused:
            recs.append(f"REMOVE candidate: '{lid}' — never selected across all runs.")
        for lp in low_perf:
            recs.append(
                f"REVIEW candidate: '{lp['lens']}' — avg score {lp['avg_score']} "
                f"(below {lp['threshold']}) across {lp['n_scores']} scored runs."
            )
        for r in redundant:
            recs.append(
                f"MERGE candidate: '{r['lens_a']}' + '{r['lens_b']}' co-occur "
                f"{r['overlap_pct']*100:.0f}% of the time — one may be redundant."
            )
        return recs

    def _save_report(self, report: dict):
        self.report_path.parent.mkdir(exist_ok=True)
        history = []
        if self.report_path.exists():
            try:
                history = json.loads(self.report_path.read_text())
            except Exception:
                pass
        history.append(report)
        self.report_path.write_text(json.dumps(history[-50:], indent=2))

    def print_report(self, report: dict):
        print("=" * 60)
        print("  Darwin — Lens Pruning Analysis")
        print(f"  {datetime.now().strftime('%Y-%m-%d %H:%M')}")
        print("=" * 60)

        if report["status"] == "insufficient_data":
            print(f"\n  {report['message']}")
            print("=" * 60)
            return

        print(f"\n  Runs analyzed : {report['runs_analyzed']}")
        print(f"  Lenses tracked: {report['lenses_tracked']}")
        print()

        if report["top_lenses"]:
            print("  TOP PERFORMING LENSES:")
            for t in report["top_lenses"]:
                print(f"    {t['lens']:<28} avg={t['avg_score']}  used={t['usage']}x")
        print()

        if report["unused_lenses"]:
            print(f"  UNUSED ({len(report['unused_lenses'])}):")
            for lid in report["unused_lenses"]:
                print(f"    · {lid}")
        if report["low_performers"]:
            print(f"\n  LOW PERFORMERS ({len(report['low_performers'])}):")
            for lp in report["low_performers"]:
                print(f"    · {lp['lens']} — avg {lp['avg_score']}")
        if report["redundant_pairs"]:
            print(f"\n  REDUNDANT PAIRS ({len(report['redundant_pairs'])}):")
            for r in report["redundant_pairs"]:
                print(f"    · {r['lens_a']} ↔ {r['lens_b']} ({r['overlap_pct']*100:.0f}% overlap)")

        print()
        if report["recommendations"]:
            print("  RECOMMENDATIONS:")
            for rec in report["recommendations"]:
                print(f"    → {rec}")
        else:
            print("  No pruning recommendations yet.")
        print("=" * 60)


if __name__ == "__main__":
    import sys
    sys.path.insert(0, str(Path(__file__).parent.parent.parent))
    parser = argparse.ArgumentParser()
    parser.add_argument("--min-runs",           type=int,   default=MIN_RUNS_FOR_PRUNING)
    parser.add_argument("--low-score-threshold", type=float, default=LOW_SCORE_THRESHOLD)
    args = parser.parse_args()

    d      = Darwin(min_runs=args.min_runs, low_score_threshold=args.low_score_threshold)
    report = d.analyze()
    d.print_report(report)
