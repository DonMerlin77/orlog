"""
orlog/builder/build_loop.py — Self-healing build loop.

After Builder generates scaffolding:
  1. Argus scans the generated files for issues
  2. If issues found: Hermes researches fixes, patches the files
  3. Repeat until clean or max_iterations reached
  4. Returns a report: what was fixed, what remains, confidence score

The observer state in code: Orlog detects its own generated errors
without panicking, patches them, and documents what it learned.

Usage:
    from orlog.builder.build_loop import BuildLoop

    loop = BuildLoop()
    report = loop.run(orlog_result, output_dir="/tmp/coeus_scaffold")
    loop.print_report(report)
"""

import os
import sys
import re
import json
from datetime import datetime
from pathlib import Path
from dotenv import load_dotenv

sys.path.insert(0, str(Path(__file__).parents[2]))
from llm_router import fast_call

load_dotenv(Path(__file__).parent.parent.parent / ".env")

MAX_ITERATIONS = 3

# Placeholder patterns: matched whole-file, replaced in one pass
PLACEHOLDER_REPLACEMENTS = [
    (r'https://your[^"\'\s]*\.supabase\.co[^"\'\s]*',
     'os.getenv("SUPABASE_URL", "https://your-project.supabase.co")',
     "Supabase URL placeholder → env var"),
    (r'"your-supabase-anon-key[^"]*"',
     'os.getenv("SUPABASE_ANON_KEY", "")',
     "Supabase anon key placeholder → env var"),
    (r'"your-anon-key[^"]*"',
     'os.getenv("SUPABASE_ANON_KEY", "")',
     "Anon key placeholder → env var"),
    (r'"your-supabase-service-key[^"]*"',
     'os.getenv("SUPABASE_SERVICE_KEY", "")',
     "Service key placeholder → env var"),
]

# Scan-only patterns: flagged but not auto-replaced (need human or LLM review)
SCAN_PATTERNS = [
    (r'raise NotImplementedError',
     "NotImplementedError — will crash when called", "P2"),
]


