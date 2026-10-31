"""
orlog/crystal_ball/ — Outcome forecasting. Informs decisions, never votes.

Predicts whether a proposed action will succeed by pattern-matching against
historical log data. Confidence score degrades in uncharted territory or
regime mismatch. Tracks actual outcomes to self-calibrate over time.

NOTE (SPEC): Current win_rate is self-reported (circular). Real validation
requires external signal — revenue, retention, task completion — not Orlog
scores. Phase 1: predict Orlog score (useful filter). Phase 2: predict
real-world outcome (requires external hookup).

Usage:
    from orlog.crystal_ball import CrystalBall

    cb = CrystalBall(log_path="logs/crystal_ball_orlog.json")
    result = cb.predict(
        agent_name="listing",
        proposed_action={"type": "create", "category": "kitchen"},
        context={"env_score": 72},
        agent_log_path="logs/listing_log.json",
    )
    cb.record_outcome(result["id"], actual_outcome=1.0)
"""

import sys
import json
import os
import uuid
from datetime import datetime, timezone, timedelta
from pathlib import Path
from dotenv import load_dotenv

sys.path.insert(0, str(Path(__file__).parents[2]))
from llm_router import fast_call

load_dotenv(Path(__file__).parent.parent.parent / ".env")

N_FLOOR_DEFAULT         = 5
RECENCY_DAYS            = 60
REGIME_HALVE_THRESHOLD  = 0.4

OUTCOME_KEYS = [
    "engagement", "pnl", "result", "status", "revenue", "clicks",
    "open_rate", "conversion", "sell_through", "vote", "action",
]


# ── Feature extraction ────────────────────────────────────────────────────────

def _entry_to_text(entry: dict) -> str:
    parts = []
    for k, v in entry.items():
        if isinstance(v, (str, int, float, bool)):
            parts.append(f"{k}:{v}")
        elif isinstance(v, dict):
            for kk, vv in v.items():
                parts.append(f"{k}.{kk}:{vv}")
    return " ".join(str(p).lower() for p in parts)


def _recency_weight(entry: dict) -> float:
    ts_val = entry.get("timestamp") or entry.get("ts") or entry.get("date") or ""
    if not ts_val:
        return 1.0
    try:
        ts = datetime.fromisoformat(str(ts_val).replace("Z", "+00:00"))
        if ts.tzinfo is None:
            ts = ts.replace(tzinfo=timezone.utc)
        return 3.0 if (datetime.now(timezone.utc) - ts).days <= RECENCY_DAYS else 1.0
    except Exception:
        return 1.0


def _extract_outcome(entry: dict):
    for key in OUTCOME_KEYS:
        val = entry.get(key)
        if val is None:
            continue
        if isinstance(val, (int, float)):
            return float(val)
        if isinstance(val, str):
            mapping = {
                "posted": 1.0, "success": 1.0, "buy": 1.0, "sold": 1.0,
                "failed": -1.0, "error": -1.0, "hold": 0.0, "sell": -0.5,
                "warn": -0.5, "ok": 1.0,
            }
            return mapping.get(val.lower())
    return None


def _regime_tag(context: dict) -> str:
    env = context.get("env_score", context.get("env", 50))
    vol = context.get("volatility", context.get("vol", "normal"))
    if isinstance(env, (int, float)):
        if env >= 65:
            return "bullish"
        if env <= 35:
            return "bearish"
    if isinstance(vol, str) and "high" in vol.lower():
        return "volatile"
    return "neutral"


def _find_similar(proposed: dict, entries: list, n_floor: int) -> list:
    keywords = set(_entry_to_text(proposed).split())
    scored = []
    for entry in entries:
        tokens  = set(_entry_to_text(entry).split())
        overlap = len(keywords & tokens) / max(len(keywords), 1)
        weight  = _recency_weight(entry)
        scored.append((overlap * weight, entry))
    scored.sort(key=lambda x: -x[0])
    return [e for _, e in scored[:max(n_floor * 4, 50)]]


def _compute_stats(matches: list, current_regime: str) -> dict:
    outcomes = []
    regime_matches = 0
    for entry in matches:
        o = _extract_outcome(entry)
        if o is not None:
            outcomes.append(o)
            if _regime_tag(entry) == current_regime:
                regime_matches += 1

    n = len(outcomes)
    if n == 0:
        return {"n": 0, "ev": None, "win_rate": None, "regime_match_pct": 0.0}

    positive = [o for o in outcomes if o > 0]
    negative = [o for o in outcomes if o < 0]
    win_rate = len(positive) / n
    avg_win  = sum(positive) / len(positive) if positive else 0.0
    avg_loss = abs(sum(negative) / len(negative)) if negative else 0.0
    ev       = (win_rate * avg_win) - ((1 - win_rate) * avg_loss)

    return {
        "n":                n,
        "ev":               round(ev, 4),
        "win_rate":         round(win_rate, 3),
        "avg_win":          round(avg_win, 4),
        "avg_loss":         round(avg_loss, 4),
        "regime_match_pct": round(regime_matches / n, 3),
    }


def _confidence_from_stats(stats: dict) -> float:
    if stats["ev"] is None:
        return 0.0
    base = min(1.0, max(0.0, 0.5 + stats["ev"]))
    if stats.get("regime_match_pct", 1.0) < REGIME_HALVE_THRESHOLD:
        base *= 0.5
    return round(base, 3)


def _verdict(confidence: float, ev, uncharted: bool) -> str:
    if uncharted or ev is None:
        return "UNCHARTED"
    if ev < -0.1:
        return "ABORT"
    if confidence >= 0.65 and ev > 0:
        return "PROCEED"
    return "WAIT"


