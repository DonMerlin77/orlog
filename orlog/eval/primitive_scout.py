"""
orlog/eval/primitive_scout.py — Mímirrúnar Primitive Scout

Automatically discovers new cognitive primitives from council deliberation logs,
proposes them to Orlog for approval, and adds approved ones to the vocabulary.

How it works:
  1. Scan council logs for recurring structural phrases not covered by current Mímirrúnar
  2. Cluster by semantic similarity (bigram overlap — no embeddings needed)
  3. For top N candidates, ask Orlog: is this a new primitive or covered by existing ones?
  4. Orlog approves (with tier placement) or rejects (with reason + nearest existing primitive)
  5. Approved primitives get appended to mimir/tokenizer_setup.py automatically

Orlog's approval criteria (from 2026-05-17 council verdict):
  - Does removing this primitive degrade reasoning quality on held-out questions?
  - Is it a Core Reasoning Operation (Tier 1), Deliberation Structure (Tier 2),
    or Style Signature (Tier 3)?
  - Is it genuinely distinct from all existing Mímirrúnar, or is it a paraphrase?
  - Minimum evidence: appears in >= MIN_OCCURRENCES council logs

Bloat prevention:
  - Orlog must explicitly approve — no auto-add without council sign-off
  - Candidate must meet MIN_OCCURRENCES threshold
  - Orlog can suggest "merge with existing <|X|>" instead of adding new

Usage:
    python3 -m orlog.eval.primitive_scout          # scan + propose to Orlog
    python3 -m orlog.eval.primitive_scout --dry    # scan only, no Orlog call
    python3 -m orlog.eval.primitive_scout --stats  # show current vocabulary coverage
"""

import os
import re
import json
import argparse
from pathlib import Path
from collections import Counter
from datetime import datetime
from dotenv import load_dotenv

load_dotenv(Path(__file__).parent.parent.parent / ".env")

COUNCIL_LOG   = Path("logs/orlog_council.json")
SCOUT_LOG     = Path("logs/primitive_scout_log.json")
TOKENIZER_SRC = Path("mimir/tokenizer_setup.py")

MIN_OCCURRENCES = 5   # minimum times a pattern must appear before proposing
MAX_CANDIDATES  = 5   # max candidates proposed to Orlog per scout run
APPROVAL_MARKER = "# ── Scout-approved primitives"  # insertion point in tokenizer_setup.py


def _load_council_logs(n: int = 200) -> list[str]:
    if not COUNCIL_LOG.exists():
        return []
    log = json.loads(COUNCIL_LOG.read_text())
    texts = []
    for entry in log[-n:]:
        s = entry.get("synthesis", "")
        if isinstance(s, dict):
            s = " ".join(str(v) for v in s.values())
        persp = entry.get("perspectives", {})
        for p in persp.values():
            texts.append(str(p))
        texts.append(str(s))
    return texts


def _current_primitives() -> set[str]:
    """Load current Mímirrúnar names (without <| |> wrappers)."""
    try:
        from mimir.tokenizer_setup import MIMIRRUNA
        return {t.strip("<|>") for t in MIMIRRUNA}
    except ImportError:
        return set()


# Content words and acronyms that appear frequently but aren't reasoning structures
_NOISE_WORDS = {
    "what", "this", "that", "with", "from", "have", "will", "been", "they",
    "their", "when", "then", "also", "some", "more", "most", "such", "each",
    # domain acronyms (content, not structure)
    "tcet", "rlhf", "dpo", "lora", "llm", "api", "seo", "roi", "kpi",
    # meta words that show up in synthesis headers
    "synthesis", "analysis", "cognitive", "agent", "style", "lens", "model",
    "revised", "version", "section", "note", "result", "output", "response",
}


