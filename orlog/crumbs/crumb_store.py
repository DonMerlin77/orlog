"""
orlog/crumbs/crumb_store.py

Crumb layer — passive decision-shape cache that builds itself.

Flow:
  raw question
    → _loki_extract() → clean_question
    → crumb_lookup(clean_question) → hit? seed council with tensions/blind spot
    → council runs (always — same tension, different context needs fresh verdict)
    → store_crumb(clean_question, tension, blind_spot, verdict_hint)

Intent key: sha256 of normalized clean_question (lowercased, stripped, collapsed whitespace).
This catches repeated identical questions — e.g. Wyrd "Bring to Orlog" sends exact tension strings back.
Semantic deduplication (embedding similarity) is a future upgrade.

Crumb schema:
  {
    "key":          "sha256[:16]",
    "clean":        "the extracted decision",
    "tension":      "core tension in <=10 words",
    "blind_spot":   "what people typically miss on this shape",
    "verdict_hint": "one-line council conclusion",
    "hits":         int,
    "first_seen":   "ISO timestamp",
    "last_seen":    "ISO timestamp"
  }
"""

import hashlib
import json
import time
from pathlib import Path


def _intent_key(clean_question: str) -> str:
    normalized = " ".join(clean_question.lower().strip().split())
    return hashlib.sha256(normalized.encode()).hexdigest()[:16]


def lookup(clean_question: str, crumb_path: Path) -> dict | None:
    """Return crumb dict if this decision shape has been seen before, else None."""
    if not crumb_path.exists():
        return None
    key = _intent_key(clean_question)
    for line in crumb_path.read_text().splitlines():
        line = line.strip()
        if not line:
            continue
        try:
            entry = json.loads(line)
            if entry.get("key") == key:
                return entry
        except Exception:
            continue
    return None


def store(
    clean_question: str,
    tension: str,
    blind_spot: str,
    verdict_hint: str,
    crumb_path: Path,
) -> None:
    """
    Write or update a crumb. If the key already exists, increment hits + update timestamps.
    If new, append a fresh entry.
    """
    key = _intent_key(clean_question)
    now = time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())

    existing_lines = []
    updated = False

    if crumb_path.exists():
        for line in crumb_path.read_text().splitlines():
            line = line.strip()
            if not line:
                continue
            try:
                entry = json.loads(line)
                if entry.get("key") == key:
                    entry["hits"]       = entry.get("hits", 1) + 1
                    entry["last_seen"]  = now
                    entry["tension"]    = tension or entry["tension"]
                    entry["blind_spot"] = blind_spot or entry.get("blind_spot", "")
                    entry["verdict_hint"] = verdict_hint or entry.get("verdict_hint", "")
                    existing_lines.append(json.dumps(entry))
                    updated = True
                else:
                    existing_lines.append(line)
            except Exception:
                existing_lines.append(line)

    if updated:
        crumb_path.write_text("\n".join(existing_lines) + "\n")
        return

    new_entry = {
        "key":          key,
        "clean":        clean_question[:300],
        "tension":      tension[:120] if tension else "",
        "blind_spot":   blind_spot[:200] if blind_spot else "",
        "verdict_hint": verdict_hint[:300] if verdict_hint else "",
        "hits":         1,
        "first_seen":   now,
        "last_seen":    now,
    }
    with crumb_path.open("a") as f:
        f.write(json.dumps(new_entry) + "\n")


def build_seed_context(crumb: dict) -> str:
    """
    Format a crumb hit into a council context injection.
    Injected as a prefix to the council system prompt so agents
    know what previous runs on this shape found.
    """
    parts = ["[PATTERN RECOGNIZED — previous sessions on this decision shape found:]"]
    if crumb.get("tension"):
        parts.append(f"Core tension: {crumb['tension']}")
    if crumb.get("blind_spot"):
        parts.append(f"Common blind spot: {crumb['blind_spot']}")
    if crumb.get("verdict_hint"):
        parts.append(f"Prior council conclusion: {crumb['verdict_hint']}")
    parts.append(f"(seen {crumb.get('hits', 1)} time(s) — validate against this session's specifics)")
    return "\n".join(parts)


def extract_from_trace(trace: str) -> tuple[str, str, str]:
    """
    Pull tension, blind_spot, and verdict_hint out of a raw council trace.
    Returns (tension, blind_spot, verdict_hint) — empty strings if not found.
    """
    tension = blind_spot = verdict_hint = ""
    for line in trace.splitlines():
        l = line.strip()
        for prefix in ("KEY TENSION:", "ᚦ KEY TENSION:"):
            if l.startswith(prefix):
                tension = l[len(prefix):].strip()[:120]
        for prefix in ("BLIND SPOT:", "ᛈ BLIND SPOT:"):
            if l.startswith(prefix):
                blind_spot = l[len(prefix):].strip()[:200]
        for prefix in ("VERDICT:", "ᛏ VERDICT:"):
            if l.startswith(prefix):
                verdict_hint = l[len(prefix):].strip()[:300]
    return tension, blind_spot, verdict_hint
