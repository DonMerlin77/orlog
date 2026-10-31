"""
orlog/council/intake.py — Clinical intake before deliberation.

Runs before the council speaks. Translates the presenting complaint into
the question the council should actually be answering.

Doctors take history before diagnosing. Therapists find the real question
behind the stated one. This is that stage.

Returns a structured intake dict:
  presenting       — what the user said
  underlying       — what they might actually mean
  cognitive_pattern — which distortion is operating (if any)
  values_tension   — the two values pulling against each other
  differential     — 3 alternative framings of the question
"""

import sys
import json
from pathlib import Path
sys.path.insert(0, str(Path(__file__).parents[2]))
from llm_router import fast_call


INTAKE_SYSTEM = (
    "You are a clinical intake analyst for the Orlog reasoning council. "
    "Your job is to translate the presenting complaint into what the council should actually reason about. "
    "You are trained in both medical differential diagnosis and psychotherapeutic reframing. "
    "You do NOT give advice. You do NOT judge the person. "
    "You only illuminate the structure of the question so the council reasons about the right thing. "
    "\n\n"
    "COGNITIVE DISTORTIONS (name the most active one, or 'none detected'):\n"
    "catastrophizing, black-and-white thinking, mind reading, fortune telling, "
    "personalization, anchoring bias, sunk cost fallacy, availability bias, "
    "false dilemma, overgeneralization, emotional reasoning, should statements, "
    "confirmation bias, illusion of control, post-hoc rationalization\n\n"
    "Return ONLY valid JSON with these exact keys: "
    "presenting, underlying, cognitive_pattern, values_tension, differential (array of 3 strings). "
    "No preamble. No explanation outside the JSON."
)


def clinical_intake(
    question: str,
    context: dict,
    brain: str = "",
    client=None,
) -> dict:
    """
    Run clinical intake on the user's question.
    Returns structured dict or a minimal fallback if the call fails.
    """
    ctx = json.dumps(context, indent=2) if context else "{}"
    brain_section = f"REASONING CONTEXT:\n{brain}\n\n" if brain else ""

    user_prompt = (
        f"{brain_section}"
        f"PROJECT CONTEXT:\n{ctx}\n\n"
        f"PRESENTING QUESTION: {question}\n\n"
        f"Produce the clinical intake JSON."
    )

    try:
        raw = fast_call(INTAKE_SYSTEM, user_prompt, 500)
        # Strip markdown code fences if model wraps output
        if raw.startswith("```"):
            raw = raw.split("```")[1]
            if raw.startswith("json"):
                raw = raw[4:]
        return json.loads(raw.strip())

    except Exception as e:
        return {
            "presenting":        question,
            "underlying":        "",
            "cognitive_pattern": "",
            "values_tension":    "",
            "differential":      [],
            "_error":            str(e),
        }


def format_intake_for_agents(intake: dict) -> str:
    """
    Format the intake findings as a context block injected into every agent prompt.
    """
    if not intake or intake.get("_error"):
        return ""

    parts = ["CLINICAL INTAKE (read before deliberating):"]

    if intake.get("underlying"):
        parts.append(f"Presenting complaint: {intake['presenting']}")
        parts.append(f"Underlying question:  {intake['underlying']}")

    if intake.get("cognitive_pattern") and intake["cognitive_pattern"] != "none detected":
        parts.append(f"Cognitive pattern:    {intake['cognitive_pattern']}")

    if intake.get("values_tension"):
        parts.append(f"Values in tension:    {intake['values_tension']}")

    if intake.get("differential"):
        parts.append("Differential framings (the council should hold all three):")
        for i, framing in enumerate(intake["differential"], 1):
            parts.append(f"  {i}. {framing}")

    parts.append(
        "Reason about the UNDERLYING question, not just the presenting complaint. "
        "Name which differential framing your perspective most illuminates."
    )

    return "\n".join(parts)
