"""
orlog/wyrd/wyrd_writer.py

Runs a lightweight Mimir pass at the end of every session.
Extracts structured signal and appends one entry to the user's wyrd log.

Each entry is a small JSON object:
  {
    "ts":        "2026-05-23T...",
    "session_id": "...",
    "question":   "...",
    "topic":      "business_build | relationship | mental_health | creative | learning | other",
    "heat":       0.0-1.0,        # emotional intensity detected
    "resolved":   true/false,     # did the verdict close the question?
    "tension":    "...",           # the core tension in one phrase
    "recurring":  true/false,     # model flagged this as a repeat pattern
    "anchor":     "..."            # standout phrase from the question (if any)
  }

This file only writes. orlog/wyrd/wyrd_profile.py reads and aggregates.
"""

import sys
import json
import os
import hashlib
from datetime import datetime, timezone
from pathlib import Path

import requests as _requests
from dotenv import load_dotenv

sys.path.insert(0, str(Path(__file__).parents[2]))
from llm_router import fast_call

load_dotenv(Path(__file__).parent.parent.parent / ".env")

_CLASSIFIER_URL = "https://tomsem77--mimir-classifier-serve.modal.run/extract_wyrd"


def _call_classifier(text: str) -> dict | None:
    try:
        r = _requests.post(_CLASSIFIER_URL, json={"text": text}, timeout=10)
        if r.status_code == 200:
            data = r.json()
            if "error" not in data:
                return data
    except Exception:
        pass
    return None


EXTRACT_PROMPT = """\
Read this decision question and the verdict that was given. Extract structured signal.

QUESTION: {question}

VERDICT SUMMARY: {verdict}

Respond with ONLY a JSON object — no explanation, no markdown:
{{
  "topic": "<one of: business_build | relationship | mental_health | creative | learning | identity | financial | other>",
  "heat": <float 0.0-1.0, emotional intensity — 0=purely analytical, 1=deeply personal/charged>,
  "resolved": <true if the verdict gave a clear actionable answer, false if it remained open>,
  "tension": "<the core tension in 8 words or fewer>",
  "recurring": <true if the question feels like something this person has wrestled with before — look for sunk-cost language, "keep coming back to", "again", "still", "I don't know why">,
  "anchor": "<one standout phrase from the question that reveals the emotional core, or empty string>"
}}"""


LOKI_EXTRACT_PROMPT = """\
Read this Loki conversation. The user was exploring something but may not have reached a verdict yet.
The goal Loki distilled is provided.

LOKI GOAL: {goal}

CONVERSATION (last 6 turns):
{conversation}

Extract structured signal from what the person was circling.
Respond with ONLY a JSON object — no explanation, no markdown:
{{
  "topic": "<one of: business_build | relationship | mental_health | creative | learning | identity | financial | other>",
  "heat": <float 0.0-1.0, emotional intensity>,
  "tension": "<the core tension in 8 words or fewer>",
  "recurring": <true if the person signals they've been here before — "keep coming back", "again", "still", "don't know why">,
  "anchor": "<one standout phrase from the conversation that reveals the emotional core, or empty string>"
}}"""


def _wyrd_path(user_id: str, data_dir: Path) -> Path:
    safe = hashlib.sha256(user_id.encode()).hexdigest()[:16]
    return data_dir / f"{safe}_wyrd.jsonl"


def write_entry(
    user_id: str,
    session_id: str,
    question: str,
    verdict: str,
    data_dir: Path,
) -> dict | None:
    """
    Extract signal from one session and append to the user's wyrd log.
    Returns the entry dict, or None if extraction failed.
    Called as a background task — failures are silent.
    """
    classifier_text = f"QUESTION: {question[:600]}\n\nVERDICT: {verdict[:400]}"
    signal = _call_classifier(classifier_text)

    if signal is None:
        prompt = EXTRACT_PROMPT.format(
            question=question[:800],
            verdict=verdict[:600],
        )
        try:
            raw = fast_call(
                "You are a signal extractor. Respond with JSON only.",
                prompt,
                256,
            )
            if raw.startswith("```"):
                raw = raw.split("```")[1]
                if raw.startswith("json"):
                    raw = raw[4:]
            signal = json.loads(raw)
        except Exception:
            return None

    entry = {
        "ts":         datetime.now(timezone.utc).isoformat(),
        "session_id": session_id,
        "question":   question[:400],
        "verdict":    verdict[:400] if verdict else "",
        "topic":      signal.get("topic", "other"),
        "heat":       float(signal.get("heat", 0.5)),
        "resolved":   bool(signal.get("resolved", True)),
        "tension":    signal.get("tension", ""),
        "recurring":  bool(signal.get("recurring", False)),
        "anchor":     signal.get("anchor", ""),
    }

    path = _wyrd_path(user_id, data_dir)
    with open(path, "a") as f:
        f.write(json.dumps(entry) + "\n")

    return entry


def write_loki_entry(
    user_id: str,
    session_id: str,
    goal: str,
    messages: list,
    data_dir: Path,
    mode: str = "",
) -> dict | None:
    """
    Write a Wyrd entry from a Loki conversation that hasn't (yet) produced an Orlog verdict.
    Always written as resolved=False — the question was found but not answered.
    Called when Loki reaches [READY:] state and user is signed in.
    """
    if not goal:
        return None

    convo_text = "\n".join(
        f"{m['role'].upper()}: {m['content'][:200]}"
        for m in messages[-8:]
    )

    classifier_text = f"QUESTION: {goal[:400]}\n\nVERDICT: (loki session, not yet resolved)"
    signal = _call_classifier(classifier_text)

    if signal is None:
        prompt = LOKI_EXTRACT_PROMPT.format(
            goal=goal[:400],
            conversation=convo_text,
        )
        try:
            raw = fast_call(
                "You are a signal extractor. Respond with JSON only.",
                prompt,
                200,
            )
            if raw.startswith("```"):
                raw = raw.split("```")[1]
                if raw.startswith("json"):
                    raw = raw[4:]
            signal = json.loads(raw)
        except Exception:
            return None

    entry = {
        "ts":         datetime.now(timezone.utc).isoformat(),
        "session_id": session_id,
        "question":   goal[:400],
        "source":     "loki",
        "mode":       mode,
        "topic":      signal.get("topic", "other"),
        "heat":       float(signal.get("heat", 0.5)),
        "resolved":   False,
        "tension":    signal.get("tension", ""),
        "recurring":  bool(signal.get("recurring", False)),
        "anchor":     signal.get("anchor", ""),
    }

    path = _wyrd_path(user_id, data_dir)
    with open(path, "a") as f:
        f.write(json.dumps(entry) + "\n")

    return entry
