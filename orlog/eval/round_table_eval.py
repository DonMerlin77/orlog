"""
Round Table Eval — Layer 3 proof.

Tests: do cognitive styles actually produce different perspectives?
Does synthesis produce something better than any individual style?
Two questions across different domains.
"""
import sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).parent.parent.parent))
from dotenv import load_dotenv
load_dotenv(Path(__file__).parent.parent.parent / ".env")

from orlog.council.council import RoundTable

QUESTIONS = [
    {
        "question": "Should we launch the product now with known bugs, or delay 6 weeks for a cleaner v1?",
        "context": {"goal": "reach first paying customer", "domain": "software", "current_state": "MVP works but has edge case crashes"},
    },
    {
        "question": "We have $500 left in budget. Do we spend it on paid ads or on better product photos?",
        "context": {"goal": "first sale on Shopify store", "domain": "store", "current_state": "0 sales, 80 visitors/week organic"},
    },
]


def run_eval():
    print("=" * 60)
    print("  Orlog Round Table Eval — Layer 3 Proof")
    print("=" * 60)

    rt = RoundTable()

    for q in QUESTIONS:
        result = rt.deliberate(q["question"], q["context"])
        rt.print_result(result)

        # Check: did each style produce a distinct perspective?
        texts = list(result["perspectives"].values())
        unique_starts = len(set(t[:40] for t in texts))
        styles_differ = unique_starts == len(texts)

        # Check: synthesis is longer and references multiple styles
        synth = result["synthesis"].lower()
        synth_quality = (
            len(result["synthesis"]) > 200
            and ("verdict" in synth or "tension" in synth or "blind" in synth)
        )

        print(f"  Styles produced distinct perspectives: {'PASS' if styles_differ else 'FAIL'}")
        print(f"  Synthesis has structure (verdict/tension/blind spot): {'PASS' if synth_quality else 'FAIL'}")
        print()


if __name__ == "__main__":
    run_eval()
