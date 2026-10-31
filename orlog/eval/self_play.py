"""
orlog/eval/self_play.py — Self-play DPO pair generator.

Two independent Orlog council instances answer the same question.
Neither sees the other's reasoning before answering.
Then each reads the other's synthesis and responds to it.

Why this beats self-scoring:
- Genuine external perspective (two councils, different random seeds, different state detection)
- Disagreement between instances is real divergence, not simulated variance
- The response round exposes WHAT each council misses when confronted with a different view
- Higher-signal DPO pairs: chosen = whichever synthesis the judges prefer, rejected = the other

Pair labeling:
  auto  — auto-score both, chosen = higher score (same as batch runner but with real divergence)
  human — save both for manual review (prints side-by-side)

Usage:
    from orlog.eval.self_play import SelfPlay
    sp = SelfPlay()
    pair = sp.duel("Should we launch now or wait for v2?", domain="software")
    # pair has: prompt, chosen, rejected, gap, winner_score, loser_score

    # Run a full session (N duels):
    sp.run_session(n=10, log_dir="logs")

    # CLI:
    python3 -m orlog.eval.self_play --n 5 --domain software
    python3 -m orlog.eval.self_play --question "..." --domain store
"""

import os
import json
import time
import argparse
from datetime import datetime
from pathlib import Path
from dotenv import load_dotenv

load_dotenv(Path(__file__).parent.parent.parent / ".env")

SELF_PLAY_LOG = Path("logs/self_play_log.json")
SELF_PLAY_PAIRS = Path("logs/self_play_pairs.jsonl")


def _constitutional_score(question: str, synthesis: str) -> tuple[float, bool]:
    """Constitutional checklist score. Returns (score, passed).
    Replaces numerical 1-10 scoring — Orlog diagnosis: numerical scoring collapses
    to ~8.0 due to information-theoretic compression (3.3 bits vs 50+ bits quality space).
    Structural pass/fail per invariant is ground truth. No scorer can infer quality
    from text alone without external outcome feedback.
    """
    from orlog.eval.constitution import Constitution
    result = Constitution().check(question, synthesis, verbose=False)
    return result["score"], result["passed"]


def _extract_synthesis_text(result: dict) -> str:
    s = result.get("synthesis", "")
    if isinstance(s, str):
        return s
    if isinstance(s, dict):
        parts = []
        for k in ["verdict", "reasoning", "key_tension", "blind_spot"]:
            if s.get(k):
                parts.append(f"[{k.upper()}]\n{s[k]}")
        return "\n\n".join(parts) or json.dumps(s)
    return str(s)


