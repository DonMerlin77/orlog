"""
orlog/agents/mirror_audit.py — Mirror + GapAnalysis on the live Orlog interface.

Fetches the live HTML, runs a stranger's-eye evaluation (Mirror), then cross-checks
against creator intent (Narcissus), and synthesizes the gap (GapAnalysis).
Saves the report to logs/mirror_audit.json and prints the top 3 fixes.

Run manually:
    cd ~/shopify_store && source venv/bin/activate
    python3 -m orlog.agents.mirror_audit

Options:
    --url      URL to audit (default: https://www.orlog.fyi)
    --local    Read local index.html instead of fetching live URL
"""

import json
import argparse
import requests
from pathlib import Path
from datetime import datetime

from dotenv import load_dotenv
load_dotenv(Path(__file__).parents[2] / ".env")

from orlog.mirror.mirror import GapAnalysis

AUDIT_LOG   = Path("logs/mirror_audit.json")
LOCAL_HTML  = Path(__file__).parents[1] / "app" / "static" / "index.html"
LIVE_URL    = "https://www.orlog.fyi"

CREATOR_INTENT = (
    "Orlog is a reasoning interface — not an assistant, not a chatbot. "
    "It takes a decision or situation, maps the load-bearing assumption, names the real conflict, "
    "surfaces the missing information, and identifies the failure condition. "
    "Then it gives a falsifiable verdict and an execution plan. "
    "The target user is someone facing a hard decision who wants structured thinking, not reassurance. "
    "The Norse rune aesthetic signals seriousness and permanence — decisions have weight. "
    "The interface should feel like a tool, not a product."
)

WHAT_WORKS = (
    "The rune animation sequence (ᚠ → ᚢ → ᚷ → ᛉ → Verdict) with the pause before verdict. "
    "The plain-English translation block below the trace. "
    "The Challenge button for adversarial counter-cases. "
    "The Execute button for step-by-step action plans. "
    "The dark obsidian + amber color palette signaling precision over friendliness."
)


def _fetch_html(url: str) -> str:
    headers = {
        "User-Agent": (
            "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
            "AppleWebKit/537.36 (KHTML, like Gecko) "
            "Chrome/124.0.0.0 Safari/537.36"
        ),
        "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
        "Accept-Language": "en-US,en;q=0.5",
    }
    r = requests.get(url, headers=headers, timeout=15)
    r.raise_for_status()
    return r.text


def _strip_to_visible(html: str) -> str:
    from html.parser import HTMLParser

    class TextExtractor(HTMLParser):
        def __init__(self):
            super().__init__()
            self.parts = []
            self._skip = False

        def handle_starttag(self, tag, attrs):
            if tag in ("script", "style"):
                self._skip = True

        def handle_endtag(self, tag):
            if tag in ("script", "style"):
                self._skip = False

        def handle_data(self, data):
            if not self._skip:
                stripped = data.strip()
                if stripped:
                    self.parts.append(stripped)

    p = TextExtractor()
    p.feed(html)
    return "\n".join(p.parts)


def run(url: str = None, use_local: bool = False, local_path: Path = None) -> dict:
    if use_local:
        path = local_path or LOCAL_HTML
        print(f"Reading local: {path}")
        raw_html = path.read_text()
        source   = str(path.name)
    else:
        target = url or LIVE_URL
        print(f"Fetching: {target}")
        raw_html = _fetch_html(target)
        source   = target

    visible_text = _strip_to_visible(raw_html)
    print(f"Extracted {len(visible_text)} chars of visible text.")

    ga = GapAnalysis()
    print("Running Mirror + Narcissus + GapAnalysis...")
    result = ga.full_audit(
        output=visible_text,
        output_type="web interface for a reasoning tool",
        intent=CREATOR_INTENT,
        context={"what_works": WHAT_WORKS},
    )

    report = {
        "timestamp": datetime.now().isoformat(),
        "source":    source,
        **result,
    }

    existing = json.loads(AUDIT_LOG.read_text()) if AUDIT_LOG.exists() else []
    existing.append(report)
    AUDIT_LOG.parent.mkdir(exist_ok=True)
    AUDIT_LOG.write_text(json.dumps(existing[-20:], indent=2))

    print(f"\n{'='*60}")
    print("MIRROR (stranger's view)")
    print(f"{'='*60}")
    print(result["mirror"])

    print(f"\n{'='*60}")
    print("NARCISSUS (creator's view)")
    print(f"{'='*60}")
    print(result["narcissus"])

    print(f"\n{'='*60}")
    print("GAP ANALYSIS — what needs to change")
    print(f"{'='*60}")
    print(result["gap"])

    print(f"\nSaved to: {AUDIT_LOG}")
    return report


STATIC_DIR = Path(__file__).parents[1] / "app" / "static"

ALL_PAGES = [
    ("index.html",    "Main app — Orlog + Loki tabs, demo panel, tourney"),
    ("how.html",      "How it works page — 3-step walkthrough, session examples"),
    ("compare.html",  "Compare page — side-by-side reasoning trace"),
    ("signin.html",   "Sign-in page"),
    ("terms.html",    "Terms of service"),
]


def run_all() -> list:
    results = []
    for filename, page_desc in ALL_PAGES:
        path = STATIC_DIR / filename
        if not path.exists():
            print(f"  Skipping {filename} — not found")
            continue
        print(f"\n{'='*60}")
        print(f"Auditing: {filename} — {page_desc}")
        print(f"{'='*60}")
        result = run(use_local=True, local_path=path)
        results.append({"page": filename, **result})
    return results


def main():
    parser = argparse.ArgumentParser(description="Mirror audit of the Orlog interface")
    parser.add_argument("--url",   default=None,  help="URL to audit (default: orlog.fyi)")
    parser.add_argument("--local", action="store_true", default=True,
                        help="Use local HTML files (default: True)")
    parser.add_argument("--live",  action="store_true",
                        help="Fetch live URL instead of local files")
    parser.add_argument("--all",   action="store_true",
                        help="Audit all pages (index, how, compare, signin, terms)")
    parser.add_argument("--page",  default=None,
                        help="Audit a specific page filename, e.g. how.html")
    args = parser.parse_args()

    if args.all:
        run_all()
        return

    if args.page:
        path = STATIC_DIR / args.page
        run(use_local=True, local_path=path)
        return

    use_local = not args.live
    run(url=args.url, use_local=use_local)


if __name__ == "__main__":
    import sys
    sys.path.insert(0, str(Path(__file__).parents[2]))
    main()