def _parse_ts(val):
    if not val:
        return None
    try:
        ts = datetime.fromisoformat(str(val).replace("Z", "+00:00"))
        return ts.replace(tzinfo=timezone.utc) if ts.tzinfo is None else ts
    except Exception:
        return None


# ── CrystalBall class ─────────────────────────────────────────────────────────

class CrystalBall:
    def __init__(self, log_path: str = None, api_key: str = None):
        self.log_path = Path(log_path or "logs/crystal_ball_orlog.json")
        self.log_path.parent.mkdir(exist_ok=True)

    def _load_log(self) -> list:
        if self.log_path.exists():
            try:
                return json.loads(self.log_path.read_text())
            except Exception:
                pass
        return []

    def _save_log(self, entries: list):
        self.log_path.write_text(json.dumps(entries[-2000:], indent=2))

    def _load_agent_log(self, log_path: str) -> list:
        p = Path(log_path)
        if not p.exists():
            return []
        try:
            data = json.loads(p.read_text())
            if isinstance(data, list):
                return data
            if isinstance(data, dict):
                for v in data.values():
                    if isinstance(v, list) and v:
                        return v
        except Exception:
            pass
        return []

    def _generate_narrative(self, agent: str, proposed: dict, stats: dict,
                            context: dict, regime: str, matches: list) -> dict:
        sample = json.dumps(matches[:5], indent=2, default=str)
        prompt = (
            f"Proposed action:\n{json.dumps(proposed, indent=2, default=str)}\n\n"
            f"Context: {json.dumps(context, indent=2, default=str)}\n"
            f"Regime: {regime}\n"
            f"Historical stats ({stats['n']} comparable actions):\n"
            f"  Win rate: {stats.get('win_rate', 'N/A')}\n"
            f"  Expected value: {stats.get('ev', 'N/A')}\n"
            f"  Regime match: {stats.get('regime_match_pct', 'N/A')}\n\n"
            f"Sample past outcomes:\n{sample}\n\n"
            f"Reply with ONLY valid JSON:\n"
            f'{{"forecast":"<one sentence>","counter_signal":"<one sentence>","recommendation":"<one sentence>"}}'
        )
        try:
            text  = fast_call(
                f"You are Crystal Ball, outcome forecaster for the {agent} agent. Reply with ONLY valid JSON.",
                prompt,
                300,
            )
            start = text.find("{")
            end   = text.rfind("}") + 1
            return json.loads(text[start:end])
        except Exception as e:
            return {
                "forecast":        f"Narrative generation failed: {e}",
                "counter_signal":  "Unknown.",
                "recommendation":  "Review logs manually.",
            }

    def predict(self, agent_name: str, proposed_action: dict,
                context: dict, agent_log_path: str,
                n_floor: int = N_FLOOR_DEFAULT) -> dict:
        entries    = self._load_agent_log(agent_log_path)
        regime     = _regime_tag(context)
        matches    = _find_similar(proposed_action, entries, n_floor)
        stats      = _compute_stats(matches, regime)
        uncharted  = stats["n"] < n_floor
        confidence = _confidence_from_stats(stats)
        verdict    = _verdict(confidence, stats.get("ev"), uncharted)
        narrative  = self._generate_narrative(agent_name, proposed_action, stats, context, regime, matches)

        result = {
            "id":             str(uuid.uuid4())[:8],
            "timestamp":      datetime.now().isoformat(),
            "agent":          agent_name,
            "verdict":        verdict,
            "confidence":     confidence if not uncharted else None,
            "n_matches":      stats["n"],
            "regime":         regime,
            "ev":             stats.get("ev"),
            "win_rate":       stats.get("win_rate"),
            "uncharted":      uncharted,
            "forecast":       narrative.get("forecast"),
            "counter_signal": narrative.get("counter_signal"),
            "recommendation": narrative.get("recommendation"),
            "proposed":       proposed_action,
            "actual":         None,
            "surprise":       None,
        }

        log = self._load_log()
        log.append(result)
        self._save_log(log)
        return result

    def record_outcome(self, prediction_id: str, actual_outcome, notes: str = ""):
        log = self._load_log()
        for entry in log:
            if entry.get("id") == prediction_id:
                entry["actual"] = actual_outcome
                predicted_ev = entry.get("ev")
                if predicted_ev is not None:
                    actual_float = actual_outcome if isinstance(actual_outcome, float) else (
                        1.0 if str(actual_outcome).lower() in ("success", "posted", "win") else -1.0
                    )
                    divergence = abs(actual_float - predicted_ev)
                    entry["surprise"] = round(divergence, 3)
                    if divergence > 0.5:
                        entry["surprise_flag"]  = True
                        entry["learning_note"]  = (
                            f"Prediction diverged by {divergence:.2f} — review similar past cases."
                        )
                entry["outcome_notes"] = notes
                break
        self._save_log(log)

    def track_record(self, agent: str = None, n: int = 20) -> dict:
        log     = self._load_log()
        entries = [e for e in log if e.get("actual") is not None]
        if agent:
            entries = [e for e in entries if e.get("agent") == agent]

        verdicts  = {}
        surprises = [e for e in entries if e.get("surprise_flag")]
        for e in entries:
            v = e.get("verdict", "UNKNOWN")
            verdicts[v] = verdicts.get(v, 0) + 1

        return {
            "total_with_outcomes": len(entries),
            "verdict_breakdown":   verdicts,
            "surprises":           len(surprises),
            "recent_surprises": [
                {"id": e["id"], "agent": e["agent"],
                 "forecast": e.get("forecast", "")[:80],
                 "note": e.get("learning_note", "")}
                for e in surprises[-5:]
            ],
            "recent_predictions": log[-n:],
        }