class SelfPlay:
    def __init__(self, log_dir: str = "logs"):
        self.log_dir = Path(log_dir)
        self.log_dir.mkdir(exist_ok=True)
        self._log_path   = self.log_dir / "self_play_log.json"
        self._pairs_path = self.log_dir / "self_play_pairs.jsonl"

    def _load_log(self) -> list:
        if self._log_path.exists():
            try:
                return json.loads(self._log_path.read_text())
            except Exception:
                pass
        return []

    def _save_log(self, entries: list):
        self._log_path.write_text(json.dumps(entries[-500:], indent=2))

    def _append_pair(self, pair: dict):
        with self._pairs_path.open("a") as f:
            row = {k: v for k, v in pair.items() if not k.startswith("_")}
            f.write(json.dumps(row) + "\n")

    def duel(
        self,
        question: str,
        domain: str = None,
        goal: str = None,
        task_type: str = "general",
        min_gap: float = 0.5,
        verbose: bool = True,
    ) -> dict | None:
        """
        Two councils answer the same question independently.
        Returns a DPO pair if gap >= min_gap, else None.
        """
        from orlog.core import Orlog

        goal = goal or f"Orlog self-play deliberation on: {question[:80]}"

        if verbose:
            print(f"\n[self-play] {domain or 'general'}: {question[:70]}...")
            print("  Running council A...")

        result_a = Orlog(goal=goal, domain=domain).run(question, task_type=task_type)
        text_a   = _extract_synthesis_text(result_a)
        const_a, passed_a = _constitutional_score(question, text_a)

        if verbose:
            print(f"  A const={const_a:.2f} ({'pass' if passed_a else 'fail'})")
            print("  Running council B...")

        time.sleep(0.5)
        result_b = Orlog(goal=goal, domain=domain).run(question, task_type=task_type)
        text_b   = _extract_synthesis_text(result_b)
        const_b, passed_b = _constitutional_score(question, text_b)

        if verbose:
            print(f"  B const={const_b:.2f} ({'pass' if passed_b else 'fail'})")

        # Pair saved when constitutions differ — one clearer than the other
        # (not numerical gap: Orlog showed 1-10 scoring collapses to ~8 regardless)
        const_gap  = abs(const_a - const_b)
        pair_valid = (passed_a or passed_b) and const_gap >= min_gap

        entry = {
            "timestamp":  datetime.now().isoformat(),
            "question":   question,
            "domain":     domain,
            "task_type":  task_type,
            "const_a":    round(const_a, 3),
            "const_b":    round(const_b, 3),
            "const_gap":  round(const_gap, 3),
            "passed_a":   passed_a,
            "passed_b":   passed_b,
            "pair_saved": pair_valid,
        }
        log = self._load_log()
        log.append(entry)
        self._save_log(log)

        if not pair_valid:
            if verbose:
                print(f"  No pair — gap={const_gap:.2f} or neither passed constitution")
            return None

        if const_a >= const_b:
            chosen, rejected = text_a, text_b
            winner_score, loser_score = const_a, const_b
        else:
            chosen, rejected = text_b, text_a
            winner_score, loser_score = const_b, const_a

        pair = {
            "prompt":        f"Domain: {domain}\nQuestion: {question}",
            "chosen":        chosen,
            "rejected":      rejected,
            "_meta": {
                "domain":        domain,
                "winner_const":  winner_score,
                "loser_const":   loser_score,
                "const_gap":     round(const_gap, 3),
                "source":        "self_play",
                "timestamp":     entry["timestamp"],
            },
        }

        self._append_pair(pair)
        if verbose:
            print(f"  Pair saved (gap={gap:.1f}, winner={winner_score:.1f})")
        return pair

    def run_session(
        self,
        n: int = 10,
        domain: str = None,
        task_type: str = "general",
        min_gap: float = 0.5,
        delay_seconds: float = 2.0,
    ) -> dict:
        """Run N duels, return session summary."""
        from orlog.eval.batch_runner import QUESTION_BANK

        bank = QUESTION_BANK
        if domain:
            bank = [q for q in bank if q[0] == domain]
        if not bank:
            print("No questions found.")
            return {}

        pairs_saved = 0
        total_gap   = 0.0
        results     = []

        print(f"Self-play session: {n} duels, domain={domain or 'all'}")

        for i in range(n):
            q_domain, q_task, q_goal, q_question = bank[i % len(bank)]
            use_domain = domain or q_domain

            pair = self.duel(
                question=q_question,
                domain=use_domain,
                goal=q_goal,
                task_type=q_task,
                min_gap=min_gap,
            )

            if pair:
                pairs_saved += 1
                total_gap   += pair["_meta"]["gap"]
                results.append(pair)

            if i < n - 1:
                time.sleep(delay_seconds)

        avg_gap = total_gap / max(pairs_saved, 1)
        summary = {
            "duels":        n,
            "pairs_saved":  pairs_saved,
            "pair_rate":    f"{pairs_saved/n:.0%}",
            "avg_gap":      round(avg_gap, 2),
            "pairs_file":   str(self._pairs_path),
        }

        print(f"\nSession complete:")
        for k, v in summary.items():
            print(f"  {k}: {v}")

        return summary

    def stats(self) -> dict:
        log   = self._load_log()
        pairs = [e for e in log if e.get("pair_saved")]
        gaps  = [e["gap"] for e in pairs]
        return {
            "total_duels":  len(log),
            "pairs_saved":  len(pairs),
            "avg_gap":      round(sum(gaps) / max(len(gaps), 1), 2),
            "max_gap":      round(max(gaps, default=0), 2),
        }


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Orlog self-play DPO pair generator")
    parser.add_argument("--n",        type=int,   default=5,     help="Number of duels")
    parser.add_argument("--domain",               default=None,  help="Lock to domain")
    parser.add_argument("--question",             default=None,  help="Run one specific question")
    parser.add_argument("--min-gap",  type=float, default=0.5,   dest="min_gap")
    parser.add_argument("--stats",    action="store_true",       help="Print stats and exit")
    args = parser.parse_args()

    import sys
    sys.path.insert(0, str(Path(__file__).parent.parent.parent))

    sp = SelfPlay()

    if args.stats:
        s = sp.stats()
        print("Self-play stats:")
        for k, v in s.items():
            print(f"  {k}: {v}")
    elif args.question:
        sp.duel(args.question, domain=args.domain, min_gap=args.min_gap)
    else:
        sp.run_session(n=args.n, domain=args.domain, min_gap=args.min_gap)
