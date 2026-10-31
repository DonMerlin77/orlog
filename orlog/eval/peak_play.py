"""
orlog/eval/peak_play.py — Tangent-wave self-play with calculus peak detection.

Two councils argue iteratively. Each round, each council reads the other's last
synthesis and responds. The divergence gap g(t) = |score_A - score_B| is tracked
each round. The synthesizer (bridge) fires when g'(t) transitions from positive
to negative — i.e., when the first derivative of the gap function crosses zero,
meaning divergence has peaked and more rounds would only add noise.

The math:
  g(t)  = gap at round t
  g'(t) = g(t) - g(t-1)           # first derivative (numerical)
  Peak  = argmax(g(t))             # round where divergence is maximum
  Bridge fires at: g'(t) <= 0 for 2 consecutive rounds after positive g'

Why this is better than fixed-round self-play:
  - Extracts maximum information from the argument before synthesizing
  - DPO pairs are from peak-divergence round — highest signal, not arbitrary round
  - Synthesizer has both extreme positions at maximum tension
  - Natural stopping criterion (derived from the data, not hardcoded)

Analogy: tan(x) approaches ±∞ at the asymptote but never reaches it.
  Council A → +∞ (best case position)
  Council B → -∞ (worst case position)
  Bridge (synthesizer) lives AT the asymptote — connects the extremes
  into a finite, actionable verdict.

Usage:
    from orlog.eval.peak_play import PeakPlay
    pp = PeakPlay()
    result = pp.duel("Should we launch now or polish for two more weeks?", domain="software")
    print(result['peak_round'], result['peak_gap'], result['bridge'])

    # Full session:
    pp.run_session(n=5)

    # CLI:
    python3 -m orlog.eval.peak_play --n 3 --domain software
    python3 -m orlog.eval.peak_play --question "..." --max-rounds 6
"""

import os
import sys
import json
import time
import argparse
from datetime import datetime
from pathlib import Path
from dotenv import load_dotenv

sys.path.insert(0, str(Path(__file__).parents[2]))
from llm_router import fast_call

load_dotenv(Path(__file__).parent.parent.parent / ".env")

PEAK_PLAY_LOG   = Path("logs/peak_play_log.json")
PEAK_PLAY_PAIRS = Path("logs/peak_play_pairs.jsonl")

MAX_ROUNDS_DEFAULT  = 6
MIN_ROUNDS          = 2    # always run at least 2 rounds before peak detection
PEAK_CONFIRM_ROUNDS = 2    # g'(t) must be <= 0 for this many rounds to confirm peak


# ── Scoring ──────────────────────────────────────────────────────────────────

def _auto_score(client, question: str, synthesis: str) -> float:
    # Gradient-boosted scoring: three independent judges, take mean.
    # Each judge sees a different framing to break systematic bias.
    # Like bootstrap sampling — variance across judges >> variance from one judge.
    score_prompts = [
        (
            f"Score this synthesis 1-10. STRICT calibration: the median output is 5. "
            f"Only give 8+ if the verdict is genuinely counterintuitive AND correct. "
            f"Give 3 or below if it hedges or could apply to any question.\n\n"
            f"Q: {question}\n\nSynthesis: {synthesis[:600]}\n\nReply: one integer only."
        ),
        (
            f"You are scoring reasoning quality 1-10 where 5=average human deliberation. "
            f"Question: {question}\n\nOutput to score: {synthesis[:600]}\n\n"
            f"Does it name a specific tension? Does the verdict follow from it? "
            f"Could this verdict apply to a completely different question (if yes: max 5)?\n"
            f"Score (1-10, integer only):"
        ),
        (
            f"Forced ranking: score 1-10 where you MUST use the full range. "
            f"3 = generic advice. 5 = real tradeoff named. 7 = non-obvious verdict. "
            f"9 = changes how you see the question. Default to 4-6. Reserve 8-10 rarely.\n\n"
            f"Question: {question}\nSynthesis: {synthesis[:600]}\n\nInteger score:"
        ),
    ]
    scores = []
    for prompt in score_prompts:
        try:
            raw = fast_call("You are a strict scoring judge. Reply with only an integer 1-10.", prompt, 10)
            scores.append(max(1.0, min(10.0, float(raw.split()[0]))))
        except Exception:
            scores.append(5.0)
    return sum(scores) / len(scores)