def _extract_candidates(texts: list[str], primitives: set[str]) -> list[tuple[str, int]]:
    """
    Find recurring structural phrases in council output not covered by existing Mímirrúnar.
    Uses structural pattern matching + capitalized-phrase detection.
    Filters content words and domain acronyms — only genuine reasoning structures pass.
    """
    phrase_counts: Counter = Counter()

    # Patterns that signal reasoning structure (multi-word, not generic nouns)
    structural_patterns = [
        r'\b(the\s+\w+\s+(?:problem|assumption|constraint|boundary|failure|signal|bottleneck|tradeoff))\b',
        r'\b(this\s+(?:decision|choice|move|step)\s+\w+s\s+\w+)\b',
        r'\b(when\s+\w+\s+(?:meets|hits|reaches|exceeds|crosses|dominates))\b',
        r'\b(the\s+gap\s+between\s+\w+\s+and\s+\w+)\b',
        r'\b(\w+\s+cannot\s+be\s+(?:undone|reversed|recovered|retaken|unlearned))\b',
        r'\b(this\s+creates\s+a\s+(?:loop|cycle|dependency|constraint|ratchet))\b',
        r'\b(the\s+(?:real|actual|underlying|hidden|latent)\s+\w+\s+is)\b',
        r'\b(without\s+\w+\s+(?:the|this|that)\s+\w+\s+(?:fails|collapses|breaks))\b',
        r'\b(the\s+cost\s+of\s+(?:ignoring|delaying|avoiding|missing)\s+\w+)\b',
    ]

    for text in texts:
        text_lower = text.lower()
        # Extract structural phrases
        for pattern in structural_patterns:
            for match in re.finditer(pattern, text_lower):
                phrase = match.group(1).strip()
                if len(phrase.split()) >= 3:  # raised from 2 to reduce noise
                    phrase_counts[phrase] += 1

        # Extract capitalized structural signals — require 5+ chars, not in noise set
        for match in re.finditer(r'\b([A-Z][A-Z_]{4,})\b', text):
            word = match.group(1)
            if word not in primitives and not word.startswith("_") and word.lower() not in _NOISE_WORDS:
                phrase_counts[word.lower()] += 1

    # Filter by minimum occurrences, noise words, and proximity to existing primitives
    candidates = []
    for phrase, count in phrase_counts.most_common(50):
        if count < MIN_OCCURRENCES:
            break
        if phrase.lower() in _NOISE_WORDS:
            continue
        phrase_upper = phrase.upper().replace(" ", "_")
        if any(phrase_upper in p or p in phrase_upper for p in primitives):
            continue
        candidates.append((phrase, count))

    return candidates[:MAX_CANDIDATES * 2]


def _orlog_approve(candidates: list[tuple[str, int]], primitives: set[str]) -> list[dict]:
    """
    Ask Orlog whether each candidate is a new Mímirrúnar primitive.
    Returns list of approved primitives with tier and canonical name.
    """
    from orlog.core import Orlog

    if not candidates:
        return []

    candidate_list = "\n".join(
        f"  {i+1}. '{phrase}' (appears {count}x in council logs)"
        for i, (phrase, count) in enumerate(candidates)
    )
    existing_list = ", ".join(sorted(primitives)[:20]) + "..."

    question = f"""Primitive Scout has found these recurring patterns in council deliberation logs
that are NOT covered by existing Mímirrúnar:

{candidate_list}

Existing Mímirrúnar (partial): {existing_list}

For each candidate, decide:
A) APPROVE as Tier 1 (Core Reasoning Operation — what reasoning DOES)
B) APPROVE as Tier 2 (Deliberation Structure — how council is organized)
C) APPROVE as Tier 3 (Style Signature — who is speaking)
D) REJECT — already covered by <existing_primitive>
E) REJECT — not load-bearing, would add bloat without improving reasoning quality

For each APPROVED candidate, also provide:
- Canonical name in SCREAMING_SNAKE_CASE (becomes <|NAME|> primitive)
- One-line description of what it marks

Format your response as JSON array:
[{{"candidate": "...", "decision": "APPROVE_T1/APPROVE_T2/APPROVE_T3/REJECT",
   "canonical": "NAME_IF_APPROVED", "description": "...", "reason": "..."}}]"""

    o = Orlog(goal="Evaluate new Mímirrúnar primitive candidates", domain="research")
    result = o.run(question, task_type="research")
    synthesis = result.get("synthesis", "")
    if isinstance(synthesis, dict):
        synthesis = str(synthesis)

    # Parse JSON from synthesis
    approved = []
    try:
        start = synthesis.find("[")
        end   = synthesis.rfind("]") + 1
        if start >= 0 and end > start:
            decisions = json.loads(synthesis[start:end])
            for d in decisions:
                if "APPROVE" in d.get("decision", ""):
                    tier = {"APPROVE_T1": 1, "APPROVE_T2": 2, "APPROVE_T3": 3}.get(
                        d["decision"], 3
                    )
                    approved.append({
                        "canonical":   d.get("canonical", "").upper().replace(" ", "_"),
                        "description": d.get("description", ""),
                        "tier":        tier,
                        "candidate":   d.get("candidate", ""),
                        "reason":      d.get("reason", ""),
                        "timestamp":   datetime.now().isoformat(),
                    })
    except Exception as e:
        print(f"  [scout] Could not parse Orlog approval JSON: {e}")
        print(f"  Raw synthesis: {synthesis[:500]}")

    return approved