class BuildLoop:
    def __init__(self, api_key: str = None):
        pass

    def run(self, result: dict, output_dir: str, max_iterations: int = MAX_ITERATIONS) -> dict:
        out     = Path(output_dir).expanduser()
        domain  = result.get("domain", "generic")
        verdict = result.get("synthesis", {}).get("verdict", "")

        report = {
            "output_dir":  str(out),
            "domain":      domain,
            "iterations":  [],
            "final_status": "pending",
            "timestamp":   datetime.now().isoformat(),
        }

        for i in range(max_iterations):
            iteration = {"iteration": i + 1, "findings": [], "patches": []}

            files = self._collect_files(out)

            # Pass 1: whole-file placeholder replacements (fast, deterministic)
            placeholder_patches = self._apply_placeholder_replacements(files)
            iteration["patches"].extend(placeholder_patches)

            # Pass 2: scan for remaining issues needing LLM review
            findings = self._scan(files)
            iteration["findings"] = findings

            if not findings and not placeholder_patches:
                iteration["result"] = "clean"
                report["iterations"].append(iteration)
                report["final_status"] = "clean"
                break

            if findings:
                for finding in findings[:3]:
                    patch = self._patch(finding, domain, verdict)
                    if patch:
                        iteration["patches"].append(patch)

            report["iterations"].append(iteration)

            if i == max_iterations - 1:
                remaining = self._scan(self._collect_files(out))
                report["final_status"] = "clean" if not remaining else "issues_remain"
                report["remaining_issues"] = remaining

        self._write_loop_report(out, report)
        return report

    def _collect_files(self, out: Path) -> list[Path]:
        extensions = {".py", ".gd", ".sql", ".ts", ".js"}
        files = []
        for ext in extensions:
            files.extend(out.rglob(f"*{ext}"))
        return files

    def _apply_placeholder_replacements(self, files: list[Path]) -> list[dict]:
        """Whole-file find-and-replace for placeholder patterns. One pass, all occurrences."""
        patches = []
        for filepath in files:
            try:
                source = filepath.read_text()
            except Exception:
                continue
            modified = source
            for pattern, replacement, description in PLACEHOLDER_REPLACEMENTS:
                if re.search(pattern, modified):
                    modified = re.sub(pattern, replacement, modified)
                    patches.append({
                        "file":    str(filepath),
                        "line":    0,
                        "issue":   description,
                        "original": pattern,
                        "fix":     replacement,
                    })
            if modified != source:
                filepath.write_text(modified)
        return patches

    def _scan(self, files: list[Path]) -> list[dict]:
        findings = []
        for filepath in files:
            try:
                source = filepath.read_text()
            except Exception:
                continue
            for pattern, description, severity in SCAN_PATTERNS:
                for match in re.finditer(pattern, source, re.MULTILINE):
                    line_num = source[:match.start()].count("\n") + 1
                    findings.append({
                        "file":        str(filepath),
                        "line":        line_num,
                        "description": description,
                        "severity":    severity,
                        "snippet":     source.splitlines()[line_num - 1].strip()[:120],
                    })
        return findings

    def _patch(self, finding: dict, domain: str, verdict: str) -> dict | None:
        """LLM-assisted patch for scan-only issues that need human judgment."""
        filepath = Path(finding["file"])
        try:
            source = filepath.read_text()
        except Exception:
            return None

        prompt = (
            f"Fix this issue in a generated {filepath.suffix} file for a {domain} project.\n"
            f"Architecture: {verdict[:200]}\n\n"
            f"Issue at line {finding['line']}: {finding['description']}\n"
            f"Snippet: {finding['snippet']}\n\n"
            f"File (truncated):\n{source[:2000]}\n\n"
            f"Return ONLY the fixed line or block. No explanation."
        )
        try:
            fix = fast_call(
                f"You are a code fixer for {filepath.suffix} files. Return ONLY the fixed line or block. No explanation.",
                prompt,
                300,
            )

            lines    = source.splitlines()
            line_idx = finding["line"] - 1
            if 0 <= line_idx < len(lines):
                original = lines[line_idx]
                lines[line_idx] = fix
                filepath.write_text("\n".join(lines))
                return {"file": str(filepath), "line": finding["line"],
                        "issue": finding["description"], "original": original, "fix": fix[:150]}
        except Exception:
            pass
        return None

    def _write_loop_report(self, out: Path, report: dict):
        (out / "build_loop_report.json").write_text(json.dumps(report, indent=2))

        total_findings = sum(len(it["findings"]) for it in report["iterations"])
        total_patches  = sum(len(it["patches"])  for it in report["iterations"])
        status = report["final_status"]

        md = f"""# Build Loop Report
> Generated: {report['timestamp']}
> Domain: {report['domain']}
> Status: **{status.upper()}**

## Summary
- Iterations run: {len(report['iterations'])}
- Total issues found: {total_findings}
- Patches applied: {total_patches}
- Final status: {status}

## Iteration Log
"""
        for it in report["iterations"]:
            md += f"\n### Iteration {it['iteration']}\n"
            md += f"- Issues found: {len(it['findings'])}\n"
            md += f"- Patches applied: {len(it['patches'])}\n"
            for p in it.get("patches", []):
                md += f"  - Fixed `{Path(p['file']).name}:{p['line']}` — {p['issue'][:60]}\n"

        if report.get("remaining_issues"):
            md += "\n## Remaining Issues (manual review needed)\n"
            for issue in report["remaining_issues"]:
                md += f"- [{issue['severity']}] `{Path(issue['file']).name}:{issue['line']}` — {issue['description']}\n"

        (out / "BUILD_LOOP_REPORT.md").write_text(md)

    def print_report(self, report: dict):
        print("=" * 56)
        print("  Build Loop Report")
        print(f"  Status: {report['final_status'].upper()}")
        print("=" * 56)
        for it in report["iterations"]:
            print(f"\n  Iteration {it['iteration']}: "
                  f"{len(it['findings'])} issues, "
                  f"{len(it['patches'])} patched")
            for p in it.get("patches", []):
                print(f"    ✓ {Path(p['file']).name}:{p['line']} — {p['issue'][:55]}")
        if report.get("remaining_issues"):
            print(f"\n  Remaining: {len(report['remaining_issues'])} issue(s) need manual review")
        print("=" * 56)
