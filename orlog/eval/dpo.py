"""
orlog/eval/dpo.py — DPO training pair formatter.

Pulls labeled Orlog runs and router decisions, formats them as
winner/loser pairs for LoRA fine-tuning on Qwen2.5-7B (or any model
compatible with HuggingFace trl DPOTrainer).

Two pair types:
  router    — lens selection decisions (prompt=goal+domain+task, response=lens set)
  synthesis — full council outputs (prompt=question, response=synthesis verdict)

Output: JSONL with {"prompt": ..., "chosen": ..., "rejected": ...}
Compatible with trl DPOTrainer out of the box.

Usage:
    python3 -m orlog.eval.dpo --out dpo_pairs.jsonl
    python3 -m orlog.eval.dpo --out dpo_pairs.jsonl --type router
    python3 -m orlog.eval.dpo --out dpo_pairs.jsonl --min-gap 1.5
"""

import json
import argparse
from pathlib import Path
from datetime import datetime


# ── Prompt templates ──────────────────────────────────────────────────────────

ROUTER_SYSTEM = """You are Orlog's routing layer. Given a builder's goal, domain, and task type, select the optimal set of reasoning lenses for the council deliberation. Each lens shapes how the council frames the problem. Choose lenses that will surface the highest-value tensions and blind spots for this specific situation."""

SYNTHESIS_SYSTEM = """You are Orlog — a domain-agnostic AI operating system. A council of five cognitive styles (ADHD, Autism, OCD, Dyslexia, Paranoid) has deliberated on the question below. Synthesize their perspectives into a single verdict that captures the most important insight, the key tension, and the blind spot none of them saw."""


def _format_router_prompt(decision: dict) -> str:
    return (
        f"{ROUTER_SYSTEM}\n\n"
        f"Goal: {decision['goal']}\n"
        f"Domain: {decision['domain']}\n"
        f"Task: {decision['task_type']}\n\n"
        f"Select the reasoning lenses for this council run."
    )


def _format_router_response(decision: dict) -> str:
    lenses = decision.get("lenses_selected", [])
    names  = [l["name"] if isinstance(l, dict) else l for l in lenses]
    return (
        f"Selected lenses ({len(names)}):\n"
        + "\n".join(f"- {n}" for n in names)
        + f"\n\nReasoning: {decision.get('reasoning', '')}"
    )


def _format_synthesis_prompt(run: dict) -> str:
    lenses = run.get("lenses", [])
    return (
        f"{SYNTHESIS_SYSTEM}\n\n"
        f"Goal: {run['goal']}\n"
        f"Domain: {run['domain']}\n"
        f"Task: {run['task_type']}\n"
        f"Lenses used: {', '.join(lenses)}\n\n"
        f"Question: {run['question']}"
    )


def _format_synthesis_response(run: dict) -> str:
    s = run.get("synthesis", {})
    parts = []
    if s.get("verdict"):
        parts.append(f"# VERDICT\n{s['verdict']}")
    if s.get("key_tension"):
        parts.append(f"# KEY TENSION\n{s['key_tension']}")
    if s.get("blind_spot"):
        parts.append(f"# BLIND SPOT\n{s['blind_spot']}")
    return "\n\n".join(parts) if parts else run.get("synthesis_raw", "")


# ── Pair extraction ───────────────────────────────────────────────────────────

def router_pairs(
    router_log_path: str = "logs/router_decisions.json",
    min_gap: float = 1.0,
) -> list[dict]:
    """
    Extract DPO pairs from router decisions.
    Same domain+task_type → higher score = winner, lower = loser.
    Only pairs with score gap >= min_gap are included.
    """
    log_path = Path(router_log_path)
    if not log_path.exists():
        return []

    decisions = json.loads(log_path.read_text())
    labeled   = [d for d in decisions if d.get("outcome_score") is not None]

    by_key: dict[tuple, list] = {}
    for d in labeled:
        key = (d["domain"], d["task_type"])
        by_key.setdefault(key, []).append(d)

    pairs = []
    for key, entries in by_key.items():
        entries.sort(key=lambda x: x["outcome_score"], reverse=True)
        for i in range(len(entries) - 1):
            winner = entries[i]
            loser  = entries[i + 1]
            gap    = winner["outcome_score"] - loser["outcome_score"]
            if gap < min_gap:
                continue
            chosen   = _format_router_response(winner)
            rejected = _format_router_response(loser)
            # Skip pairs where lens selections are identical — same lenses, different outcome
            # means the question drove the score difference, not the routing decision.
            # These would produce contradictory training signal.
            if chosen == rejected:
                continue
            pairs.append({
                "prompt":   _format_router_prompt(winner),
                "chosen":   chosen,
                "rejected": rejected,
                "_meta": {
                    "type":         "router",
                    "domain":       key[0],
                    "task_type":    key[1],
                    "winner_id":    winner["id"],
                    "winner_score": winner["outcome_score"],
                    "loser_id":     loser["id"],
                    "loser_score":  loser["outcome_score"],
                    "gap":          round(gap, 2),
                },
            })
    return pairs


