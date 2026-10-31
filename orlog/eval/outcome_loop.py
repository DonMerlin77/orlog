"""
orlog/eval/outcome_loop.py — Closes the Mimir training loop with real-world outcomes.

The open loop problem (Orlog diagnosis 2026-05-17):
  Constitutional scoring is internal quality — it checks reasoning structure.
  It cannot tell you whether the verdict was CORRECT.
  Only real outcomes can close that gap.

The loop:
  Question → Council → Constitutional filter → DPO pair → Train Mimir
      ↑                                                         ↓
  Outcome ←←←←←←← Real world ←←←←←← Mimir verdict ←←←←←←←←←←

This module:
  1. Records outcomes against specific Orlog run IDs
  2. Upgrades or downgrades DPO pair labels based on outcome
  3. Surfaces high-outcome questions for more self-play (reinforce the right distribution)
  4. Surfaces low-outcome questions for Orlog re-deliberation

Usage:
    from orlog.eval.outcome_loop import OutcomeLoop

    loop = OutcomeLoop()

    # Record: the verdict from run abc123 led to this outcome
    loop.record("abc123", outcome_type="binary", value=1.0, notes="shipped, users loved it")
    loop.record("def456", outcome_type="revenue", value=340.0, target=500.0)

    # Promote or demote DPO pairs based on accumulated outcomes
    loop.regrade_pairs()

    # Find questions that need more deliberation (low outcome, high uncertainty)
    gaps = loop.open_gaps()
    for gap in gaps:
        print(gap["question"], gap["reason"])

    # CLI:
    python3 -m orlog.eval.outcome_loop --stats
    python3 -m orlog.eval.outcome_loop --gaps
    python3 -m orlog.eval.outcome_loop --record <run_id> --type binary --value 1
"""

import os
import json
import argparse
from datetime import datetime
from pathlib import Path
from dotenv import load_dotenv

load_dotenv(Path(__file__).parent.parent.parent / ".env")

OUTCOME_LOG   = Path("logs/outcome_log.json")
PAIRS_PATHS   = [
    Path("logs/self_play_pairs.jsonl"),
    Path("logs/peak_play_pairs.jsonl"),
]
REGRADED_PATH = Path("logs/regraded_pairs.jsonl")


# ── Outcome scoring ───────────────────────────────────────────────────────────

def _normalize(outcome_type: str, value: float, target: float = None) -> float:
    """Convert raw outcome signal → 0.0-1.0 quality score."""
    if outcome_type == "binary":
        return max(0.0, min(1.0, float(value)))

    if outcome_type == "revenue" and target:
        return max(0.0, min(1.0, value / target))

    if outcome_type == "conversion":
        # 0%=0, 1%=0.3, 3%=0.7, 5%+=1.0
        anchors = [(0, 0), (0.01, 0.3), (0.03, 0.7), (0.05, 1.0)]
        for i in range(len(anchors) - 1):
            lv, ls = anchors[i]; hv, hs = anchors[i+1]
            if lv <= value <= hv:
                return ls + (value - lv) / (hv - lv) * (hs - ls)
        return 1.0

    if outcome_type == "retention":
        return max(0.0, min(1.0, value / 0.40))

    if outcome_type == "engagement":
        return max(0.0, min(1.0, value / 0.25))

    return 0.5


