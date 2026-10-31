"""
Domain-agnostic Sentinel. Takes any dict of {name: log_path} and scans for errors.
"""

import json
from datetime import datetime, timezone, timedelta
from pathlib import Path

ERROR_CLASSES = {
    "credentials": ["expired", "revoked", "invalid_grant", "401", "403", "unauthorized", "token"],
    "rate_limit":  ["429", "rate limit", "throttle", "too many requests", "quota"],
    "parse":       ["jsondecodeerror", "expecting", "delimiter", "parse error", "invalid json"],
    "data":        ["nonetype", "keyerror", "indexerror", "attributeerror", "none has no attribute"],
    "network":     ["timeout", "connectionerror", "503", "502", "504", "connection refused"],
    "module":      ["modulenotfounderror", "importerror", "no module named"],
    "code":        ["typeerror", "valueerror", "assertionerror", "runtimeerror", "nameerror"],
}

FIX_HINTS = {
    "credentials": "Refresh or rotate the relevant API token/key",
    "rate_limit":  "Add sleep() between API calls or reduce batch size",
    "parse":       "Check model output format — likely unescaped chars in JSON string",
    "data":        "Guard against None/missing keys before calling .get() or indexing",
    "network":     "Transient network issue — check connectivity or add retry logic",
    "module":      "Install the missing package in the project venv",
    "code":        "Logic error — review the traceback",
    "unknown":     "No pattern matched — inspect the raw error manually",
}


def classify(error_text: str) -> str:
    low = error_text.lower()
    for cls, patterns in ERROR_CLASSES.items():
        if any(p in low for p in patterns):
            return cls
    return "unknown"


class Sentinel:
    def __init__(self, log_paths: dict, report_path: Path = None):
        self.log_paths = log_paths
        self.report_path = report_path

    def scan(self, since_hours: float = None) -> dict:
        since = None
        if since_hours:
            since = datetime.now(timezone.utc) - timedelta(hours=since_hours)

        errors = self._extract(since)
        errors = self._deduplicate(errors)
        known  = self._load_known()

        new_errors = [
            e for e in errors
            if (e["source"], e["class"], e["error"][:80]) not in known
        ]

        by_class = {}
        for e in errors:
            by_class[e["class"]] = by_class.get(e["class"], 0) + 1

        report = {
            "generated_at": datetime.now(timezone.utc).isoformat(),
            "total_errors": len(errors),
            "new_errors":   len(new_errors),
            "by_class":     by_class,
            "errors":       errors,
            "new":          new_errors,
        }

        if self.report_path:
            self.report_path.write_text(json.dumps(report, indent=2))

        return report

    def _extract(self, since):
        errors = []
        for source, path in self.log_paths.items():
            path = Path(path)
            if not path.exists():
                continue
            try:
                raw = json.loads(path.read_text())
                entries = raw if isinstance(raw, list) else raw.get("errors", raw.get("runs", []))
                if not isinstance(entries, list):
                    continue
                for entry in entries[-20:]:
                    if not isinstance(entry, dict):
                        continue
                    err = entry.get("error") or entry.get("errors")
                    if not err:
                        continue
                    err_text = str(err)
                    errors.append({
                        "source":    source,
                        "error":     err_text[:200],
                        "class":     classify(err_text),
                        "hint":      FIX_HINTS[classify(err_text)],
                        "timestamp": entry.get("timestamp", ""),
                        "seen_at":   datetime.now(timezone.utc).isoformat(),
                    })
            except Exception:
                continue
        return errors

    def _deduplicate(self, errors):
        seen = set()
        out = []
        for e in errors:
            key = (e["source"], e["class"], e["error"][:80])
            if key not in seen:
                seen.add(key)
                out.append(e)
        return out

    def _load_known(self):
        if not self.report_path or not Path(self.report_path).exists():
            return set()
        try:
            d = json.loads(Path(self.report_path).read_text())
            return {(e["source"], e["class"], e["error"][:80]) for e in d.get("errors", [])}
        except Exception:
            return set()