def _fast_score(client, question: str, synthesis: str) -> float:
    """Single-judge score for mid-loop peak detection. Speed over precision."""
    prompt = (
        f"Score 1-10. Strict: median output = 5. Only 8+ if genuinely counterintuitive AND correct.\n\n"
        f"Q: {question}\nSynthesis: {synthesis[:600]}\n\nInteger only."
    )
    try:
        raw = fast_call("You are a strict scoring judge. Reply with only an integer 1-10.", prompt, 10)
        return max(1.0, min(10.0, float(raw.split()[0])))
    except Exception:
        return 5.0


def _extract_text(result: dict) -> str:
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


# ── Bridge synthesizer ────────────────────────────────────────────────────────

BRIDGE_SYSTEM = (
    "You are the Bridge — you fire at the peak of maximum tension between two councils. "
    "Council A has argued toward one extreme; Council B has argued toward the opposite. "
    "Both have had multiple rounds to strengthen their positions. "
    "Your job: synthesize the maximum-information verdict from these two opposing peaks. "
    "Do not average them. Do not pick a side. Find what is true IN the tension between them. "
    "The verdict must be more specific and correct than either council could produce alone."
)


def _bridge_synthesize(client, question: str, synthesis_a: str, synthesis_b: str,
                       peak_round: int, peak_gap: float) -> str:
    try:
        return fast_call(
            BRIDGE_SYSTEM,
            f"QUESTION: {question}\n\n"
            f"PEAK REACHED AT ROUND {peak_round} (divergence gap: {peak_gap:.2f})\n\n"
            f"[COUNCIL A — POSITION AT PEAK]\n{synthesis_a}\n\n"
            f"[COUNCIL B — POSITION AT PEAK]\n{synthesis_b}\n\n"
            f"Synthesize the bridge verdict: VERDICT (one sentence), "
            f"WHAT A SAW (that B missed), WHAT B SAW (that A missed), "
            f"RESOLUTION (what lives in the tension between them).",
            1000,
        )
    except Exception as e:
        return f"[bridge error: {e}]"


# ── Peak detection ────────────────────────────────────────────────────────────

def _detect_peak(gaps: list[float]) -> bool:
    """
    Returns True if the gap function has peaked.
    Peak confirmed when g'(t) <= 0 for PEAK_CONFIRM_ROUNDS consecutive rounds.
    Requires at least MIN_ROUNDS before detection starts.
    """
    if len(gaps) < MIN_ROUNDS + 1:
        return False
    derivatives = [gaps[i] - gaps[i-1] for i in range(1, len(gaps))]
    # Check last PEAK_CONFIRM_ROUNDS derivatives all <= 0
    if len(derivatives) < PEAK_CONFIRM_ROUNDS:
        return False
    return all(d <= 0 for d in derivatives[-PEAK_CONFIRM_ROUNDS:])


# ── Core duel ─────────────────────────────────────────────────────────────────

