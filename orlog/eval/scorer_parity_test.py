"""
orlog/eval/scorer_parity_test.py — Tests whether the Constitution scorer is format-agnostic.

Orlog identified this gap in the A/B test methodology:
  The +0.090 delta (plain vs rune labels) could be scorer bias, not model quality difference.
  If Claude Haiku sees rune Unicode (ᚠ_anchor:) and scores differently than (ANCHOR:)
  for identical reasoning content — the A/B result is a measurement artifact.

Method:
  - Load rune_output from existing A/B results (logs/rune_ab_results.json)
  - Create a label-swapped version of each: same words, only prefix changed
  - Score both through Constitution
  - If scores are identical → scorer is format-agnostic, A/B is valid
  - If scores diverge → scorer has format bias, A/B result is partially invalid

Usage:
    python3 -m orlog.eval.scorer_parity_test
"""

import json
import re
from pathlib import Path
from orlog.eval.constitution import Constitution

RESULTS_PATH = Path("logs/rune_ab_results.json")
PARITY_PATH  = Path("logs/scorer_parity_results.json")
PARITY_PATH.parent.mkdir(exist_ok=True)

RUNE_TO_PLAIN = {
    "ᚠ_anchor": "ANCHOR",
    "ᚢ_tension": "TENSION",
    "ᚷ_gap": "GAP",
    "ᛉ_risk": "RISK",
}


def relabel(text: str) -> str:
    for rune, plain in RUNE_TO_PLAIN.items():
        text = text.replace(rune + ":", plain + ":")
    return text


def run():
    if not RESULTS_PATH.exists():
        print("No A/B results found. Run rune_ab_test.py first.")
        return

    data = json.loads(RESULTS_PATH.read_text())
    constitution = Constitution()
    results = []

    print(f"Running scorer parity test on {len(data)} prompts...")
    print("Testing: same reasoning content, labels swapped — does score change?\n")

    for i, row in enumerate(data):
        q            = row["question"]
        rune_output  = row.get("rune_output", "")
        plain_swap   = relabel(rune_output)

        print(f"[{i+1}/{len(data)}] {q[:60]}...")

        rune_score = constitution.check(question=q, synthesis=rune_output)
        swap_score = constitution.check(question=q, synthesis=plain_swap)

        delta = round(swap_score["score"] - rune_score["score"], 3)
        flag  = " *** BIAS DETECTED" if abs(delta) > 0.02 else ""
        print(f"  Rune label: {rune_score['score']:.3f}  Plain label (same text): {swap_score['score']:.3f}  delta={delta:+.3f}{flag}")

        results.append({
            "question":    q,
            "rune_score":  rune_score["score"],
            "swap_score":  swap_score["score"],
            "delta":       delta,
            "biased":      abs(delta) > 0.02,
        })

    PARITY_PATH.write_text(json.dumps(results, indent=2))

    mean_delta   = sum(r["delta"] for r in results) / len(results)
    biased_count = sum(1 for r in results if r["biased"])
    max_delta    = max(r["delta"] for r in results)
    min_delta    = min(r["delta"] for r in results)

    print(f"\n{'='*55}")
    print(f"PARITY RESULTS ({len(results)} prompts)")
    print(f"{'='*55}")
    print(f"Mean delta (plain_swap - rune): {mean_delta:+.4f}")
    print(f"Prompts with bias (|delta|>0.02): {biased_count}/{len(results)}")
    print(f"Range: {min_delta:+.3f} to {max_delta:+.3f}")

    print(f"\nCONCLUSION:")
    if biased_count == 0 and abs(mean_delta) < 0.01:
        print("  Scorer is FORMAT-AGNOSTIC. A/B result reflects model behavior, not scorer bias.")
        print("  The plain-label migration decision is valid.")
    elif biased_count > 0:
        print(f"  Scorer shows FORMAT BIAS on {biased_count} prompts.")
        print("  The A/B delta is partially a scorer artifact.")
        print("  Audit constitution.py invariants for label-sensitive logic before trusting A/B results.")
    else:
        print(f"  Small systematic drift ({mean_delta:+.4f}) — likely noise, not bias.")
        print("  A/B result is probably valid but run more prompts to confirm.")

    print(f"\nFull results: {PARITY_PATH}")


if __name__ == "__main__":
    run()