def _add_to_vocabulary(approved: list[dict]):
    """Append Orlog-approved primitives to TIER3_STYLE in tokenizer_setup.py."""
    if not approved or not TOKENIZER_SRC.exists():
        return

    src = TOKENIZER_SRC.read_text()

    # Build insertion block
    lines = [f"\n    # Scout-approved {datetime.now().strftime('%Y-%m-%d')}"]
    for p in approved:
        token = f"<|{p['canonical']}|>"
        tier_comment = f"Tier {p['tier']}"
        lines.append(f'    "{token}",  # {tier_comment}: {p["description"]}')

    insertion = "\n".join(lines)

    # Insert before the closing bracket of TIER3_STYLE
    # Find the last item in TIER3_STYLE
    marker = '"<|PROCESS_AUDITOR|>",'
    if marker in src:
        src = src.replace(marker, marker + insertion)
        TOKENIZER_SRC.write_text(src)
        print(f"  Added {len(approved)} primitive(s) to Mímirrúnar vocabulary")
    else:
        print(f"  Could not find insertion point — add manually: {[p['canonical'] for p in approved]}")


def _save_scout_log(candidates, approved):
    existing = []
    if SCOUT_LOG.exists():
        try:
            existing = json.loads(SCOUT_LOG.read_text())
        except Exception:
            pass
    existing.append({
        "timestamp":  datetime.now().isoformat(),
        "candidates": [{"phrase": p, "count": c} for p, c in candidates],
        "approved":   approved,
    })
    SCOUT_LOG.write_text(json.dumps(existing[-100:], indent=2))


def coverage_stats(texts: list[str], primitives: set[str]) -> dict:
    """Measure how many council outputs contain at least one Mímirrúnar reference."""
    if not texts:
        return {}
    covered = sum(
        1 for t in texts
        if any(p.lower() in t.lower() or f"<|{p}|>" in t for p in primitives)
    )
    prim_freq: Counter = Counter()
    for t in texts:
        for p in primitives:
            if p.lower() in t.lower() or f"<|{p}|>" in t:
                prim_freq[p] += 1
    return {
        "total_texts":    len(texts),
        "covered":        covered,
        "coverage_pct":   round(covered / len(texts) * 100, 1),
        "top_primitives": prim_freq.most_common(10),
        "zero_coverage":  [p for p in primitives if prim_freq[p] == 0],
    }


def run_scout(dry: bool = False, verbose: bool = True) -> list[dict]:
    texts      = _load_council_logs(n=200)
    primitives = _current_primitives()

    if verbose:
        print(f"[primitive-scout] Scanning {len(texts)} council texts, {len(primitives)} existing Mímirrúnar...")

    candidates = _extract_candidates(texts, primitives)

    if verbose:
        if candidates:
            print(f"  {len(candidates)} candidates found:")
            for phrase, count in candidates:
                print(f"    '{phrase}' ({count}x)")
        else:
            print("  No new candidates above threshold — vocabulary is current.")

    if dry or not candidates:
        return []

    if verbose:
        print(f"  Proposing top {min(len(candidates), MAX_CANDIDATES)} to Orlog for approval...")

    approved = _orlog_approve(candidates[:MAX_CANDIDATES], primitives)

    if approved:
        if verbose:
            for p in approved:
                print(f"  APPROVED (Tier {p['tier']}): <|{p['canonical']}|> — {p['description']}")
        _add_to_vocabulary(approved)
    else:
        if verbose:
            print("  Orlog rejected all candidates — vocabulary is stable.")

    _save_scout_log(candidates, approved)
    return approved


if __name__ == "__main__":
    import sys
    sys.path.insert(0, str(Path(__file__).parent.parent.parent))

    parser = argparse.ArgumentParser(description="Mímirrúnar Primitive Scout")
    parser.add_argument("--dry",   action="store_true", help="Scan only, no Orlog call")
    parser.add_argument("--stats", action="store_true", help="Show vocabulary coverage stats")
    parser.add_argument("--n",     type=int, default=200, help="Council logs to scan")
    args = parser.parse_args()

    if args.stats:
        texts      = _load_council_logs(n=args.n)
        primitives = _current_primitives()
        stats      = coverage_stats(texts, primitives)
        print(f"Coverage: {stats.get('covered')}/{stats.get('total_texts')} texts ({stats.get('coverage_pct')}%)")
        print("Top primitives by frequency:")
        for p, c in stats.get("top_primitives", []):
            print(f"  {p:25} {c}x")
        zero = stats.get("zero_coverage", [])
        if zero:
            print(f"Zero coverage ({len(zero)}): {', '.join(zero[:10])}")
    else:
        run_scout(dry=args.dry)
