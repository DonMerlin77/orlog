"""
orlog/app/pull_questions.py — ONE-SHOT. Read anonymized questions from Modal volume.

Pulls inference_log.jsonl (Orlog analyses) and loki_log.jsonl (Loki conversations)
from the deployed volume. Strips all user identifiers — only question text is output.

Usage:
    modal run orlog/app/pull_questions.py
    modal run orlog/app/pull_questions.py --since 2026-06-09
"""
import json
import modal
from pathlib import Path

app = modal.App("orlog-pull-questions")

image = modal.Image.debian_slim(python_version="3.11")
user_vol = modal.Volume.from_name("orlog-user-data", create_if_missing=False)


@app.function(
    image=image,
    volumes={"/data": user_vol},
)
def pull(since: str = ""):
    import json
    from pathlib import Path

    data_dir = Path("/data")
    results = {"orlog": [], "loki": []}

    inference_log = data_dir / "inference_log.jsonl"
    if inference_log.exists():
        for line in inference_log.read_text().splitlines():
            if not line.strip():
                continue
            try:
                entry = json.loads(line)
                ts = entry.get("ts", "")
                if since and ts < since:
                    continue
                q = entry.get("question", "").strip()
                if q:
                    results["orlog"].append({"question": q, "timestamp": ts[:16]})
            except Exception:
                pass

    # /conversation endpoint logs to loki_shadow_log.jsonl (user_message field)
    loki_log = data_dir / "loki_shadow_log.jsonl"
    if loki_log.exists():
        seen = set()
        for line in loki_log.read_text().splitlines():
            if not line.strip():
                continue
            try:
                entry = json.loads(line)
                ts = entry.get("ts", "")
                if since and ts < since:
                    continue
                msg = entry.get("user_message", "").strip()
                if msg and msg not in seen:
                    seen.add(msg)
                    results["loki"].append({"message": msg, "timestamp": ts[:16]})
            except Exception:
                pass

    return results


@app.local_entrypoint()
def main(since: str = ""):
    results = pull.remote(since=since)

    orlog_qs = results["orlog"]
    loki_msgs = results["loki"]

    print(f"\n{'='*60}")
    print(f"ORLOG ANALYSES ({len(orlog_qs)} questions)")
    print(f"{'='*60}")
    for i, q in enumerate(orlog_qs, 1):
        print(f"[{i}] {q['timestamp']}  {q['question'][:100]}")

    print(f"\n{'='*60}")
    print(f"LOKI CONVERSATIONS ({len(loki_msgs)} messages)")
    print(f"{'='*60}")
    for i, m in enumerate(loki_msgs, 1):
        print(f"[{i}] {m['timestamp']}  {m['message'][:100]}")

    print(f"\nTotal: {len(orlog_qs)} Orlog questions + {len(loki_msgs)} Loki messages")
    print("No user identifiers included.")
