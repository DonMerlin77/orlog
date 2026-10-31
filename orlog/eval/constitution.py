"""
orlog/eval/constitution.py — Reasoning constitution for Orlog verdicts.

The constitutional layer is a renormalization group (RG) filter:
it integrates out per-voice bias (short-distance noise) to check whether
the long-wavelength structure of a verdict obeys the invariants of good reasoning.

Physics analogy:
  Conservation laws in classical mechanics don't care HOW a system gets from
  A to B — only that energy, momentum, and angular momentum are preserved.
  Similarly, the constitution doesn't care which voices contributed —
  only that the verdict preserves these reasoning invariants:

  1. FALSIFIABILITY   — verdict makes a prediction that could be wrong
  2. NON-CIRCULARITY  — reasoning is not its own justification
  3. SPECIFICITY      — verdict is not vacuously true for any question
  4. TENSION PRESERVED — disagreement was resolved, not suppressed
  5. BLIND SPOT NAMED — synthesis acknowledges what it cannot see

RG framing: verdicts that violate invariants under ANY paraphrase are
"physically impossible trajectories" — the constitutional layer rejects them
before they enter the DPO training corpus (garbage in → garbage trained in).

The scoring model: each invariant violation reduces the constitutional score.
Score >= 0.6 → passes. Score < 0.6 → flag for regeneration.

Usage:
    from orlog.eval.constitution import Constitution
    c = Constitution()
    result = c.check(question="...", synthesis="...", perspectives={...})
    # result: {score, passed, violations, verdict}

    # CLI:
    python3 -m orlog.eval.constitution --question "..." --synthesis "..."
"""

import os
import sys
import json
import argparse
from pathlib import Path
from dotenv import load_dotenv

sys.path.insert(0, str(Path(__file__).parents[2]))
from llm_router import fast_call

load_dotenv(Path(__file__).parent.parent.parent / ".env")

CONSTITUTION_LOG = Path("logs/constitution_log.json")

INVARIANTS = [
    {
        "name":        "falsifiability",
        "weight":      0.20,
        "description": "The verdict makes a specific prediction or recommendation that could be proven wrong.",
        "test":        "Does this synthesis make a concrete claim that could be falsified by evidence or outcome? Or is it so hedged it applies regardless of what happens?",
    },
    {
        "name":        "non_circularity",
        "weight":      0.15,
        "description": "The reasoning supports the verdict; the verdict does not support itself.",
        "test":        "Does the reasoning provide evidence/logic that leads to the verdict, or does it just restate the verdict in different words?",
    },
    {
        "name":        "specificity",
        "weight":      0.20,
        "description": "The verdict applies specifically to THIS question, not to any question.",
        "test":        "Replace the question with a completely different question. Would this verdict still sound plausible? If yes, the verdict is too generic.",
    },
    {
        "name":        "tension_preserved",
        "weight":      0.15,
        "description": "The key tension between perspectives was resolved, not suppressed or averaged.",
        "test":        "Does the synthesis acknowledge the sharpest disagreement between perspectives and explain why one view wins (or why both are partially right)? Or does it just split the difference?",
    },
    {
        "name":        "blind_spot_named",
        "weight":      0.15,
        "description": "The synthesis names at least one thing it cannot see or has not accounted for.",
        "test":        "Does the synthesis admit its own limitations — what it would need to change the verdict, or what it is assuming it cannot verify?",
    },
    {
        "name":        "no_target_leakage",
        "weight":      0.15,
        "description": (
            "The reasoning does not use outcome knowledge that only exists AFTER the decision is made. "
            "ML term: target leakage — a feature that becomes known only after the prediction window "
            "has closed, making the model appear accurate while actually cheating. "
            "In reasoning: arguing from hindsight ('we know X happened, therefore Y was right') "
            "is not valid if X was not knowable at decision time."
        ),
        "test":        (
            "Does any part of the reasoning assume knowledge of outcomes that would not have been "
            "available at the moment the decision needed to be made? Does the verdict depend on "
            "information that only exists after the fact? If the decision had to be made RIGHT NOW "
            "with only currently-available information, does the reasoning still hold?"
        ),
    },
]

CONSTITUTION_SYSTEM = (
    "You are a reasoning auditor applying constitutional invariants to a synthesis. "
    "You are NOT evaluating quality or cleverness — you are checking whether specific "
    "structural properties are present or absent. Be strict. A synthesis that sounds "
    "impressive but fails an invariant still fails. Reply with JSON only."
)


