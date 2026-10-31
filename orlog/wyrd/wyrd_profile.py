"""
orlog/wyrd/wyrd_profile.py

Reads a user's wyrd log and aggregates it into a full profile.
Called by the /wyrd API endpoint and by Lore to open sessions.

Profile shape:
  {
    "sessions":   int,
    "topics":     { topic: { count, heat_avg, last_seen } },
    "recurring":  [ tension strings that appeared 2+ times ],
    "anchors":    [ high-heat anchor phrases ],
    "open_threads": [ unresolved tensions ],
    "score_trend": [],           # placeholder — wired when Orlog scores are stored here
    "top_topic":  str,
    "top_heat":   float,
    "mahoraga_calibrations": int  # number of times Mahoraga has adjusted based on this profile
  }
"""

import json
from collections import defaultdict
from pathlib import Path

from orlog.wyrd.wyrd_writer import _wyrd_path


def load_entries(user_id: str, data_dir: Path) -> list[dict]:
    path = _wyrd_path(user_id, data_dir)
    if not path.exists():
        return []
    entries = []
    for line in path.read_text().splitlines():
        line = line.strip()
        if line:
            try:
                entries.append(json.loads(line))
            except Exception:
                pass
    return entries


def build_profile(user_id: str, data_dir: Path) -> dict:
    entries = load_entries(user_id, data_dir)
    if not entries:
        return _empty_profile()

    topic_stats: dict[str, dict] = defaultdict(lambda: {"count": 0, "heat_sum": 0.0, "last_seen": ""})
    tension_counts: dict[str, int] = defaultdict(int)
    anchors: list[str] = []
    open_threads: list[str] = []

    for e in entries:
        t = e.get("topic", "other")
        topic_stats[t]["count"] += 1
        topic_stats[t]["heat_sum"] += e.get("heat", 0.5)
        ts = e.get("ts", "")
        if ts > topic_stats[t]["last_seen"]:
            topic_stats[t]["last_seen"] = ts

        tension = e.get("tension", "").strip()
        if tension:
            tension_counts[tension] += 1

        anchor = e.get("anchor", "").strip()
        heat = e.get("heat", 0.0)
        if anchor and heat >= 0.65 and anchor not in anchors:
            anchors.append(anchor)

        if not e.get("resolved", True) and tension:
            if tension not in open_threads:
                open_threads.append(tension)

    topics_out = {}
    for topic, stats in topic_stats.items():
        c = stats["count"]
        topics_out[topic] = {
            "count":     c,
            "heat_avg":  round(stats["heat_sum"] / c, 2),
            "last_seen": stats["last_seen"][:10],
        }

    recurring = [t for t, count in tension_counts.items() if count >= 2]

    top_topic = max(topics_out, key=lambda t: topics_out[t]["count"]) if topics_out else "other"
    top_heat  = max((v["heat_avg"] for v in topics_out.values()), default=0.0)

    return {
        "sessions":              len(entries),
        "topics":                topics_out,
        "recurring":             recurring,
        "anchors":               anchors[:10],
        "open_threads":          open_threads[:10],
        "score_trend":           [],
        "top_topic":             top_topic,
        "top_heat":              round(top_heat, 2),
        "mahoraga_calibrations": 0,
    }


_STOP_WORDS = {
    "a","an","the","and","or","but","in","on","at","to","for","of","with",
    "is","it","i","my","me","this","that","what","how","do","should","would",
    "could","be","been","have","has","will","if","about","from","are","was",
    "not","so","its","just","like","more","than","up","out","when","as","by",
    "we","they","you","your","their","our","which","who","there","use","get",
}

def _topic_keywords(entries: list[dict], topic: str) -> str | None:
    """Extract the most common meaningful word from questions sharing a topic."""
    from collections import Counter
    words: list[str] = []
    for e in entries:
        if e.get("topic") == topic:
            text = (e.get("question", "") + " " + e.get("anchor", "")).lower()
            for w in text.split():
                w = w.strip(".,?!\"'()-")
                if len(w) > 3 and w not in _STOP_WORDS:
                    words.append(w)
    if not words:
        return None
    top = Counter(words).most_common(1)[0][0]
    return top


def lore_opening(user_id: str, data_dir: Path) -> str | None:
    """
    Returns a one-sentence pattern notice to surface at session start.
    None if the user is new or the profile is thin (< 2 sessions).
    """
    entries = load_entries(user_id, data_dir)
    profile = build_profile(user_id, data_dir)
    if profile["sessions"] < 2:
        return None

    # 1. Exact recurring tensions — strongest signal
    if profile["recurring"]:
        return f"You keep returning to: {profile['recurring'][0]}."

    # 2. Open thread — something still unresolved
    if profile["open_threads"]:
        return f"Still unresolved from last time: {profile['open_threads'][0]}."

    # 3. Topic cluster — 3+ sessions sharing a topic in recent history
    recent = sorted(entries, key=lambda e: e.get("ts", ""), reverse=True)[:6]
    from collections import Counter
    recent_topics = Counter(e.get("topic", "other") for e in recent)
    top_recent, top_count = recent_topics.most_common(1)[0]
    if top_count >= 3 and top_recent != "other":
        keyword = _topic_keywords(recent, top_recent)
        label = top_recent.replace("_", " ")
        if keyword:
            return f"I notice a lot of your recent thinking has been about {keyword} — you've come back to it {top_count} times."
        return f"A lot of your recent questions have been about {label}."

    # 4. Anchors — common word across high-heat sessions
    anchors = profile.get("anchors", [])
    if len(anchors) >= 2:
        all_anchor_words: list[str] = []
        for a in anchors:
            for w in a.lower().split():
                w = w.strip(".,?!\"'()-")
                if len(w) > 3 and w not in _STOP_WORDS:
                    all_anchor_words.append(w)
        from collections import Counter
        if all_anchor_words:
            common = Counter(all_anchor_words).most_common(1)[0][0]
            return f"There's a thread through your sessions: {common} keeps showing up."

    return None


def _empty_profile() -> dict:
    return {
        "sessions":              0,
        "topics":                {},
        "recurring":             [],
        "anchors":               [],
        "open_threads":          [],
        "score_trend":           [],
        "top_topic":             "other",
        "top_heat":              0.0,
        "mahoraga_calibrations": 0,
    }
