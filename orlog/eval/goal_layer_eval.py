"""
Goal Layer Eval — Layer 2 proof.

Tests: domain detection, clarifying question quality, toybox bin selection.
5 test cases spanning clear → ambiguous goals across different domains.

Usage:
    python3 orlog/eval/goal_layer_eval.py
"""

import sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).parent.parent.parent))
from dotenv import load_dotenv
load_dotenv(Path(__file__).parent.parent.parent / ".env")

from orlog.goal.goal_layer import GoalLayer

TEST_CASES = [
    {
        "id": 1,
        "goal": "I want to build an H2O powered car from scratch, starting with the fuel cell system",
        "expected_domain": "hardware",
        "expected_bin": "toybox/hardware",
        "should_clarify": False,
        "description": "Hardware — specific enough, should detect correctly",
    },
    {
        "id": 2,
        "goal": "I need a Shopify store for handmade ceramic jewelry with a supplier in Mexico",
        "expected_domain": "store",
        "expected_bin": "toybox/stores",
        "should_clarify": False,
        "description": "E-commerce — clear domain, clear enough goal",
    },
    {
        "id": 3,
        "goal": "build something",
        "expected_domain": "generic",
        "expected_bin": "toybox/generic",
        "should_clarify": True,
        "description": "Maximally vague — should flag clarification needed",
    },
    {
        "id": 4,
        "goal": "I'm writing a sci-fi novel set in 2400 where humanity has colonized 12 star systems",
        "expected_domain": "creative_writing",
        "expected_bin": "toybox/creative_writing",
        "should_clarify": False,
        "description": "Creative writing — keyword 'novel' should now match directly",
    },
    {
        "id": 5,
        "goal": "I want to automate my stock trading with a multi-timeframe momentum strategy",
        "expected_domain": "finance",
        "expected_bin": "toybox/finance",
        "should_clarify": False,
        "description": "Finance — clear domain and intent",
    },
]


def score_result(case: dict, result: dict) -> dict:
    domain_correct = result["domain"] == case["expected_domain"]
    bin_correct    = result["toybox_bin"] == case["expected_bin"]
    clarify_correct = result["needs_clarification"] == case["should_clarify"]
    questions_present = len(result["clarifying_questions"]) >= 2

    score = sum([domain_correct, bin_correct, clarify_correct, questions_present])
    return {
        "domain_correct":    domain_correct,
        "bin_correct":       bin_correct,
        "clarify_correct":   clarify_correct,
        "questions_present": questions_present,
        "score":             score,
        "max":               4,
    }


def run_eval():
    print("=" * 60)
    print("  Orlog Goal Layer Eval — Layer 2 Proof")
    print("=" * 60)
    print()

    total_score = 0
    total_max   = 0
    results     = []

    for case in TEST_CASES:
        gl     = GoalLayer(goal=case["goal"])
        result = gl.summary()
        scored = score_result(case, result)

        total_score += scored["score"]
        total_max   += scored["max"]
        results.append({"case": case, "result": result, "scored": scored})

        status = "PASS" if scored["score"] == scored["max"] else "PARTIAL" if scored["score"] >= 2 else "FAIL"
        print(f"Test {case['id']}: {status} ({scored['score']}/{scored['max']})")
        print(f"  Goal    : {case['goal'][:65]}...")
        print(f"  Desc    : {case['description']}")
        print(f"  Domain  : {result['domain']} (expected {case['expected_domain']}) {'✓' if scored['domain_correct'] else '✗'}")
        print(f"  Bin     : {result['toybox_bin']} {'✓' if scored['bin_correct'] else '✗'}")
        print(f"  Clarify : {result['needs_clarification']} (expected {case['should_clarify']}) {'✓' if scored['clarify_correct'] else '✗'}")
        print(f"  Qs      : {result['clarifying_questions'][0][:60]}...")
        print()

    pct = (total_score / total_max) * 100
    print("=" * 60)
    print(f"  Total: {total_score}/{total_max} ({pct:.0f}%)")

    if pct >= 80:
        print("  Layer 2 PROVEN — Goal Layer is working.")
    elif pct >= 60:
        print("  Layer 2 PARTIAL — Gaps in domain detection, needs tuning.")
    else:
        print("  Layer 2 FAIL — Goal Layer needs rework before building on it.")
    print()
    return results


if __name__ == "__main__":
    run_eval()
