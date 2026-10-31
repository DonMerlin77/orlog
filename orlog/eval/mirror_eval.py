"""
Mirror/Narcissus/GapAnalysis Eval — orlog/mirror/ proof.

Tests: stranger vs creator divergence, gap identification quality.
Input: a landing page blurb with intentional blind spots built in.

Usage:
    python3 orlog/eval/mirror_eval.py
"""

import sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).parent.parent.parent))
from dotenv import load_dotenv
load_dotenv(Path(__file__).parent.parent.parent / ".env")

from orlog.mirror import Mirror, Narcissus, GapAnalysis

TEST_OUTPUT = """
Welcome to FlowDesk.
The productivity tool built for teams who care about focus.
Organize tasks. Track projects. Work better.
Start free. No credit card required.
"""

TEST_INTENT = (
    "We built FlowDesk for remote teams burned out by Slack and Jira. "
    "The hero is someone who loses 3 hours a day to context switching. "
    "We want them to feel immediate relief just reading the headline."
)

TEST_CONTEXT = {
    "creator": "2-person indie team",
    "audience": "remote teams, 5-50 people",
    "differentiator": "focus-first design — no notifications by default",
}


def score_result(audit: dict) -> dict:
    mirror_has_confusion    = any(w in audit["mirror"].lower() for w in ["confus", "unclear", "vague", "missing", "don't know", "don't understand", "what", "who"])
    narcissus_has_pride     = any(w in audit["narcissus"].lower() for w in ["strong", "works", "proud", "clear", "effective", "succeeds", "intent"])
    gap_has_invisible       = "INVISIBLE" in audit["gap"]
    gap_has_fixes           = "FIXES" in audit["gap"]
    perspectives_diverge    = audit["mirror"][:100].lower() != audit["narcissus"][:100].lower()

    score = sum([mirror_has_confusion, narcissus_has_pride, gap_has_invisible, gap_has_fixes, perspectives_diverge])
    return {
        "mirror_has_confusion":  mirror_has_confusion,
        "narcissus_has_pride":   narcissus_has_pride,
        "gap_has_invisible":     gap_has_invisible,
        "gap_has_fixes":         gap_has_fixes,
        "perspectives_diverge":  perspectives_diverge,
        "score": score,
        "max":   5,
    }


def run_eval():
    print("=" * 60)
    print("  Orlog Mirror/Narcissus/Gap Eval")
    print("=" * 60)
    print()

    g     = GapAnalysis()
    audit = g.full_audit(
        output=TEST_OUTPUT,
        output_type="landing page copy",
        intent=TEST_INTENT,
        context=TEST_CONTEXT,
    )
    scored = score_result(audit)

    print("MIRROR (stranger):")
    print(audit["mirror"])
    print()
    print("NARCISSUS (creator):")
    print(audit["narcissus"])
    print()
    print("GAP ANALYSIS:")
    print(audit["gap"])
    print()
    print("=" * 60)

    checks = [
        ("Mirror surfaces confusion/gaps",   scored["mirror_has_confusion"]),
        ("Narcissus expresses pride/intent",  scored["narcissus_has_pride"]),
        ("Gap identifies INVISIBLE section",  scored["gap_has_invisible"]),
        ("Gap produces FIXES",                scored["gap_has_fixes"]),
        ("Perspectives genuinely diverge",    scored["perspectives_diverge"]),
    ]
    for label, passed in checks:
        print(f"  {'✓' if passed else '✗'} {label}")

    pct = (scored["score"] / scored["max"]) * 100
    print()
    print(f"  Total: {scored['score']}/{scored['max']} ({pct:.0f}%)")

    if pct >= 80:
        print("  Mirror/Narcissus/Gap PROVEN — divergence confirmed.")
    elif pct >= 60:
        print("  PARTIAL — perspectives not diverging enough. Tune prompts.")
    else:
        print("  FAIL — Mirror and Narcissus are too similar. Rework system prompts.")
    print()


if __name__ == "__main__":
    run_eval()
