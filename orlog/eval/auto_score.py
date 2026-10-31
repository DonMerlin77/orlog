"""
orlog/eval/auto_score.py — Automatic Orlog scorer.

Runs after every orlog.run() call. Judges the synthesis on 5 dimensions,
returns a 0-10 score, and calls orlog.score() to close the DPO loop.

5 dimensions (2 points each):
  1. Clarity      — is the verdict unambiguous and actionable?
  2. Specificity  — concrete, not generic; names real things
  3. Tension      — did the council surface a real disagreement?
  4. Blind spot   — did the synthesis name something the user missed?
  5. Momentum     — does it move forward, not just describe?

Usage:
    from orlog.eval.auto_score import score_run, auto_score

    # Score a completed run dict (does NOT call orlog.score())
    score = score_run(result)

    # Score + record in one call (calls orlog.score() automatically)
    score = auto_score(orlog_instance, result)
"""

import os
import sys
import json
from pathlib import Path
from dotenv import load_dotenv

sys.path.insert(0, str(Path(__file__).parents[2]))
from llm_router import fast_call

load_dotenv(Path(__file__).parent.parent.parent / ".env")

SCORE_DIMENSIONS = [
    ("Clarity",     "Is the VERDICT specific and unambiguous? Could someone act on it without asking a follow-up question?"),
    ("Specificity", "Does the output name real, concrete things — not generic advice that applies to any project?"),
    ("Tension",     "Did the council surface a real disagreement between perspectives, not just converge on the same answer?"),
    ("Blind spot",  "Did the synthesis identify something the user likely hadn't considered — a genuine non-obvious insight?"),
    ("Momentum",    "Does the output move the project forward, or does it describe the problem without creating motion?"),
]

SCORER_SYSTEM = """You are a calibrated evaluator of AI reasoning output quality.
Score outputs honestly — a 5 means average, 8 means genuinely excellent, 10 is reserved for outputs that would change how a practitioner thinks about the problem.
Do not inflate scores. An output that sounds smart but gives no actionable direction scores 3-4."""


def score_run(result: dict, api_key: str = None) -> dict:
    """
    Score a completed orlog.run() result dict.
    Returns {total, breakdown, feedback} without calling orlog.score().
    """
    synthesis_raw = result.get("synthesis_raw") or json.dumps(result.get("synthesis", {}))
    question      = result.get("question", "")
    domain        = result.get("domain", "")
    lenses        = result.get("lenses", [])

    dim_scores = []
    feedback   = []

    for dim_name, dim_question in SCORE_DIMENSIONS:
        prompt = (
            f"Domain: {domain}\n"
            f"Question asked: {question}\n"
            f"Lenses used: {', '.join(lenses)}\n\n"
            f"OUTPUT TO SCORE:\n{synthesis_raw[:2000]}\n\n"
            f"Dimension: {dim_name}\n"
            f"Question: {dim_question}\n\n"
            f"Score this dimension 0-2 (0=fails, 1=partial, 2=strong). "
            f"Reply with: SCORE: <number>\nFEEDBACK: <one sentence>"
        )
        response = fast_call(SCORER_SYSTEM, prompt, 80)

        score_line    = next((l for l in response.split("\n") if "SCORE:" in l), "SCORE: 1")
        feedback_line = next((l for l in response.split("\n") if "FEEDBACK:" in l), "FEEDBACK: n/a")

        try:
            s = int("".join(c for c in score_line.split(":")[-1] if c.isdigit())[:1])
            s = max(0, min(2, s))
        except (ValueError, IndexError):
            s = 1

        fb = feedback_line.split(":", 1)[-1].strip()
        dim_scores.append(s)
        feedback.append(f"{dim_name}: {fb}")

    raw_total  = sum(dim_scores)
    normalized = round((raw_total / 10) * 10, 1)

    return {
        "run_id":    result.get("id"),
        "total":     normalized,
        "raw":       raw_total,
        "breakdown": {SCORE_DIMENSIONS[i][0]: dim_scores[i] for i in range(len(dim_scores))},
        "feedback":  feedback,
        "domain":    domain,
        "question":  question[:100],
    }


def auto_score(orlog_instance, result: dict) -> dict:
    """
    Score a result AND record it via orlog.score() in one call.
    This closes the DPO loop automatically.
    """
    scored = score_run(result, api_key=orlog_instance.api_key)
    orlog_instance.score(
        run_id=result["id"],
        orlog_score=scored["total"],
        notes=" | ".join(scored["feedback"]),
    )
    return scored


def print_score(scored: dict):
    print(f"\n  Orlog Score: {scored['total']}/10")
    for dim, s in scored["breakdown"].items():
        bar = "██" * s + "░░" * (2 - s)
        print(f"  {bar}  {dim}")
    print()
    for fb in scored["feedback"]:
        print(f"  · {fb}")