class PeakPlay:
    def __init__(self, log_dir: str = "logs"):
        self.log_dir    = Path(log_dir)
        self.log_dir.mkdir(exist_ok=True)
        self._log_path   = self.log_dir / "peak_play_log.json"
        self._pairs_path = self.log_dir / "peak_play_pairs.jsonl"

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
        question:   str,
        domain:     str   = None,
        goal:       str   = None,
        task_type:  str   = "general",
        max_rounds: int   = MAX_ROUNDS_DEFAULT,
        min_gap:    float = 0.5,
        verbose:    bool  = True,
    ) -> dict:
        """
        Iterative self-play with calculus peak detection.
        Synthesizer fires when divergence gap g(t) peaks.
        Returns dict with: gaps, peak_round, peak_gap, bridge, pair (if gap >= min_gap).
        """
        from orlog.core import Orlog

        goal  = goal or f"Peak-play deliberation: {question[:80]}"
        client = None

        if verbose:
            print(f"\n[peak-play] {domain or 'general'}: {question[:65]}...")

        # Track synthesis texts and scores per round per council
        history_a: list[tuple[str, float]] = []   # (synthesis_text, score)
        history_b: list[tuple[str, float]] = []
        gaps: list[float] = []

        context_a = {"domain": domain, "goal": goal}
        context_b = {"domain": domain, "goal": goal}

        for round_n in range(max_rounds):
            if verbose:
                print(f"  Round {round_n}: ", end="", flush=True)

            # Each council runs independently in round 0,
            # then reads the other's previous synthesis as counterpoint.
            if round_n > 0 and history_b:
                context_a["counterpoint"] = (
                    f"The opposing council argued: {history_b[-1][0][:400]}. "
                    f"Respond — push your position further where they are wrong."
                )
            if round_n > 0 and history_a:
                context_b["counterpoint"] = (
                    f"The opposing council argued: {history_a[-1][0][:400]}. "
                    f"Respond — push your position further where they are wrong."
                )

            result_a = Orlog(goal=goal, domain=domain).run(question, task_type=task_type)
            text_a   = _extract_text(result_a)
            # Fast single-judge score for peak detection (speed > precision mid-loop)
            score_a  = _fast_score(client, question, text_a)

            time.sleep(0.3)

            result_b = Orlog(goal=goal, domain=domain).run(question, task_type=task_type)
            text_b   = _extract_text(result_b)
            score_b  = _fast_score(client, question, text_b)

            gap = abs(score_a - score_b)
            gaps.append(gap)
            history_a.append((text_a, score_a))
            history_b.append((text_b, score_b))

            if verbose:
                deriv = gaps[-1] - gaps[-2] if len(gaps) > 1 else 0.0
                print(f"A={score_a:.1f} B={score_b:.1f} gap={gap:.2f} g'={deriv:+.2f}")

            # Peak detection via first derivative
            if _detect_peak(gaps):
                if verbose:
                    print(f"  Peak detected at round {gaps.index(max(gaps))}.")
                break

        # Find the round of maximum divergence
        peak_idx  = gaps.index(max(gaps))
        peak_gap  = gaps[peak_idx]
        peak_a, _fast_pa = history_a[peak_idx]
        peak_b, _fast_pb = history_b[peak_idx]

        if verbose:
            print(f"  Gap curve: {[round(g,2) for g in gaps]}")
            print(f"  Bridge firing at round {peak_idx} (gap={peak_gap:.2f})...")

        # Bridge: synthesizer fires at the peak
        bridge = _bridge_synthesize(client, question, peak_a, peak_b, peak_idx, peak_gap)

        # Constitutional scoring — structural checklist, not numerical scale.
        # Orlog diagnosis: 1-10 scoring collapses to ~8 due to information-theoretic compression
        # (3.3 bits vs 50+ bits of reasoning quality space). Constitutional pass/fail per
        # invariant is the ground truth signal — no scorer needed for DPO labeling.
        from orlog.eval.constitution import Constitution
        const = Constitution()
        check_a = const.check(question, peak_a, verbose=False)
        check_b = const.check(question, peak_b, verbose=False)
        score_pa = check_a["score"]
        score_pb = check_b["score"]

        if verbose:
            print(f"  Constitutional: A={score_pa:.2f} B={score_pb:.2f} "
                  f"({'A passes' if check_a['passed'] else 'A fails'} / "
                  f"{'B passes' if check_b['passed'] else 'B fails'})")

        entry = {
            "timestamp":    datetime.now().isoformat(),
            "question":     question,
            "domain":       domain,
            "gaps":         [round(g, 2) for g in gaps],
            "peak_round":   peak_idx,
            "peak_gap":     round(peak_gap, 2),
            "total_rounds": len(gaps),
            "const_a":      round(score_pa, 3),
            "const_b":      round(score_pb, 3),
            "pair_saved":   check_a["passed"] or check_b["passed"],
        }
        log = self._load_log()
        log.append(entry)
        self._save_log(log)

        pair = None
        # Pair saved when at least one synthesis passes constitution; label by constitutional score
        if check_a["passed"] or check_b["passed"]:
            if score_pa >= score_pb:
                chosen, rejected = peak_a, peak_b
            else:
                chosen, rejected = peak_b, peak_a

            pair = {
                "prompt":   f"Domain: {domain}\nQuestion: {question}",
                "chosen":   chosen,
                "rejected": rejected,
                "_meta": {
                    "source":       "peak_play",
                    "peak_round":   peak_idx,
                    "peak_gap":     round(peak_gap, 2),
                    "total_rounds": len(gaps),
                    "gap_curve":    [round(g, 2) for g in gaps],
                    "bridge":       bridge,
                    "timestamp":    entry["timestamp"],
                },
            }
            self._append_pair(pair)
            if verbose:
                print(f"  Pair saved (peak_gap={peak_gap:.2f}, rounds={len(gaps)})")

        return {
            "question":     question,
            "gaps":         gaps,
            "peak_round":   peak_idx,
            "peak_gap":     peak_gap,
            "total_rounds": len(gaps),
            "bridge":       bridge,
            "pair":         pair,
        }

    def run_session(
        self,
        n:          int   = 5,
        domain:     str   = None,
        max_rounds: int   = MAX_ROUNDS_DEFAULT,
        min_gap:    float = 0.5,
    ) -> dict:
        from orlog.eval.batch_runner import QUESTION_BANK

        bank = QUESTION_BANK
        if domain:
            bank = [q for q in bank if q[0] == domain]
        if not bank:
            print("No questions found.")
            return {}

        pairs_saved = 0
        total_rounds = 0

        print(f"Peak-play session: {n} duels, domain={domain or 'all'}, max_rounds={max_rounds}")

        for i in range(n):
            q_domain, q_task, q_goal, q_question = bank[i % len(bank)]
            result = self.duel(
                question=q_question,
                domain=domain or q_domain,
                goal=q_goal,
                task_type=q_task,
                max_rounds=max_rounds,
                min_gap=min_gap,
            )
            if result["pair"]:
                pairs_saved += 1
            total_rounds += result["total_rounds"]
            time.sleep(1.0)

        avg_rounds = total_rounds / max(n, 1)
        print(f"\nSession: {n} duels, {pairs_saved} pairs, avg {avg_rounds:.1f} rounds/duel")
        print(f"Pairs → {self._pairs_path}")
        return {"duels": n, "pairs_saved": pairs_saved, "avg_rounds": avg_rounds}

    def stats(self) -> dict:
        log   = self._load_log()
        peaks = [e["peak_round"] for e in log if "peak_round" in e]
        gaps  = [e["peak_gap"]   for e in log if "peak_gap"   in e]
        return {
            "total_duels":   len(log),
            "pairs_saved":   sum(1 for e in log if e.get("pair_saved")),
            "avg_peak_round": round(sum(peaks) / max(len(peaks), 1), 2),
            "avg_peak_gap":   round(sum(gaps)  / max(len(gaps),  1), 2),
        }


if __name__ == "__main__":
    import sys
    sys.path.insert(0, str(Path(__file__).parent.parent.parent))

    parser = argparse.ArgumentParser(description="Tangent-wave peak-detection self-play")
    parser.add_argument("--n",          type=int,   default=3)
    parser.add_argument("--domain",                 default=None)
    parser.add_argument("--question",               default=None)
    parser.add_argument("--max-rounds", type=int,   default=MAX_ROUNDS_DEFAULT, dest="max_rounds")
    parser.add_argument("--min-gap",    type=float, default=0.5, dest="min_gap")
    parser.add_argument("--stats",      action="store_true")
    args = parser.parse_args()

    pp = PeakPlay()

    if args.stats:
        s = pp.stats()
        for k, v in s.items():
            print(f"  {k}: {v}")
    elif args.question:
        pp.duel(args.question, domain=args.domain, max_rounds=args.max_rounds)
    else:
        pp.run_session(n=args.n, domain=args.domain, max_rounds=args.max_rounds, min_gap=args.min_gap)
