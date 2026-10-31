"""
orlog/argus/ — Static code analysis + live monitoring. Named for the
hundred-eyed Greek titan: sees everything, misses nothing.

Scans a set of Python files for known anti-patterns. Uses Hermes lessons
as a dynamic pattern library — every crash Hermes researches also trains
Argus to catch the same pattern in other files.

Two scan modes:
  regex  — deterministic, instant, catches known patterns
  claude — semantic, uses Hermes lessons as pattern library

Usage:
    from orlog.argus import Argus

    a = Argus(
        scan_dirs=["agents", "scripts"],
        log_path="logs/argus_orlog.json",
        hermes_log_path="logs/hermes_orlog.json",
    )
    report = a.scan()
    a.print_report(report)

Extra patterns can be injected at runtime:
    a.scan(extra_patterns=[
        (r"page=\\d+", "Deprecated pagination pattern", "P1"),
    ])
"""

import re
import sys
import json
import os
import subprocess
from datetime import datetime
from pathlib import Path

from dotenv import load_dotenv

sys.path.insert(0, str(Path(__file__).parents[2]))
from llm_router import fast_call

load_dotenv(Path(__file__).parent.parent.parent / ".env")

UNIVERSAL_PATTERNS = [
    (
        r'print\s*\(f["\'].*\{(?:token|secret|password|api_key|access_token|client_secret)[^}]*\}',
        "Possible credential leak — f-string interpolating a sensitive variable in print().",
        "P1",
    ),
    (
        r'(?<!\bos\.getenv\b)(?<!\bos\.environ\b)\b(?:API_KEY|ACCESS_TOKEN|CLIENT_SECRET|PASSWORD)\s*=\s*["\'][^"\']{10,}["\']',
        "Possible hardcoded credential — use os.getenv() instead.",
        "P1",
    ),
    (
        r'except\s*:\s*\n\s*pass',
        "Bare except: pass silently swallows all errors — bugs will go undetected.",
        "P2",
    ),
    (
        r'time\.sleep\(0\)',
        "sleep(0) is a no-op — check intended sleep duration.",
        "P3",
    ),
    (
        r'json\.loads\([^)]+\)(?!\s*except)',
        "json.loads() without try/except — will crash on malformed input.",
        "P3",
    ),
]

SKIP_NAMES = {"__pycache__", "__init__.py", "venv", ".git"}


