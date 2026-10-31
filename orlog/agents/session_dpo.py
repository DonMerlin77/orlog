"""
orlog/agents/session_dpo.py — Live session thumbs → DPO training pairs.

Reads inference_log.jsonl (site sessions with thumbs up/down).
Converts thumbed sessions into DPO pairs for Mimir retraining.

Chosen:   regenerated with the strong Orlog system prompt
Rejected: regenerated with a deliberately weak/sycophantic system prompt

When both thumb_up and thumb_down exist on the same question, they pair
naturally. Otherwise thumb_up gets a synthetic rejected response.

Output: appended to logs/modal_dpo_pairs.jsonl (same format as self-play pairs)

Usage:
    python3 -m orlog.agents.session_dpo
    python3 -m orlog.agents.session_dpo --dry-run
    python3 -m orlog.agents.session_dpo --min-length 80
"""

import os
import sys
import json
import argparse
from datetime import datetime, timezone
from pathlib import Path

from dotenv import load_dotenv

sys.path.insert(0, str(Path(__file__).parent.parent.parent))
from llm_router import fast_call
load_dotenv(Path(__file__).parents[2] / ".env")

BASE       = Path(__file__).parent.parent.parent
LOG_PATH   = BASE / "orlog" / "app" / "inference_log.jsonl"
DPO_PATH   = BASE / "logs" / "modal_dpo_pairs.jsonl"
PROC_PATH  = BASE / "logs" / "session_dpo_processed.json"

STRONG_SYSTEM = (
    "You are Orlog — a structured reasoning interface. "
    "Force every question through: ANCHOR (what does this assume?) → "
    "TENSION (where does the logic pull apart?) → "
    "GAP (what is missing?) → RISK (what does it cost if you're wrong?) → "
    "VERDICT (one falsifiable sentence: what to do and why). "
    "Commit to a verdict. No hedging. Be specific."
)

WEAK_SYSTEM = (
    "You are a helpful AI assistant. Give a balanced, thoughtful response. "
    "Consider multiple perspectives. Avoid being too definitive since every "
    "situation is different. Try to validate the user's thinking while offering "
    "some gentle suggestions. Keep it encouraging and supportive."
)


def _load_entries() -> list[dict]:
    if not LOG_PATH.exists():
        return []
    entries = []
    for line in LOG_PATH.read_text().splitlines():
        line = line.strip()
        if not line:
            continue
        try:
            entries.append(json.loads(line))
        except Exception:
            pass
    return entries


def _load_processed() -> set:
    if not PROC_PATH.exists():
        return set()
    try:
        return set(json.loads(PROC_PATH.read_text()).get("ids", []))
    except Exception:
        return set()


def _save_processed(ids: set):
    PROC_PATH.parent.mkdir(exist_ok=True)
    PROC_PATH.write_text(json.dumps({"ids": sorted(ids)}, indent=2))


def _append_pair(pair: dict):
    DPO_PATH.parent.mkdir(exist_ok=True)
    with open(DPO_PATH, "a") as f:
        f.write(json.dumps(pair) + "\n")


def _generate(question: str, system: str, max_tokens: int = 600) -> str:
    try:
        return fast_call(system, question, max_tokens)
    except Exception as e:
        return f"[generation failed: {e}]"


def run(dry_run: bool = False, min_length: int = 50) -> dict:
    entries   = _load_entries()
    processed = _load_processed()

    thumbed_up   = [
        e for e in entries
        if e.get("thumbs") == "up"
        and e.get("id") not in processed
        and len(e.get("question", "")) >= min_length
    ]
    thumbed_down = [
        e for e in entries
        if e.get("thumbs") == "down"
        and e.get("id") not in processed
        and len(e.get("question", "")) >= min_length
    ]

    print(f"Unprocessed thumb_up:   {len(thumbed_up)}")
    print(f"Unprocessed thumb_down: {len(thumbed_down)}")

    if dry_run:
        for e in thumbed_up:
            print(f"  [up]   {e['question'][:80]}")
        for e in thumbed_down:
            print(f"  [down] {e['question'][:80]}")
        return {"would_generate": len(thumbed_up)}

    new_pairs = 0
    new_ids   = set()
    down_keys = {e["question"][:60]: e for e in thumbed_down}

    for up in thumbed_up:
        q   = up["question"]
        key = q[:60]

        chosen   = _generate(q, STRONG_SYSTEM, max_tokens=700)
        if key in down_keys:
            # natural pair — thumb_down gives us the rejected response
            rejected = _generate(down_keys[key]["question"], WEAK_SYSTEM)
            new_ids.add(down_keys[key]["id"])
        else:
            # synthetic pair
            rejected = _generate(q, WEAK_SYSTEM)

        pair = {
            "domain":     "live_session",
            "question":   q,
            "score_a":    1.0,
            "score_b":    0.0,
            "gap":        1.0,
            "timestamp":  datetime.now(timezone.utc).isoformat(),
            "chosen":     chosen,
            "rejected":   rejected,
            "source":     "session_dpo",
            "session_id": up["id"],
        }

        _append_pair(pair)
        new_ids.add(up["id"])
        new_pairs += 1
        print(f"  pair: {q[:60]}")

    _save_processed(processed | new_ids)
    print(f"\n{new_pairs} new DPO pair(s) saved to {DPO_PATH.name}")
    return {"new_pairs": new_pairs}


def main():
    parser = argparse.ArgumentParser(description="Live session thumbs → DPO pairs")
    parser.add_argument("--dry-run",    action="store_true")
    parser.add_argument("--min-length", type=int, default=50,
                        help="Minimum question character length to include")
    args = parser.parse_args()
    run(dry_run=args.dry_run, min_length=args.min_length)


if __name__ == "__main__":
    main()
