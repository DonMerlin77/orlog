"""
orlog/agents/feedback_loop.py — Hermes thumbs-down loop.

Reads inference_log.jsonl, finds unprocessed thumbs-down entries, calls Hermes
to research why the reasoning failed, and appends lessons to brain.md.

Run manually:
    cd ~/shopify_store && source venv/bin/activate
    python3 -m orlog.agents.feedback_loop

Run with --dry-run to see what would be processed without writing anything.
"""

import json
import argparse
from pathlib import Path
from datetime import datetime

from dotenv import load_dotenv
load_dotenv(Path(__file__).parents[2] / ".env")

from orlog.hermes.hermes import Hermes

LOG_PATH       = Path(__file__).parents[1] / "app" / "inference_log.jsonl"
BRAIN_PATH     = Path(__file__).parents[1] / "app" / "brain.md"
PROCESSED_PATH = Path(__file__).parents[1] / "app" / "feedback_processed.json"
HERMES_LOG     = Path("logs/hermes_orlog.json")

HERMES_ANCHOR  = "<!-- Hermes appends below this line — do not remove this comment -->"


def _load_processed() -> set:
    if PROCESSED_PATH.exists():
        return set(json.loads(PROCESSED_PATH.read_text()))
    return set()


def _save_processed(ids: set):
    PROCESSED_PATH.write_text(json.dumps(sorted(ids)))


def _load_thumbs_down() -> list[dict]:
    if not LOG_PATH.exists():
        return []
    entries = []
    for line in LOG_PATH.read_text().splitlines():
        if not line.strip():
            continue
        try:
            e = json.loads(line)
            if e.get("thumbs") == "down":
                entries.append(e)
        except Exception:
            pass
    return entries


def _append_lesson_to_brain(lesson: str):
    if not BRAIN_PATH.exists():
        return
    content = BRAIN_PATH.read_text()
    if HERMES_ANCHOR not in content:
        return
    updated = content.replace(
        HERMES_ANCHOR,
        f"{HERMES_ANCHOR}\n\n{lesson}"
    )
    BRAIN_PATH.write_text(updated)


def _build_problem(entry: dict) -> str:
    question = entry.get("question", "unknown question")
    backend  = entry.get("backend", "unknown")
    latency  = entry.get("latency", "?")
    reason   = entry.get("reason", "")

    base = (
        f"A user rated this reasoning response as bad (thumbs down). "
        f"Question asked: '{question}'. Backend: {backend}. Latency: {latency}s."
    )
    if reason:
        base += f" The user said: '{reason}'."
    else:
        base += (
            " No reason given — identify what reasoning failure likely occurred "
            "(vague verdict, false anchor, symmetric tension, unanswerable gap, hedged risk) "
            "and what rule would prevent it."
        )
    return base


def run(dry_run: bool = False, max_entries: int = 5):
    processed = _load_processed()
    thumbs_down = _load_thumbs_down()
    unprocessed = [e for e in thumbs_down if e.get("id") and e["id"] not in processed]

    if not unprocessed:
        print("No new thumbs-down entries to process.")
        return

    print(f"Found {len(unprocessed)} unprocessed thumbs-down. Processing up to {max_entries}.")

    hermes = Hermes(brain_path=None, log_path=HERMES_LOG)
    batch  = unprocessed[:max_entries]

    for entry in batch:
        entry_id = entry["id"]
        problem  = _build_problem(entry)

        print(f"\n  [{entry_id}] Q: {entry.get('question', '')[:60]}...")

        if dry_run:
            print(f"  DRY RUN — would research: {problem[:100]}...")
            continue

        result = hermes.research_and_write_lesson(
            domain="reasoning",
            problem=problem,
        )

        status  = result.get("status", "unknown")
        lesson  = result.get("lesson", "")
        confidence = result.get("confidence", 0)

        print(f"  Status: {status} (confidence: {confidence})")
        print(f"  Lesson: {lesson[:120]}...")

        if status in ("verified", "uncertain") and lesson:
            _append_lesson_to_brain(lesson)
            print(f"  Written to brain.md")

        processed.add(entry_id)

    if not dry_run:
        _save_processed(processed)
        print(f"\nDone. Processed {len(batch)} entries. brain.md updated.")


def main():
    parser = argparse.ArgumentParser(description="Hermes thumbs-down feedback loop")
    parser.add_argument("--dry-run",  action="store_true", help="Show what would run without writing")
    parser.add_argument("--max",      type=int, default=5,  help="Max entries to process per run")
    args = parser.parse_args()
    run(dry_run=args.dry_run, max_entries=args.max)


if __name__ == "__main__":
    import sys
    sys.path.insert(0, str(Path(__file__).parents[2]))
    main()