class Argus:
    def __init__(
        self,
        scan_dirs: list = None,
        log_path: str = None,
        hermes_log_path: str = None,
        api_key: str = None,
    ):
        self.scan_dirs       = [Path(d) for d in (scan_dirs or [])]
        self.log_path        = Path(log_path or "logs/argus_orlog.json")
        self.hermes_log_path = Path(hermes_log_path) if hermes_log_path else None

    def collect_files(self) -> list[Path]:
        files = []
        for directory in self.scan_dirs:
            if not directory.exists():
                continue
            for f in sorted(directory.glob("*.py")):
                if any(skip in f.parts for skip in SKIP_NAMES):
                    continue
                if f.name in SKIP_NAMES:
                    continue
                files.append(f)
        return files

    def regex_scan(self, files: list[Path], extra_patterns: list = None) -> list[dict]:
        patterns = UNIVERSAL_PATTERNS + (extra_patterns or [])
        findings = []
        for filepath in files:
            try:
                source = filepath.read_text()
            except Exception:
                continue
            for pattern, description, severity in patterns:
                for match in re.finditer(pattern, source, re.MULTILINE):
                    line_num = source[:match.start()].count("\n") + 1
                    findings.append({
                        "file":        str(filepath),
                        "line":        line_num,
                        "pattern":     pattern[:60],
                        "description": description,
                        "severity":    severity,
                        "source":      "argus:regex",
                        "snippet":     source.splitlines()[line_num - 1].strip()[:120],
                    })
        return findings

    def load_hermes_lessons(self) -> list[str]:
        if not self.hermes_log_path or not self.hermes_log_path.exists():
            return []
        try:
            data    = json.loads(self.hermes_log_path.read_text())
            lessons = data.get("lessons_added", [])
            return [l.get("lesson", "") for l in lessons if l.get("lesson")]
        except Exception:
            return []

    def claude_scan(self, files: list[Path], lessons: list[str]) -> list[dict]:
        if not lessons:
            return []

        lesson_text = "\n".join(f"- {l}" for l in lessons[-20:])
        file_text   = ""
        for f in files[:15]:
            try:
                file_text += f"\n\n### {f}\n{f.read_text()[:6000]}"
            except Exception:
                continue

        if not file_text.strip():
            return []

        prompt = (
            f"You are Argus — a static code analysis agent.\n\n"
            f"These are lessons learned from real crashes and bugs in this codebase:\n"
            f"{lesson_text}\n\n"
            f"Review the following Python files for code that violates these lessons "
            f"or shows the same anti-patterns that caused past crashes.\n\n"
            f"{file_text}\n\n"
            f"Return a JSON array of findings (empty array [] if nothing found):\n"
            f'[{{"file":"agents/example.py","line":42,"description":"...","severity":"P1|P2|P3",'
            f'"snippet":"...","lesson_reference":"..."}}]\n\n'
            f"Only flag real issues. Return only the JSON array."
        )
        try:
            text  = fast_call(
                "You are Argus — a static code analysis agent.",
                prompt,
                1000,
            )
            start = text.find("[")
            end   = text.rfind("]") + 1
            if start == -1:
                return []
            findings = json.loads(text[start:end])
            for f in findings:
                f["source"] = "argus:claude"
            return findings
        except Exception:
            return []

    def pip_audit(self) -> list[dict]:
        findings = []
        try:
            result = subprocess.run(
                ["pip", "audit", "--format", "json", "--skip-editable"],
                capture_output=True, text=True, timeout=60,
            )
            if result.stdout.strip():
                for v in json.loads(result.stdout):
                    for vuln in v.get("vulns", []):
                        findings.append({
                            "file":        f"requirements ({v.get('name')} {v.get('version')})",
                            "line":        0,
                            "description": f"CVE {vuln.get('id', '?')}: {vuln.get('description', '')[:120]}",
                            "severity":    "P1" if vuln.get("fix_versions") else "P2",
                            "source":      "argus:pip_audit",
                            "snippet":     f"Fix available: {vuln.get('fix_versions', 'none')}",
                        })
        except (FileNotFoundError, Exception):
            pass
        return findings

    def _load_existing(self) -> list[dict]:
        if self.log_path.exists():
            try:
                return json.loads(self.log_path.read_text())
            except Exception:
                pass
        return []

    def _save(self, findings: list[dict]):
        self.log_path.parent.mkdir(exist_ok=True)
        self.log_path.write_text(json.dumps(findings[-100:], indent=2))

    def _deduplicate(self, new: list[dict], existing: list[dict]) -> list[dict]:
        sigs = {
            (e.get("file", ""), e.get("line", 0), e.get("description", "")[:60])
            for e in existing if e.get("status") != "resolved"
        }
        result = []
        for f in new:
            sig = (f.get("file", ""), f.get("line", 0), f.get("description", "")[:60])
            if sig not in sigs:
                f["found_at"] = datetime.now().isoformat()
                f["status"]   = "open"
                result.append(f)
        return result

    def scan(self, extra_patterns: list = None) -> dict:
        files          = self.collect_files()
        regex_findings = self.regex_scan(files, extra_patterns)
        lessons        = self.load_hermes_lessons()
        claude_findings = self.claude_scan(files, lessons)
        pip_findings   = self.pip_audit()

        all_new  = regex_findings + claude_findings + pip_findings
        existing = self._load_existing()
        new_only = self._deduplicate(all_new, existing)

        all_findings = [f for f in existing if f.get("status") != "resolved"]
        all_findings += new_only

        self._save(all_findings)

        return {
            "files_scanned":   len(files),
            "lessons_used":    len(lessons),
            "new_findings":    new_only,
            "total_open":      len([f for f in all_findings if f.get("status") != "resolved"]),
            "p1":              [f for f in new_only if f.get("severity") == "P1"],
            "p2":              [f for f in new_only if f.get("severity") == "P2"],
            "timestamp":       datetime.now().isoformat(),
        }

    def print_report(self, report: dict):
        print("=" * 56)
        print("  Argus — Code Analysis")
        print(f"  {datetime.now().strftime('%Y-%m-%d %H:%M')}")
        print("=" * 56)
        print(f"  Files scanned  : {report['files_scanned']}")
        print(f"  Hermes lessons : {report['lessons_used']}")
        print(f"  New findings   : {len(report['new_findings'])}")
        print(f"  Total open     : {report['total_open']}")
        print()
        for f in report["new_findings"]:
            print(f"  [{f.get('severity')}][{f.get('source','?')}] "
                  f"{f.get('file','')}:{f.get('line','')}")
            print(f"    {f.get('description','')[:90]}")
        print("=" * 56)
