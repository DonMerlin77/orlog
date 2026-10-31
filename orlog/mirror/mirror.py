"""
orlog/mirror/ — Mirror, Narcissus, and Gap Analysis.

Mirror: evaluates any output through a stranger's eyes. No context, no charity.
Narcissus: evaluates the same output through the creator's eyes — intent + pride.
GapAnalysis: finds what Narcissus is blind to by comparing the two perspectives.

The gap between Mirror and Narcissus is where blind spots live.

Usage:
    from orlog.mirror import Mirror, Narcissus, GapAnalysis

    m = Mirror()
    n = Narcissus()
    g = GapAnalysis()

    mirror_view   = m.evaluate(output="...", output_type="landing page", context={})
    narcissus_view = n.evaluate(output="...", intent="...", what_works="...")
    gap           = g.analyze(mirror_view, narcissus_view)
"""

import os
import sys
import json
from datetime import datetime, timezone
from pathlib import Path

from dotenv import load_dotenv

sys.path.insert(0, str(Path(__file__).parents[2]))
from llm_router import fast_call

load_dotenv(Path(__file__).parent.parent.parent / ".env")

MIRROR_SYSTEM = """You are a stranger seeing this for the first time. You have no internal context about how it was built, why decisions were made, or what the creator intended. You evaluate purely from first impressions — what you see, what confuses you, what's missing, what makes you leave. Be honest, be specific, be cold. You are not here to be encouraging."""

NARCISSUS_SYSTEM = """You are the creator who built this. You know every decision, every trade-off, every piece of intent behind the work. You are proud of what you made. Evaluate from the inside — what you were trying to communicate, what you believe is strong, where you believe the work succeeds. Be specific about your intent."""

SYNTHESIZER_SYSTEM = """You are a gap analyst. You have two perspectives on the same output: the stranger (Mirror) and the creator (Narcissus). Your job is to find the gaps — the things the creator believes are clear that the stranger never received, the things the creator thinks are weak that the stranger found compelling. The gap is where the work actually needs to change."""


class Mirror:
    def __init__(self, api_key: str = None):
        pass

    def evaluate(self, output: str, output_type: str = "output", context: dict = None) -> dict:
        ctx = json.dumps(context or {})
        evaluation = fast_call(
            MIRROR_SYSTEM,
            f"You are seeing this {output_type} for the first time. "
            f"Context you are NOT given: {ctx}\n\n"
            f"OUTPUT TO EVALUATE:\n{output}\n\n"
            f"As a stranger: What do you understand? What confuses you? "
            f"What's missing? What would make you leave? Be specific.",
            600,
        )
        return {
            "perspective": "mirror",
            "output_type": output_type,
            "evaluation":  evaluation,
            "timestamp":   datetime.now(timezone.utc).isoformat(),
        }


class Narcissus:
    def __init__(self, api_key: str = None):
        pass

    def evaluate(self, output: str, intent: str = "", what_works: str = "") -> dict:
        evaluation = fast_call(
            NARCISSUS_SYSTEM,
            f"OUTPUT YOU CREATED:\n{output}\n\n"
            f"YOUR INTENT: {intent}\n"
            f"WHAT YOU BELIEVE WORKS: {what_works}\n\n"
            f"Evaluate from the inside. What were you trying to say? "
            f"What are you proud of? Where do you think it succeeds? Be specific.",
            600,
        )
        return {
            "perspective": "narcissus",
            "intent":      intent,
            "evaluation":  evaluation,
            "timestamp":   datetime.now(timezone.utc).isoformat(),
        }


class GapAnalysis:
    def __init__(self, api_key: str = None):
        pass

    def analyze(self, mirror_result: dict, narcissus_result: dict) -> dict:
        evaluation = fast_call(
            SYNTHESIZER_SYSTEM,
            f"MIRROR (stranger's view):\n{mirror_result['evaluation']}\n\n"
            f"NARCISSUS (creator's view):\n{narcissus_result['evaluation']}\n\n"
            f"Find the gaps. Structure your response as:\n"
            f"INVISIBLE (creator thought it was clear, stranger didn't receive it):\n"
            f"SURPRISING (creator thought it was weak, stranger found it compelling):\n"
            f"FIXES (the 3 highest-impact changes based on the gap):\n",
            600,
        )
        return {
            "perspective": "gap_analysis",
            "evaluation":  evaluation,
            "mirror":      mirror_result,
            "narcissus":   narcissus_result,
            "timestamp":   datetime.now(timezone.utc).isoformat(),
        }

    def full_audit(self, output: str, output_type: str, intent: str = "", context: dict = None) -> dict:
        m = Mirror(api_key=os.getenv("ANTHROPIC_API_KEY"))
        n = Narcissus(api_key=os.getenv("ANTHROPIC_API_KEY"))

        mirror_view    = m.evaluate(output, output_type, context)
        narcissus_view = n.evaluate(output, intent)
        gap            = self.analyze(mirror_view, narcissus_view)

        return {
            "output_type": output_type,
            "mirror":      mirror_view["evaluation"],
            "narcissus":   narcissus_view["evaluation"],
            "gap":         gap["evaluation"],
            "timestamp":   gap["timestamp"],
        }
