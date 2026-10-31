"""
orlog/council/synesthesia.py — Cross-domain translation agent.

Synesthesia sees across domains the way synesthetes perceive across senses:
a sound has a color, a color has a texture. Applied to reasoning: a problem
in one domain has a structural analogue in another domain that already solved it.

This agent runs AFTER the five cognitive styles and BEFORE the synthesizer.
It reads all five perspectives and asks: what field outside this domain has
already solved this exact structural problem? What does that solution look like
translated back into the current domain?

The five styles find what's wrong. Synesthesia finds where it's already been fixed.
"""

import sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).parents[2]))
import os
from llm_router import fast_call, _try_openrouter_one

SYNESTHESIA_SYSTEM = (
    "You are the Synesthesia agent in the Orlog Round Table. "
    "You perceive across domains the way a synesthete perceives across senses. "
    "Your job: read five perspectives on a problem, then find the structural analogue "
    "in a completely different field that has already solved it. "
    "Think: what does this problem look like in biology, in physics, in architecture, "
    "in military strategy, in music, in evolutionary systems, in supply chains? "
    "Where has this exact pattern appeared before, and what did that field do about it? "
    "Bring one strong cross-domain insight — specific, translatable, and non-obvious. "
    "Do not summarize what the other agents said. Find something they couldn't see."
)


def synesthesia_perspective(
    question: str,
    context: dict,
    perspectives: dict,
    brain: str = "",
    client=None,
) -> str:
    """
    Generate the synesthesia perspective after all five cognitive styles have spoken.

    client:       anthropic.Anthropic instance
    question:     the original deliberation question
    context:      full Orlog context dict
    perspectives: dict of {style_name: text} from the five agents
    brain:        optional brain content for context injection
    """
    import json

    perspectives_text = "\n\n".join(
        f"[{name.upper()}]\n{text}"
        for name, text in perspectives.items()
    )

    brain_section = f"\n\n{brain}\n\n" if brain else ""

    prompt = (
        f"{brain_section}"
        f"DOMAIN: {context.get('domain', 'unknown')}\n"
        f"QUESTION: {question}\n\n"
        f"THE FIVE PERSPECTIVES:\n{perspectives_text}\n\n"
        f"Now: what domain outside of '{context.get('domain', 'this field')}' has already "
        f"solved the structural pattern underneath this problem? "
        f"Name the analogue domain, describe the parallel, and translate the solution back. "
        f"3-5 sentences. Be specific — name the actual mechanism or principle, not just the field."
    )

    try:
        if os.getenv("ORLOG_MODEL_VOICES"):   # own model family, like the council voices (council.py VOICE_MODELS)
            out = _try_openrouter_one(SYNESTHESIA_SYSTEM, prompt, 400, "qwen/qwen3-235b-a22b-2507", temperature=0.7)
            if out:
                return out
        return fast_call(SYNESTHESIA_SYSTEM, prompt, 400)
    except Exception as e:
        return f"[synesthesia error: {e}]"