def synthesis_pairs(
    runs_log_path: str = "logs/orlog_runs.json",
    min_gap: float     = 1.0,
) -> list[dict]:
    """
    Extract DPO pairs from full Orlog runs.
    Same domain+task_type+question_hash → higher score = winner, lower = loser.
    """
    log_path = Path(runs_log_path)
    if not log_path.exists():
        return []

    runs    = json.loads(log_path.read_text())
    labeled = [r for r in runs if r.get("orlog_score") is not None
               and r.get("synthesis", {}).get("verdict")]

    # Group by domain + task_type (question similarity is implicit)
    by_key: dict[tuple, list] = {}
    for r in labeled:
        key = (r["domain"], r["task_type"])
        by_key.setdefault(key, []).append(r)

    pairs = []
    for key, entries in by_key.items():
        entries.sort(key=lambda x: x["orlog_score"], reverse=True)
        for i in range(len(entries) - 1):
            winner = entries[i]
            loser  = entries[i + 1]
            gap    = winner["orlog_score"] - loser["orlog_score"]
            if gap < min_gap:
                continue
            pairs.append({
                "prompt":   _format_synthesis_prompt(winner),
                "chosen":   _format_synthesis_response(winner),
                "rejected": _format_synthesis_response(loser),
                "_meta": {
                    "type":         "synthesis",
                    "domain":       key[0],
                    "task_type":    key[1],
                    "winner_id":    winner["id"],
                    "winner_score": winner["orlog_score"],
                    "loser_id":     loser["id"],
                    "loser_score":  loser["orlog_score"],
                    "gap":          round(gap, 2),
                },
            })
    return pairs


def prepare(
    out_path: str          = "dpo_pairs.jsonl",
    pair_type: str         = "both",
    min_gap: float         = 1.0,
    router_log: str        = "logs/router_decisions.json",
    runs_log: str          = "logs/orlog_runs.json",
    include_meta: bool     = False,
) -> dict:
    """
    Build and export DPO pairs to JSONL.

    pair_type: "router" | "synthesis" | "both"
    include_meta: if True, _meta field is kept in output (useful for debugging)
    Returns summary dict.
    """
    pairs = []
    if pair_type in ("router", "both"):
        pairs += router_pairs(router_log, min_gap)
    if pair_type in ("synthesis", "both"):
        pairs += synthesis_pairs(runs_log, min_gap)

    out = Path(out_path)
    out.parent.mkdir(parents=True, exist_ok=True)

    with out.open("w") as f:
        for p in pairs:
            row = {k: v for k, v in p.items() if include_meta or k != "_meta"}
            f.write(json.dumps(row) + "\n")

    by_type = {}
    for p in pairs:
        t = p["_meta"]["type"]
        by_type.setdefault(t, []).append(p["_meta"]["gap"])

    summary = {
        "exported_at":  datetime.now().isoformat(),
        "out_path":     str(out),
        "total_pairs":  len(pairs),
        "min_gap":      min_gap,
        "by_type": {
            t: {"n": len(gaps), "avg_gap": round(sum(gaps) / len(gaps), 2)}
            for t, gaps in by_type.items()
        },
        "note": (
            f"{len(pairs)} pairs exported. "
            f"Target for meaningful LoRA training: 200+ pairs. "
            f"Continue running and scoring Orlog to accumulate data."
        ) if len(pairs) < 200 else f"{len(pairs)} pairs — sufficient for LoRA training.",
    }
    return summary


# ── CLI ───────────────────────────────────────────────────────────────────────

def main():
    parser = argparse.ArgumentParser(description="Export Orlog DPO training pairs")
    parser.add_argument("--out",      default="dpo_pairs.jsonl", help="Output JSONL path")
    parser.add_argument("--type",     default="both", choices=["router", "synthesis", "both"])
    parser.add_argument("--min-gap",  type=float, default=1.0, help="Minimum score gap for a pair")
    parser.add_argument("--meta",     action="store_true", help="Include _meta field in output")
    parser.add_argument("--router-log", default="logs/router_decisions.json")
    parser.add_argument("--runs-log",   default="logs/orlog_runs.json")
    args = parser.parse_args()

    summary = prepare(
        out_path=args.out,
        pair_type=args.type,
        min_gap=args.min_gap,
        router_log=args.router_log,
        runs_log=args.runs_log,
        include_meta=args.meta,
    )

    print(f"DPO pairs exported → {summary['out_path']}")
    print(f"  Total:    {summary['total_pairs']}")
    for t, s in summary["by_type"].items():
        print(f"  {t:12} {s['n']} pairs, avg gap {s['avg_gap']}")
    print(f"  {summary['note']}")


if __name__ == "__main__":
    import sys
    sys.path.insert(0, str(Path(__file__).parent.parent.parent))
    main()
