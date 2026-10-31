"""
orlog/eval/modal_batch.py — Modal-parallel DPO pair generator.

Runs the full question bank in parallel on Modal (CPU containers).
Each question gets its own container — 30 questions run simultaneously
instead of sequentially, cutting 45min → ~3-4min.

No GPU needed — this is pure API parallelism.

Deploy:  modal deploy orlog/eval/modal_batch.py
Run:     modal run orlog/eval/modal_batch.py
         modal run orlog/eval/modal_batch.py --domain software
         modal run orlog/eval/modal_batch.py --limit 10

Cost: ~$0.01-0.05 for the full 30-question bank (CPU containers, cheap)
"""

import os
import modal
from pathlib import Path
from dotenv import load_dotenv

load_dotenv(Path(__file__).parent.parent.parent / ".env")

# ── App ───────────────────────────────────────────────────────────────────────

app = modal.App("orlog-batch")

image = (
    modal.Image.debian_slim(python_version="3.11")
    .pip_install(
        "anthropic>=0.25.0",
        "python-dotenv>=1.0.0",
        "requests>=2.31.0",
    )
)

orlog_secret = modal.Secret.from_dict({
    "ANTHROPIC_API_KEY": os.getenv("ANTHROPIC_API_KEY", ""),
})


# ── Single question runner (one container per question) ───────────────────────

@app.function(
    image=image,
    secrets=[orlog_secret],
    timeout=300,
    retries=1,
)
def run_question(domain: str, task_type: str, goal: str, question: str) -> dict:
    """Run one question through Orlog, return scored result."""
    import sys
    import os as _os
    sys.path.insert(0, "/")

    # Inline Orlog core — no file system dependency on the host
    from anthropic import Anthropic

    client = Anthropic(api_key=_os.getenv("ANTHROPIC_API_KEY"))

    # Minimal inline council — full council would need the brain files mounted
    # We run a focused single-call synthesis instead (faster, still scoreable)
    prompt = (
        f"You are Orlog, a domain-agnostic AI operating system.\n\n"
        f"Domain: {domain}\n"
        f"Task: {task_type}\n"
        f"Goal: {goal}\n\n"
        f"Question: {question}\n\n"
        f"Respond with:\n"
        f"# VERDICT\n[one clear sentence — what should actually be done]\n\n"
        f"# REASONING\n[2-3 sentences — why this is the right call]\n\n"
        f"# KEY TENSION\n[what makes this hard — the real tradeoff]\n\n"
        f"# BLIND SPOT\n[what most people miss about this situation]"
    )

    response = client.messages.create(
        model="claude-haiku-4-5-20251001",
        max_tokens=600,
        messages=[{"role": "user", "content": prompt}],
    ).content[0].text.strip()

    # Auto-score via a second call — calibrated to produce variance
    score_prompt = (
        f"Score this Orlog output from 1-10. Be critical — most outputs should score 5-7.\n\n"
        f"Question: {question}\n"
        f"Output:\n{response}\n\n"
        f"Scoring anchors:\n"
        f"1-3: Verdict is vague or hedged, tension is generic, blind spot is obvious\n"
        f"4-5: Some specificity but advice could apply to any situation\n"
        f"6-7: Clear verdict, real tradeoff named, insight is non-obvious but modest\n"
        f"8-9: Verdict is specific and counterintuitive, tension is the actual crux\n"
        f"10: Blind spot changes the entire framing — something a smart person would miss\n\n"
        f"Reserve 8+ only if genuinely surprised. Reply with ONLY a number 1-10. No explanation."
    )

    try:
        score_raw = client.messages.create(
            model="claude-haiku-4-5-20251001",
            max_tokens=10,
            messages=[{"role": "user", "content": score_prompt}],
        ).content[0].text.strip()
        score = float(score_raw.split()[0])
        score = max(1.0, min(10.0, score))
    except Exception:
        score = 5.0

    return {
        "domain":      domain,
        "task_type":   task_type,
        "goal":        goal[:100],
        "question":    question,
        "synthesis":   response,
        "orlog_score": score,
    }


# ── Orchestrator — fans out all questions in parallel ─────────────────────────

@app.local_entrypoint()
def main(domain: str = None, limit: int = None):
    from orlog.eval.batch_runner import QUESTION_BANK
    import json
    from datetime import datetime
    from pathlib import Path as _Path

    questions = QUESTION_BANK
    if domain:
        questions = [q for q in questions if q[0] == domain]
    if limit:
        questions = questions[:int(limit)]

    print(f"Orlog Modal batch — {len(questions)} questions running in parallel...")

    # Fan out — all questions run simultaneously in separate containers
    results = list(run_question.starmap(questions))

    # Collect into DPO-ready format
    pairs = []
    by_domain: dict[str, list] = {}
    for r in results:
        d = r["domain"]
        by_domain.setdefault(d, []).append(r)

    for d, runs in by_domain.items():
        runs.sort(key=lambda x: x["orlog_score"], reverse=True)
        for i in range(len(runs) - 1):
            winner = runs[i]
            loser  = runs[i + 1]
            gap    = winner["orlog_score"] - loser["orlog_score"]
            if gap < 0.5:
                continue
            pairs.append({
                "prompt":   f"Domain: {d}\nGoal: {winner['goal']}\nTask: {winner['task_type']}\nQuestion: {winner['question']}",
                "chosen":   winner["synthesis"],
                "rejected": loser["synthesis"],
                "_meta": {
                    "domain":       d,
                    "winner_score": winner["orlog_score"],
                    "loser_score":  loser["orlog_score"],
                    "gap":          round(gap, 2),
                },
            })

    # Save results
    out_dir = _Path("logs")
    out_dir.mkdir(exist_ok=True)

    results_path = out_dir / "modal_batch_results.json"
    results_path.write_text(json.dumps(results, indent=2))

    pairs_path = out_dir / "modal_dpo_pairs.jsonl"
    with pairs_path.open("w") as f:
        for p in pairs:
            row = {k: v for k, v in p.items() if k != "_meta"}
            f.write(json.dumps(row) + "\n")

    print(f"\nResults: {len(results)} runs")
    print(f"DPO pairs: {len(pairs)}")
    avg = sum(r["orlog_score"] for r in results) / max(len(results), 1)
    print(f"Avg score: {avg:.2f}")
    print(f"Saved → {results_path}")
    print(f"Pairs  → {pairs_path}")

    # Domain breakdown
    print("\nBy domain:")
    for d, runs in sorted(by_domain.items()):
        scores = [r["orlog_score"] for r in runs]
        print(f"  {d:20} n={len(runs)} avg={sum(scores)/len(scores):.1f}")
