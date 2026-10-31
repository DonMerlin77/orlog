"""
orlog/eval/outcome.py — External outcome feedback adapter.

Converts real-world signals (revenue, conversions, retention, etc.) into
a normalized 0-10 Orlog score and calls orlog.score() to close the DPO loop.

Supported signal types:
  revenue      — gross revenue over a measurement window
  conversion   — conversion rate (visitors → buyers)
  retention    — repeat purchase rate
  engagement   — click / open / watch rate
  binary       — worked / didn't work (for non-measurable outcomes)

Usage:
    from orlog.eval.outcome import record_outcome

    record_outcome(
        orlog_instance=o,
        run_id="abc12345",
        signal_type="revenue",
        value=340.0,
        baseline=0.0,          # revenue before this run's changes
        target=500.0,          # what success looks like
        notes="Week 1 post-launch",
    )
"""

from datetime import datetime
from pathlib import Path
import json


# ── Scoring curves per signal type ───────────────────────────────────────────
# Maps (value, baseline, target) → 0-10 score
# All curves are linear between anchor points; capped at 10.

def _score_revenue(value: float, baseline: float, target: float) -> float:
    if target <= baseline:
        return 5.0
    progress = (value - baseline) / (target - baseline)
    return round(min(10.0, max(0.0, progress * 10)), 2)


def _score_conversion(value: float, baseline: float, target: float) -> float:
    # Typical e-commerce: 1% = barely alive, 3% = healthy, 5%+ = strong
    anchors = [(0.0, 0.0), (0.01, 3.0), (0.02, 5.5), (0.03, 7.0), (0.05, 9.0), (0.08, 10.0)]
    for i in range(len(anchors) - 1):
        lo_v, lo_s = anchors[i]
        hi_v, hi_s = anchors[i + 1]
        if lo_v <= value <= hi_v:
            t = (value - lo_v) / (hi_v - lo_v)
            return round(lo_s + t * (hi_s - lo_s), 2)
    return 10.0 if value >= 0.08 else 0.0


def _score_retention(value: float, baseline: float, target: float) -> float:
    # Repeat purchase rate: 0%=0, 10%=5, 25%=8, 40%+=10
    anchors = [(0.0, 0.0), (0.10, 5.0), (0.25, 8.0), (0.40, 10.0)]
    for i in range(len(anchors) - 1):
        lo_v, lo_s = anchors[i]
        hi_v, hi_s = anchors[i + 1]
        if lo_v <= value <= hi_v:
            t = (value - lo_v) / (hi_v - lo_v)
            return round(lo_s + t * (hi_s - lo_s), 2)
    return 10.0 if value >= 0.40 else 0.0


def _score_engagement(value: float, baseline: float, target: float) -> float:
    # Email open rate: <15%=poor, 25%=ok, 40%=strong, 55%+=excellent
    anchors = [(0.0, 0.0), (0.15, 3.0), (0.25, 5.5), (0.40, 8.0), (0.55, 10.0)]
    for i in range(len(anchors) - 1):
        lo_v, lo_s = anchors[i]
        hi_v, hi_s = anchors[i + 1]
        if lo_v <= value <= hi_v:
            t = (value - lo_v) / (hi_v - lo_v)
            return round(lo_s + t * (hi_s - lo_s), 2)
    return 10.0 if value >= 0.55 else 0.0


def _score_binary(value: float, baseline: float, target: float) -> float:
    # value: 1.0 = worked, 0.0 = failed, 0.5 = partial
    return round(min(10.0, max(0.0, value * 10)), 2)


_SCORERS = {
    "revenue":    _score_revenue,
    "conversion": _score_conversion,
    "retention":  _score_retention,
    "engagement": _score_engagement,
    "binary":     _score_binary,
}

SIGNAL_TYPES = list(_SCORERS.keys())


def normalize_signal(signal_type: str, value: float, baseline: float = 0.0, target: float = 1.0) -> float:
    """Convert a raw signal value to a 0-10 Orlog score."""
    scorer = _SCORERS.get(signal_type)
    if not scorer:
        raise ValueError(f"Unknown signal_type '{signal_type}'. Valid: {SIGNAL_TYPES}")
    return scorer(value, baseline, target)


def record_outcome(
    orlog_instance,
    run_id: str,
    signal_type: str,
    value: float,
    baseline: float = 0.0,
    target: float   = 1.0,
    window_days: int = 7,
    notes: str       = "",
    log_path: str    = None,
) -> dict:
    """
    Normalize a real-world signal and record it as the Orlog score for run_id.

    orlog_instance: a live Orlog object (needs .score())
    run_id:         the run to label (from result["id"])
    signal_type:    one of SIGNAL_TYPES
    value:          the measured value (revenue in $, rate as 0-1 float, etc.)
    baseline:       value before the Orlog-guided changes (default 0)
    target:         what 10/10 looks like for this signal
    window_days:    measurement window (logged for context, not used in scoring)
    notes:          freeform context (e.g. "Week 1 post-launch, US only")
    """
    score = normalize_signal(signal_type, value, baseline, target)

    full_notes = (
        f"[outcome:{signal_type}] value={value}, baseline={baseline}, "
        f"target={target}, window={window_days}d"
        + (f" | {notes}" if notes else "")
    )

    orlog_instance.score(run_id, score, notes=full_notes)

    entry = {
        "run_id":       run_id,
        "signal_type":  signal_type,
        "value":        value,
        "baseline":     baseline,
        "target":       target,
        "window_days":  window_days,
        "orlog_score":  score,
        "notes":        notes,
        "recorded_at":  datetime.now().isoformat(),
    }

    log_path = Path(log_path or "logs/outcome_log.json")
    log_path.parent.mkdir(exist_ok=True)
    entries = []
    if log_path.exists():
        try:
            entries = json.loads(log_path.read_text())
        except Exception:
            pass
    entries.append(entry)
    log_path.write_text(json.dumps(entries, indent=2))

    return entry


def outcome_summary(log_path: str = None) -> dict:
    """Summarize all recorded outcomes — signal types, score distribution, labeled runs."""
    log_path = Path(log_path or "logs/outcome_log.json")
    if not log_path.exists():
        return {"total": 0, "by_signal": {}}

    entries = json.loads(log_path.read_text())
    by_signal = {}
    for e in entries:
        s = e["signal_type"]
        if s not in by_signal:
            by_signal[s] = []
        by_signal[s].append(e["orlog_score"])

    return {
        "total": len(entries),
        "by_signal": {
            s: {
                "n":   len(scores),
                "avg": round(sum(scores) / len(scores), 2),
                "min": min(scores),
                "max": max(scores),
            }
            for s, scores in by_signal.items()
        },
    }
