"""
orlog/hermes/ — Research relay + inter-agent communication.

Named after the Greek messenger: carries knowledge between agents without
distorting it. Each layer has ONE job; Hermes moves information, never judges it.

Reads conflict/failure logs, researches the fix online (multi-platform),
validates the lesson with a blind second read, then writes it to a brain file.

Usage:
    from orlog.hermes import Hermes

    h = Hermes(brain_path="agents/intelligence/reasoning_brain.md")
    lesson = h.research_and_write_lesson(
        domain="finance",
        problem="momentum signals produce false positives in low-volume markets",
    )
    # lesson is also appended to brain_path automatically
"""

import os
import sys
import json
import time
import requests
from datetime import datetime
from pathlib import Path
from dotenv import load_dotenv

sys.path.insert(0, str(Path(__file__).parents[2]))
from llm_router import fast_call, quality_call

load_dotenv(Path(__file__).parent.parent.parent / ".env")

_PLATFORMS = [
    ("site:github.com",              "GitHub"),
    ("site:gitlab.com",              "GitLab"),
    ("site:huggingface.co",          "HuggingFace"),
    ("site:arxiv.org",               "arXiv"),
    ("site:eugeneyan.com OR site:hamel.dev OR site:newsletter.pragmaticengineer.com", "Eng Blogs"),
]

CONFIDENCE_VERIFIED  = 70
CONFIDENCE_UNCERTAIN = 40


def web_search(query: str, num_results: int = 5) -> str:
    """DuckDuckGo HTML scrape — no API key needed."""
    try:
        url = "https://html.duckduckgo.com/html/"
        headers = {"User-Agent": "Mozilla/5.0 (compatible; HermesOrlog/1.0)"}
        r = requests.post(url, data={"q": query}, headers=headers, timeout=10)
        from html.parser import HTMLParser

        class SnippetParser(HTMLParser):
            def __init__(self):
                super().__init__()
                self.snippets, self.capture, self.current = [], False, ""
            def handle_starttag(self, tag, attrs):
                if "result__snippet" in dict(attrs).get("class", ""):
                    self.capture, self.current = True, ""
            def handle_endtag(self, tag):
                if self.capture and tag == "a":
                    if self.current.strip():
                        self.snippets.append(self.current.strip())
                    self.capture = False
            def handle_data(self, data):
                if self.capture:
                    self.current += data

        parser = SnippetParser()
        parser.feed(r.text)
        results = parser.snippets[:num_results]
        return "\n".join(f"- {s}" for s in results) if results else "No results found."
    except Exception as e:
        return f"Search failed: {e}"


def multi_platform_search(query: str, results_per_platform: int = 3) -> str:
    """Search across code/research platforms, tag results by source."""
    sections = []
    for site_filter, label in _PLATFORMS:
        result = web_search(f"{query} {site_filter}", num_results=results_per_platform)
        if result and "No results" not in result and "Search failed" not in result:
            sections.append(f"[{label}]\n{result}")
        time.sleep(0.4)

    general = web_search(query, num_results=results_per_platform)
    if general and "No results" not in general and "Search failed" not in general:
        sections.append(f"[General]\n{general}")

    return "\n\n".join(sections) if sections else "No results found across platforms."


class Hermes:
    def __init__(self, brain_path: str = None, log_path: str = None, api_key: str = None):
        self.brain_path = Path(brain_path) if brain_path else None
        self.log_path   = Path(log_path) if log_path else Path("logs/hermes_orlog.json")

    def _load_log(self) -> dict:
        if self.log_path.exists():
            try:
                return json.loads(self.log_path.read_text())
            except Exception:
                pass
        return {"runs": [], "lessons_added": []}

    def _save_log(self, log: dict):
        self.log_path.parent.mkdir(exist_ok=True)
        self.log_path.write_text(json.dumps(log, indent=2))

    def validate_lesson(self, lesson: str, search_results: str) -> tuple[int, str]:
        """
        Two independent Claude calls on the same raw data.
        Returns (confidence 0-100, validator_lesson).
        >= 70 VERIFIED, 40-69 UNCERTAIN (write with caveat), < 40 REJECTED.
        """
        validator_lesson = fast_call(
            "You are a lesson extractor. Write exactly what the search results say.",
            f"Read these web search results and write ONE actionable lesson (2-3 sentences). "
            f"Only use what the results actually say. No outside knowledge.\n\n"
            f"Search results:\n{search_results}\n\nWrite the lesson now:",
            200,
        )

        score_text = fast_call(
            "You are a similarity scorer. Reply with only a number 0-100.",
            f"Rate how much these two lessons agree on a scale of 0 to 100. "
            f"0=contradictory. 100=identical advice. Reply with just the number.\n\n"
            f"Lesson A: {lesson[:300]}\n\nLesson B: {validator_lesson[:300]}",
            10,
        )

        try:
            score = int("".join(c for c in score_text if c.isdigit())[:3])
            score = max(0, min(100, score))
        except (ValueError, IndexError):
            score = 0

        return score, validator_lesson

    def research_and_write_lesson(self, domain: str, problem: str) -> dict:
        """
        Research a problem online → validate → write to brain file.
        Returns a dict with lesson, confidence, status (verified/uncertain/rejected).
        """
        search_query = fast_call(
            "You are a search query writer. Output only the query, nothing else.",
            f"Write a 1-line web search query to find actionable fixes for:\n{problem}\n"
            f"Just the query, nothing else.",
            60,
        ).strip('"')

        search_results = multi_platform_search(search_query)

        lesson = quality_call(
            "You are a research synthesizer. Write specific, actionable lessons.",
            f"Domain: {domain}\nProblem: {problem}\n\n"
            f"Web research findings:\n{search_results}\n\n"
            f"Write ONE specific, actionable lesson (2-4 sentences) that addresses this problem. "
            f"Start with: \"HERMES {datetime.now().strftime('%Y-%m-%d')}:\"",
            300,
        )

        confidence, validator_lesson = self.validate_lesson(lesson, search_results)

        if confidence >= CONFIDENCE_VERIFIED:
            status = "verified"
            brain_lesson = lesson
        elif confidence >= CONFIDENCE_UNCERTAIN:
            status = "uncertain"
            brain_lesson = f"[UNVERIFIED — confidence {confidence}/100] {lesson}"
        else:
            status = "rejected"
            brain_lesson = None

        if brain_lesson and self.brain_path and self.brain_path.exists():
            with open(self.brain_path, "a") as f:
                f.write(f"\n\n{brain_lesson}")

        result = {
            "domain":     domain,
            "problem":    problem,
            "lesson":     lesson,
            "confidence": confidence,
            "status":     status,
            "query":      search_query,
            "timestamp":  datetime.now().isoformat(),
        }

        log = self._load_log()
        log["lessons_added"].append(result)
        self._save_log(log)

        return result

    def relay(self, logs: list[dict], domain: str, max_problems: int = 3) -> list[dict]:
        """
        Read a list of log entries, identify the top failures, research and patch them.
        logs: list of dicts, each with at minimum an 'error' or 'problem' key.
        """
        problems = []
        for entry in logs:
            err = entry.get("error") or entry.get("problem") or entry.get("conflict") or ""
            if err and len(err) > 10:
                problems.append(err)

        problems = problems[:max_problems]
        results  = []
        for problem in problems:
            result = self.research_and_write_lesson(domain, problem)
            results.append(result)
            time.sleep(1)

        return results