class OutcomeLoop:
    def __init__(self, log_dir: str = "logs"):
        self.log_dir = Path(log_dir)
        self.log_dir.mkdir(exist_ok=True)
        self._log = OUTCOME_LOG

    def _load(self) -> list:
        if self._log.exists():
            try:
                return json.loads(self._log.read_text())
            except Exception:
                pass
        return []

    def _save(self, entries: list):
        self._log.write_text(json.dumps(entries[-1000:], indent=2))

    def record(
        self,
        run_id:       str,
        outcome_type: str,
        value:        float,
        target:       float = None,
        baseline:     float = 0.0,
        notes:        str   = "",
        question:     str   = "",
    ) -> dict:
        score = _normalize(outcome_type, value, target)
        entry = {
            "timestamp":    datetime.now().isoformat(),
            "run_id":       run_id,
            "outcome_type": outcome_type,
            "value":        value,
            "target":       target,
            "baseline":     baseline,
            "outcome_score": round(score, 3),
            "notes":        notes,
            "question":     question,
        }
        log = self._load()
        log.append(entry)
        self._save(log)
        return entry

    def regrade_pairs(self, verbose: bool = True) -> dict:
        """
        Re-label DPO pairs based on accumulated outcome scores.

        Logic (gradient boosting on outcomes):
          - If a verdict's outcome_score > 0.7: promote it (chosen stays chosen, weight += 1)
          - If outcome_score < 0.3: demote it (swap chosen/rejected if verdict was chosen)
          - Pairs with no outcome record: unchanged

        This is the closing of the training loop — constitutional score chose the pair,
        real-world outcome validates or overrides that choice.
        """
        outcomes = {e["run_id"]: e for e in self._load()}
        if not outcomes:
            if verbose:
                print("No outcomes recorded yet.")
            return {"regraded": 0, "promoted": 0, "demoted": 0}

        promoted = demoted = unchanged = 0
        regraded = []

        for pairs_path in PAIRS_PATHS:
            if not pairs_path.exists():
                continue
            for line in pairs_path.read_text().splitlines():
                if not line.strip():
                    continue
                try:
                    pair = json.loads(line)
                except Exception:
                    continue

                run_id = pair.get("_meta", {}).get("run_id", "")
                if run_id not in outcomes:
                    unchanged += 1
                    continue

                score = outcomes[run_id]["outcome_score"]
                if score >= 0.7:
                    # Outcome validates constitutional choice — reinforce
                    pair["_meta"]["outcome_score"]  = score
                    pair["_meta"]["outcome_action"] = "promoted"
                    promoted += 1
                elif score < 0.3:
                    # Outcome contradicts constitutional choice — swap
                    pair["chosen"], pair["rejected"] = pair["rejected"], pair["chosen"]
                    pair["_meta"]["outcome_score"]  = score
                    pair["_meta"]["outcome_action"] = "demoted"
                    demoted += 1
                else:
                    unchanged += 1
                    continue

                regraded.append(pair)

        if regraded:
            with REGRADED_PATH.open("a") as f:
                for p in regraded:
                    f.write(json.dumps(p) + "\n")

        if verbose:
            print(f"Regrade: {promoted} promoted, {demoted} demoted, {unchanged} unchanged")
            print(f"Regraded pairs → {REGRADED_PATH}")

        return {"regraded": len(regraded), "promoted": promoted, "demoted": demoted}

    def open_gaps(self, min_outcomes: int = 1) -> list:
        """
        Find questions where outcomes are low — need re-deliberation.
        These are the highest-value inputs for the next Orlog session.
        A low-outcome question means the council's verdict didn't work in the real world.
        That gap is the most valuable training signal we have.
        """
        log = self._load()
        gaps = []
        for entry in log:
            score = entry.get("outcome_score", 0.5)
            if score < 0.4:
                gaps.append({
                    "question":      entry.get("question", entry.get("run_id", "")),
                    "outcome_score": score,
                    "outcome_type":  entry.get("outcome_type", ""),
                    "notes":         entry.get("notes", ""),
                    "reason":        f"Verdict underperformed (outcome={score:.2f}). Needs re-deliberation.",
                    "timestamp":     entry.get("timestamp", ""),
                })
        return sorted(gaps, key=lambda x: x["outcome_score"])

    def ping_orlog(self, verbose: bool = True) -> list:
        """
        Open loop detector: find gaps, feed each to Orlog for re-deliberation.
        This IS the automatic loop-closer Tom described:
          detect gap → ping Orlog → Orlog generates fix → new DPO pair with outcome label
        """
        from orlog.core import Orlog

        gaps = self.open_gaps()
        if not gaps:
            if verbose:
                print("No open gaps found — all recorded outcomes are healthy.")
            return []

        results = []
        for gap in gaps[:3]:  # cap at 3 re-deliberations per call
            q = gap["question"]
            if not q or len(q) < 10:
                continue

            if verbose:
                print(f"\n[outcome-loop] Re-deliberating: {q[:65]}...")
                print(f"  Previous outcome score: {gap['outcome_score']:.2f} — {gap['reason']}")

            context_note = (
                f"IMPORTANT: A previous deliberation on this question produced a verdict that "
                f"underperformed in practice (outcome score: {gap['outcome_score']:.2f}). "
                f"Notes from outcome: {gap['notes']}. "
                f"Re-examine the question with this failure in mind. "
                f"What did the prior council miss? Where was target leakage — reasoning from "
                f"information that wasn't available at decision time?"
            )
            o = Orlog(goal=f"Re-deliberate: {q[:60]}", domain="research")
            result = o.run(q + "\n\nCONTEXT: " + context_note)
            results.append({"gap": gap, "re_deliberation": result})

            if verbose:
                s = result.get("synthesis", "")
                print(f"  New synthesis: {str(s)[:200]}...")

        return results

    def stats(self) -> dict:
        log = self._load()
        if not log:
            return {"total": 0}
        scores = [e["outcome_score"] for e in log]
        return {
            "total_outcomes":   len(log),
            "avg_outcome":      round(sum(scores) / len(scores), 3),
            "high_performers":  sum(1 for s in scores if s >= 0.7),
            "low_performers":   sum(1 for s in scores if s < 0.3),
            "open_gaps":        len(self.open_gaps()),
        }


if __name__ == "__main__":
    import sys
    sys.path.insert(0, str(Path(__file__).parent.parent.parent))

    parser = argparse.ArgumentParser()
    parser.add_argument("--stats",   action="store_true")
    parser.add_argument("--gaps",    action="store_true")
    parser.add_argument("--regrade", action="store_true")
    parser.add_argument("--ping",    action="store_true", help="Re-deliberate open gaps via Orlog")
    parser.add_argument("--record",  default=None, metavar="RUN_ID")
    parser.add_argument("--type",    default="binary", dest="outcome_type")
    parser.add_argument("--value",   type=float, default=1.0)
    parser.add_argument("--target",  type=float, default=None)
    parser.add_argument("--notes",   default="")
    parser.add_argument("--question", default="")
    args = parser.parse_args()

    loop = OutcomeLoop()

    if args.stats:
        for k, v in loop.stats().items():
            print(f"  {k}: {v}")
    elif args.gaps:
        gaps = loop.open_gaps()
        if not gaps:
            print("No open gaps.")
        for g in gaps:
            print(f"  [{g['outcome_score']:.2f}] {g['question'][:70]}")
            print(f"         {g['reason']}")
    elif args.regrade:
        loop.regrade_pairs()
    elif args.ping:
        loop.ping_orlog()
    elif args.record:
        e = loop.record(
            run_id=args.record,
            outcome_type=args.outcome_type,
            value=args.value,
            target=args.target,
            notes=args.notes,
            question=args.question,
        )
        print(f"  Recorded: run_id={e['run_id']} outcome_score={e['outcome_score']}")
    else:
        parser.print_help()
