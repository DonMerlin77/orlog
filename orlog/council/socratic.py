"""
orlog/council/socratic.py — The Socratic Agent.

Does not argue. Does not answer. Asks one question — the question that,
if answered honestly, changes the entire decision. Fires after all council
voices, before synthesis, so the synthesizer must confront it.
"""

import sys
import json
from pathlib import Path
sys.path.insert(0, str(Path(__file__).parents[2]))
from llm_router import fast_call


SOCRATIC_SYSTEM = (
    "You are the Socratic agent in the Orlog Round Table. "
    "You do not argue. You do not answer. You ask ONE question. "
    "The question must expose the most important assumption that nobody in the council stated explicitly. "
    "Not a clarifying question. Not a practical question. A foundational one — the kind that, "
    "if answered honestly, changes the entire decision. "
    "It should be uncomfortable. It should make the council pause. "
    "Return only the question. No preamble, no explanation, no alternatives."
)


def socratic_question(
    question: str,
    context: dict,
    perspectives: dict,
    brain: str = "",
    client=None,
) -> str:
    perspectives_text = "\n\n".join(
        f"[{name.upper()}]\n{text}"
        for name, text in perspectives.items()
    )
    brain_section = f"\n\n{brain}\n\n" if brain else ""

    user_prompt = (
        f"{brain_section}"
        f"ORIGINAL QUESTION: {question}\n\n"
        f"PROJECT CONTEXT: {json.dumps(context)}\n\n"
        f"COUNCIL PERSPECTIVES:\n{perspectives_text}\n\n"
        f"What is the ONE question these perspectives all failed to ask? "
        f"The question whose answer changes everything."
    )

    try:
        return fast_call(SOCRATIC_SYSTEM, user_prompt, 120)
    except Exception as e:
        return f"[socratic error: {e}]"
