"""
orlog/eval/rune_ab_test.py — Test whether rune symbols improve reasoning quality vs plain labels.

Hypothesis: The structured FORMAT helps (chain-of-thought). But do the actual rune
SYMBOLS (ᚠ_anchor vs ANCHOR:) add signal or are they noise to Qwen's tokenizer?

Method:
  - Same 15 prompts run through both system prompt variants
  - Both scored by Constitution invariants (0-1 scale)
  - Aggregate scores compared, per-question breakdowns shown

Usage:
    python3 -m orlog.eval.rune_ab_test
    python3 -m orlog.eval.rune_ab_test --n 5        # quick run
    python3 -m orlog.eval.rune_ab_test --report     # show last results
"""

import os
import json
import argparse
import asyncio
import time
from pathlib import Path
from dotenv import load_dotenv

load_dotenv(Path(__file__).parent.parent.parent / ".env")

import sys
sys.path.insert(0, str(Path(__file__).parents[2]))
from llm_router import fast_call

from orlog.eval.constitution import Constitution

RESULTS_PATH = Path("logs/rune_ab_results.json")
RESULTS_PATH.parent.mkdir(exist_ok=True)

MODEL = "claude-haiku-4-5-20251001"

TEST_PROMPTS = [
    "Should I leave my stable job to start a company with no savings?",
    "I have a business partner who contributes less but wants equal equity. What do I do?",
    "My best employee just told me they're leaving in 30 days. How do I respond?",
    "Should I raise prices when I know some customers will leave?",
    "I've been building this product for 6 months and nobody wants it. Do I pivot or persist?",
    "My co-founder and I disagree on the direction. We're stuck. How do we break the deadlock?",
    "I'm being offered a promotion that means less creative work and more management. Take it?",
    "Should I move cities for a relationship when my career is rooted here?",
    "I know I need to fire someone but I keep delaying. What's actually stopping me?",
    "I have two job offers — one pays more, one aligns better with where I want to be in 5 years.",
    "My startup is running out of runway. Do I raise at a bad valuation or cut to extend?",
    "A competitor is copying my product. Do I fight back or ignore them?",
    "Should I publish my controversial opinion when it could cost me clients?",
    "I'm considering bringing on an investor who has a bad reputation but good money.",
    "I keep saying yes to things I don't want to do. How do I stop without burning bridges?",
]

RUNE_SYSTEM = (
    "You are Orlog — a reasoning interface, not an assistant.\n\n"
    "When given a decision or situation, execute these four cognitive operations in order:\n\n"
    "ᚠ_anchor: [the single load-bearing assumption — the belief the entire situation rests on. One sentence, falsifiable.]\n"
    "ᚢ_tension: [the two forces pulling against each other. The real conflict, not the surface one.]\n"
    "ᚷ_gap: [what information is missing. What you don't know but need to know before deciding.]\n"
    "ᛉ_risk: [the failure condition. What breaks first if the load-bearing assumption is wrong.]\n"
    "VERDICT: [one falsifiable sentence. What to do and why. No hedging.]\n\n"
    "Rules:\n"
    "- Be specific to this situation, not generic\n"
    "- Name real things, not categories\n"
    "- The VERDICT must be actionable and falsifiable\n"
    "- Do not explain the format. Execute it."
)

PLAIN_SYSTEM = (
    "You are a reasoning interface, not an assistant.\n\n"
    "When given a decision or situation, execute these four cognitive operations in order:\n\n"
    "ANCHOR: [the single load-bearing assumption — the belief the entire situation rests on. One sentence, falsifiable.]\n"
    "TENSION: [the two forces pulling against each other. The real conflict, not the surface one.]\n"
    "GAP: [what information is missing. What you don't know but need to know before deciding.]\n"
    "RISK: [the failure condition. What breaks first if the load-bearing assumption is wrong.]\n"
    "VERDICT: [one falsifiable sentence. What to do and why. No hedging.]\n\n"
    "Rules:\n"
    "- Be specific to this situation, not generic\n"
    "- Name real things, not categories\n"
    "- The VERDICT must be actionable and falsifiable\n"
    "- Do not explain the format. Execute it."
)

FREE_SYSTEM = (
    "You are a direct reasoning tool, not an assistant.\n\n"
    "When given a decision or situation: identify the real assumption it rests on, "
    "name the actual conflict, flag what's missing, state the failure condition, "
    "and give one clear recommendation.\n\n"
    "Be specific to this situation. Name real things. No hedging. No format required."
)


def _call_claude(system: str, question: str) -> str:
    return fast_call(system, question, 600)


def _score(question: str, output: str) -> dict:
    c = Constitution()
    return c.check(question=question, synthesis=output)