class Constitution:
    def __init__(self):
        self.log_path = CONSTITUTION_LOG
        self.log_path.parent.mkdir(exist_ok=True)

    def check(
        self,
        question:     str,
        synthesis:    str,
        perspectives: dict = None,
        verbose:      bool = False,
    ) -> dict:
        persp_summary = ""
        if perspectives:
            top = list(perspectives.items())[:4]
            persp_summary = "\n".join(
                f"[{k.upper()}]: {v[:200]}" for k, v in top
            )

        checks = []
        for inv in INVARIANTS:
            prompt = (
                f"QUESTION: {question}\n\n"
                f"SYNTHESIS TO AUDIT:\n{synthesis[:1500]}\n\n"
                + (f"KEY PERSPECTIVES:\n{persp_summary}\n\n" if persp_summary else "")
                + f"INVARIANT TO CHECK: {inv['name'].upper()}\n"
                f"Description: {inv['description']}\n"
                f"Test question: {inv['test']}\n\n"
                f"Reply with JSON: "
                f'{{\"passes\": true/false, \"confidence\": 0.0-1.0, \"reason\": \"one sentence\"}}'
            )
            try:
                raw = fast_call(CONSTITUTION_SYSTEM, prompt, 150)
                # Parse JSON robustly
                start = raw.find("{")
                end   = raw.rfind("}") + 1
                data  = json.loads(raw[start:end]) if start >= 0 else {}
                passes     = bool(data.get("passes", False))
                confidence = float(data.get("confidence", 0.5))
                reason     = data.get("reason", "")
            except Exception as e:
                passes, confidence, reason = False, 0.5, f"[parse error: {e}]"

            checks.append({
                "invariant":  inv["name"],
                "weight":     inv["weight"],
                "passes":     passes,
                "confidence": confidence,
                "reason":     reason,
            })
            if verbose:
                status = "PASS" if passes else "FAIL"
                print(f"  [{status}] {inv['name']}: {reason}")

        # Weighted constitutional score
        score = sum(
            c["weight"] * (c["confidence"] if c["passes"] else (1 - c["confidence"]) * 0.1)
            for c in checks
        )
        passed     = score >= 0.6
        violations = [c for c in checks if not c["passes"]]

        result = {
            "question":   question,
            "score":      round(score, 3),
            "passed":     passed,
            "violations": violations,
            "checks":     checks,
            "synthesis_preview": synthesis[:200],
        }

        self._log(result)
        return result

    def check_from_orlog(self, orlog_result: dict, verbose: bool = False) -> dict:
        """Convenience: pass an Orlog run result directly."""
        synthesis = orlog_result.get("synthesis", "")
        if isinstance(synthesis, dict):
            parts = []
            for k in ["verdict", "reasoning", "key_tension", "blind_spot"]:
                if synthesis.get(k):
                    parts.append(synthesis[k])
            synthesis = " ".join(parts)
        return self.check(
            question=orlog_result.get("question", ""),
            synthesis=synthesis,
            perspectives=orlog_result.get("perspectives", {}),
            verbose=verbose,
        )

    def _log(self, result: dict):
        existing = []
        if self.log_path.exists():
            try:
                existing = json.loads(self.log_path.read_text())
            except Exception:
                pass
        existing.append(result)
        self.log_path.write_text(json.dumps(existing[-200:], indent=2))

    def log_stats(self) -> dict:
        if not self.log_path.exists():
            return {}
        log = json.loads(self.log_path.read_text())
        scores = [e["score"] for e in log]
        pass_rate = sum(1 for e in log if e["passed"]) / max(len(log), 1)
        violation_counts = {}
        for e in log:
            for v in e.get("violations", []):
                n = v["invariant"]
                violation_counts[n] = violation_counts.get(n, 0) + 1
        top_violations = sorted(violation_counts.items(), key=lambda x: -x[1])
        return {
            "total_checked":   len(log),
            "pass_rate":       round(pass_rate, 3),
            "avg_score":       round(sum(scores) / max(len(scores), 1), 3),
            "top_violations":  top_violations[:3],
        }


if __name__ == "__main__":
    import sys
    sys.path.insert(0, str(Path(__file__).parent.parent.parent))

    parser = argparse.ArgumentParser(description="Constitutional invariant checker for Orlog verdicts")
    parser.add_argument("--question",  default=None)
    parser.add_argument("--synthesis", default=None)
    parser.add_argument("--stats",     action="store_true")
    parser.add_argument("--run",       action="store_true", help="Run Orlog then check constitution")
    parser.add_argument("--domain",    default="general")
    args = parser.parse_args()

    c = Constitution()

    if args.stats:
        s = c.log_stats()
        for k, v in s.items():
            print(f"  {k}: {v}")

    elif args.run and args.question:
        from orlog.core import Orlog
        result = Orlog(goal=args.question[:80], domain=args.domain).run(args.question)
        print(f"\n[synthesis preview]\n{str(result.get('synthesis',''))[:400]}\n")
        cr = c.check_from_orlog(result, verbose=True)
        verdict = "PASSES" if cr["passed"] else "FAILS"
        print(f"\n  Constitutional score: {cr['score']:.3f} — {verdict}")

    elif args.question and args.synthesis:
        cr = c.check(args.question, args.synthesis, verbose=True)
        verdict = "PASSES" if cr["passed"] else "FAILS"
        print(f"\n  Constitutional score: {cr['score']:.3f} — {verdict}")

    else:
        parser.print_help()
