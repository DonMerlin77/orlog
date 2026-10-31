"""
orlog/eval/adversarial.py — Adversarial DPO pair generator.

Feeds Mimir "trap" prompts engineered to trigger each known failure mode,
scores the output, generates a corrected response, and writes a DPO pair.
This is how Mimir learns its own weak spots — by failing on purpose.

Failure modes targeted:
  VAGUE_VERDICT    — prompt so abstract the model produces non-verdicts
  FALSE_ANCHOR     — prompt has an obvious surface assumption to distract from the real one
  HALLUCINATED_SPEC — prompt invites made-up statistics or named facts
  HEDGE_RISK       — prompt emotionally loaded so model softens the risk
  SYMMETRIC_TENSION — prompt frames a false either/or so tension reads as "cost vs benefit"
  UNANSWERABLE_GAP  — prompt where model invents a gap that can't actually be researched

Run:
    python3 -m orlog.eval.adversarial --mode generate   # generate pairs from traps
    python3 -m orlog.eval.adversarial --mode score      # score latest trap responses
    python3 -m orlog.eval.adversarial --mode report     # print failure summary
    python3 -m orlog.eval.adversarial --n 10            # run N traps per mode
"""

import os
import sys
import json
import argparse
import time
from pathlib import Path
from datetime import datetime

from dotenv import load_dotenv

sys.path.insert(0, str(Path(__file__).parents[2]))
from llm_router import fast_call, quality_call

load_dotenv(Path(__file__).parents[2] / ".env")

TRAP_LOG  = Path("logs/adversarial_traps.jsonl")
PAIR_LOG  = Path("logs/modal_dpo_pairs.jsonl")

FAILURE_MODES = {
    "VAGUE_VERDICT": [
        "What is the right thing to do when everything feels wrong?",
        "How do I know if I'm on the right path?",
        "What does success actually mean for someone like me?",
        "Is this the right time in my life to make a change?",
        "Should I follow my passion or be practical?",
    ],
    "FALSE_ANCHOR": [
        "I know the real problem is I don't have enough money — so should I take the higher-paying job even if I hate it?",
        "Obviously the issue is I'm not working hard enough. How do I fix my discipline?",
        "The real bottleneck is my cofounder. Should I fire them?",
        "My team isn't motivated — that's clearly why we're failing. What do I do?",
        "The market just isn't ready yet. Should I wait it out or pivot?",
    ],
    "HALLUCINATED_SPEC": [
        "Is the 80/20 rule actually true for B2B SaaS customer churn?",
        "Studies show most startups fail because of bad hiring. Is that what's killing us?",
        "I heard that 70% of partnerships fail in year two. Should I restructure ours now?",
        "What does the data say about the best time to raise a Series A?",
        "Is it true that remote teams underperform by 23% on creative tasks?",
    ],
    "HEDGE_RISK": [
        "My mom is sick and I don't know whether to stay near home or pursue my dream job abroad.",
        "I've been offered my dream role but it means leaving my team during a crisis. What should I do?",
        "My closest friend and I started a business together. It's failing. Should I walk away?",
        "I want to leave my religion but my entire family and community are in it. What do I do?",
        "I found out my business partner has been lying to me. Should I confront them?",
    ],
    "SYMMETRIC_TENSION": [
        "Should I spend more money on marketing or save cash for runway?",
        "Is it better to go fast and break things or slow down and be careful?",
        "Should I focus on acquiring new customers or retaining existing ones?",
        "Do I raise prices and lose customers or keep prices low and burn cash?",
        "Should I build the product or sell it first?",
    ],
    "UNANSWERABLE_GAP": [
        "What will the AI industry look like in 10 years and how do I position for it?",
        "Will my relationship survive if we move in together?",
        "Is my business idea good enough to succeed?",
        "What does my customer actually want deep down?",
        "Will this decision make me happy in the long run?",
    ],
}

SCORE_SYSTEM = """You are evaluating a reasoning trace for quality. Score each invariant 0 or 1.

INVARIANTS:
1. ANCHOR is specific and falsifiable (not "the assumption that X matters")
2. TENSION names two forces that cannot both win (not "cost vs benefit")
3. GAP is something the user can actually research in the next 7 days
4. RISK names the specific mechanism of failure (not "it might not work")
5. VERDICT is falsifiable within 30 days and names real things (not categories)
6. No hedging, moralizing, or "however" after the verdict

For each invariant, output exactly: PASS or FAIL
Then output: TOTAL: N/6
Then output: FAILURE_MODE: [the dominant failure from this list: VAGUE_VERDICT, FALSE_ANCHOR, HALLUCINATED_SPEC, HEDGE_RISK, SYMMETRIC_TENSION, UNANSWERABLE_GAP, NONE]
Then output: FIXABLE: YES or NO (NO only if the question itself is unanswerable)"""

CORRECT_SYSTEM = """You are correcting a failed reasoning trace. The original response had a specific failure.

Your job: produce a correct response to the same question that passes all six invariants:
1. Anchor — specific, falsifiable, the real load-bearing assumption
2. Tension — two forces that cannot both win simultaneously (not "cost vs benefit")
3. Gap — something researchable in 7 days
4. Risk — specific mechanism of failure
5. Verdict — falsifiable within 30 days, names real things

Format exactly:
ANCHOR: [one sentence]
TENSION: [one sentence]
GAP: [one sentence]
RISK: [one sentence]
VERDICT: [one sentence]

Do not explain. Do not hedge. Execute the format."""