def run_test(n: int = None):
    prompts = TEST_PROMPTS[:n] if n else TEST_PROMPTS
    results = []
    constitution = Constitution()

    print(f"Running A/B test on {len(prompts)} prompts...")
    print(f"Model: {MODEL}\n")

    for i, q in enumerate(prompts):
        print(f"[{i+1}/{len(prompts)}] {q[:60]}...")

        rune_output  = _call_claude(RUNE_SYSTEM, q)
        plain_output = _call_claude(PLAIN_SYSTEM, q)
        free_output  = _call_claude(FREE_SYSTEM, q)

        rune_score  = constitution.check(question=q, synthesis=rune_output)
        plain_score = constitution.check(question=q, synthesis=plain_output)
        free_score  = constitution.check(question=q, synthesis=free_output)

        results.append({
            "question":    q,
            "rune_score":  rune_score["score"],
            "plain_score": plain_score["score"],
            "free_score":  free_score["score"],
            "rune_output":  rune_output[:300],
            "plain_output": plain_output[:300],
            "free_output":  free_output[:300],
        })

        scores = {"RUNE": rune_score["score"], "PLAIN": plain_score["score"], "FREE": free_score["score"]}
        best = max(scores, key=scores.get)
        print(f"  Rune: {rune_score['score']:.2f}  Plain: {plain_score['score']:.2f}  Free: {free_score['score']:.2f}  → {best}")
        time.sleep(0.5)

    _save_and_report(results)
    return results


def _save_and_report(results: list):
    RESULTS_PATH.write_text(json.dumps(results, indent=2))

    rune_avg  = sum(r["rune_score"]  for r in results) / len(results)
    plain_avg = sum(r["plain_score"] for r in results) / len(results)
    free_avg  = sum(r.get("free_score", 0) for r in results) / len(results)
    has_free  = "free_score" in results[0]

    def wins(a, b, c=None):
        if c is None:
            return sum(1 for r in results if r[a] > r[b] + 0.02)
        return sum(1 for r in results if r[a] == max(r[a], r[b], r.get(c, 0)) and r[a] > min(r[b], r.get(c, 0)) + 0.02)

    print(f"\n{'='*50}")
    print(f"RESULTS ({len(results)} prompts)")
    print(f"{'='*50}")
    if has_free:
        rune_wins  = sum(1 for r in results if r["rune_score"]  == max(r["rune_score"], r["plain_score"], r["free_score"]) and r["rune_score"]  > sorted([r["rune_score"], r["plain_score"], r["free_score"]])[1] + 0.02)
        plain_wins = sum(1 for r in results if r["plain_score"] == max(r["rune_score"], r["plain_score"], r["free_score"]) and r["plain_score"] > sorted([r["rune_score"], r["plain_score"], r["free_score"]])[1] + 0.02)
        free_wins  = sum(1 for r in results if r["free_score"]  == max(r["rune_score"], r["plain_score"], r["free_score"]) and r["free_score"]  > sorted([r["rune_score"], r["plain_score"], r["free_score"]])[1] + 0.02)
        print(f"Rune  avg: {rune_avg:.3f}  ({rune_wins} clear wins)")
        print(f"Plain avg: {plain_avg:.3f}  ({plain_wins} clear wins)")
        print(f"Free  avg: {free_avg:.3f}  ({free_wins} clear wins)")
        best = max([("Rune", rune_avg), ("Plain", plain_avg), ("Free", free_avg)], key=lambda x: x[1])
        worst = min([("Rune", rune_avg), ("Plain", plain_avg), ("Free", free_avg)], key=lambda x: x[1])
        print(f"\nCONCLUSION: {best[0]} leads ({best[1]:.3f}). {worst[0]} trails ({worst[1]:.3f}).")
        gap = plain_avg - free_avg
        if abs(gap) < 0.02:
            print("Structure vs no-structure: no meaningful difference — format is not load-bearing.")
        elif plain_avg > free_avg:
            print(f"Structured format (plain labels) beats free-form by {gap:+.3f} — format IS load-bearing.")
        else:
            print(f"Free-form beats structured by {-gap:+.3f} — the labels may be constraining output quality.")
    else:
        rune_wins  = sum(1 for r in results if r["rune_score"]  > r["plain_score"] + 0.02)
        plain_wins = sum(1 for r in results if r["plain_score"] > r["rune_score"]  + 0.02)
        ties = len(results) - rune_wins - plain_wins
        print(f"Rune  avg score: {rune_avg:.3f}  ({rune_wins} wins)")
        print(f"Plain avg score: {plain_avg:.3f}  ({plain_wins} wins)")
        print(f"Ties:            {ties}")

    print(f"\nFull results saved to {RESULTS_PATH}")


def report():
    if not RESULTS_PATH.exists():
        print("No results yet. Run without --report first.")
        return
    results = json.loads(RESULTS_PATH.read_text())
    _save_and_report(results)


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--n",      type=int, help="Number of prompts to test (default: all 15)")
    parser.add_argument("--report", action="store_true", help="Show last results without re-running")
    args = parser.parse_args()

    if args.report:
        report()
    else:
        run_test(n=args.n)