def _get_client():
    return None


def _get_mimir():
    try:
        import modal
        return modal.Function.from_name("orlog-inference", "infer")
    except Exception:
        return None


def _run_mimir(question: str) -> tuple[str, str]:
    mimir = _get_mimir()
    if mimir:
        try:
            import asyncio
            result = asyncio.get_event_loop().run_until_complete(
                asyncio.wait_for(mimir.remote.aio(question), timeout=120)
            )
            return result, "mimir"
        except Exception as e:
            print(f"  Mimir failed ({e.__class__.__name__}), using Claude")
    from orlog.app.main import SYSTEM, _build_system
    return quality_call(_build_system(), question, 800), "quality_router"


def score_trace(trace: str, question: str) -> dict:
    raw = fast_call(SCORE_SYSTEM, f"Question: {question}\n\nTrace:\n{trace}", 200)

    lines = raw.splitlines()
    score = sum(1 for l in lines if "PASS" in l)

    failure_mode = "UNKNOWN"
    fixable = True
    for line in lines:
        if line.startswith("FAILURE_MODE:"):
            failure_mode = line.split(":", 1)[1].strip()
        if line.startswith("FIXABLE:"):
            fixable = "YES" in line

    return {"score": score, "failure_mode": failure_mode, "fixable": fixable, "raw": raw}


def correct_trace(question: str, bad_trace: str, failure_mode: str) -> str:
    return quality_call(
        CORRECT_SYSTEM,
        f"Question: {question}\n\n"
        f"Failed response (failure: {failure_mode}):\n{bad_trace}\n\n"
        f"Produce the correct response now:",
        600,
    )


def run_traps(n_per_mode: int = 2) -> list[dict]:
    results = []
    total_pairs = 0

    for mode, prompts in FAILURE_MODES.items():
        batch = prompts[:n_per_mode]
        print(f"\n[{mode}] Running {len(batch)} trap(s)...")

        for question in batch:
            print(f"  Q: {question[:70]}...")
            trace, backend = _run_mimir(question)
            scored = score_trace(trace, question)

            print(f"  Score: {scored['score']}/6 — {scored['failure_mode']} — backend: {backend}")

            pair = None
            if scored["score"] < 5 and scored["fixable"]:
                corrected = correct_trace(question, trace, scored["failure_mode"])
                pair = {
                    "prompt":   question,
                    "chosen":   corrected,
                    "rejected": trace,
                    "source":   f"adversarial_{mode}",
                    "score_rejected": scored["score"],
                    "ts":       datetime.now().isoformat(),
                }
                with PAIR_LOG.open("a") as f:
                    f.write(json.dumps(pair) + "\n")
                total_pairs += 1
                print(f"  DPO pair written (score {scored['score']}/6 → corrected)")
            elif scored["score"] >= 5:
                print(f"  Passed — no pair needed (Mimir handled the trap)")

            entry = {
                "question":     question,
                "mode":         mode,
                "backend":      backend,
                "trace":        trace,
                "score":        scored["score"],
                "failure_mode": scored["failure_mode"],
                "fixable":      scored["fixable"],
                "pair_written": pair is not None,
                "ts":           datetime.now().isoformat(),
            }
            results.append(entry)
            with TRAP_LOG.open("a") as f:
                f.write(json.dumps(entry) + "\n")

            time.sleep(0.5)

    print(f"\nDone. {total_pairs} DPO pairs written to {PAIR_LOG}")
    print(f"Total pairs in file: {sum(1 for _ in PAIR_LOG.open()) if PAIR_LOG.exists() else 0}")
    return results


def report():
    if not TRAP_LOG.exists():
        print("No trap log found. Run --mode generate first.")
        return

    entries = [json.loads(l) for l in TRAP_LOG.read_text().splitlines() if l.strip()]
    if not entries:
        print("No entries.")
        return

    by_mode: dict = {}
    for e in entries:
        m = e.get("mode", "?")
        by_mode.setdefault(m, []).append(e)

    print(f"\n{'='*60}")
    print(f"ADVERSARIAL REPORT — {len(entries)} traps run")
    print(f"{'='*60}")

    for mode, runs in by_mode.items():
        avg = sum(r["score"] for r in runs) / len(runs)
        pairs = sum(1 for r in runs if r.get("pair_written"))
        print(f"\n{mode}")
        print(f"  Runs: {len(runs)} | Avg score: {avg:.1f}/6 | Pairs generated: {pairs}")
        fails = [r for r in runs if r["score"] < 5]
        for f in fails[-2:]:
            print(f"  ✗ [{f['score']}/6] {f['question'][:60]}...")


def main():
    parser = argparse.ArgumentParser(description="Adversarial DPO pair generator")
    parser.add_argument("--mode", choices=["generate", "score", "report"], default="generate")
    parser.add_argument("--n",    type=int, default=2, help="Traps per failure mode")
    args = parser.parse_args()

    import sys
    sys.path.insert(0, str(Path(__file__).parents[2]))

    if args.mode == "generate":
        run_traps(n_per_mode=args.n)
    elif args.mode == "report":
        report()


if __name__ == "__main__":
    main()
