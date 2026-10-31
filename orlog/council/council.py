"""
orlog/council/council.py — The Round Table.

Five cognitive style agents deliberate on any question, then a Synthesizer
resolves their disagreements. The insight lives in the tension, not the consensus.

Usage:
    from orlog.council.council import RoundTable
    rt = RoundTable(brain_path="agents/intelligence/reasoning_brain_lite.md")
    result = rt.deliberate(
        question="Should we launch the hardware product now or wait for the v2 prototype?",
        context={"goal": "ship H2O car by Q4", "domain": "hardware"}
    )
"""

import os
import sys
import json
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import datetime, timezone
from pathlib import Path

from dotenv import load_dotenv

sys.path.insert(0, str(Path(__file__).parents[2]))
from llm_router import fast_call, _try_openrouter_one

# ORLOG_MODEL_VOICES=1 (2026-09-30, Tom's idea): each council voice speaks through a DIFFERENT model family on
# OpenRouter, so the council has real epistemic diversity instead of one model playing ten characters. Non-thinking
# models only (gemini-2.5-flash's hidden thinking ate the 400-token budget and truncated voices to one line).
# Off by default; falls back to fast_call if a model fails.
VOICE_MODELS = [
    "meta-llama/llama-4-maverick", "deepseek/deepseek-v3.2", "mistralai/mistral-large-2512",
    "qwen/qwen3-235b-a22b-2507", "google/gemma-4-31b-it", "moonshotai/kimi-k2-0905",
    "cohere/command-a-plus", "meta-llama/llama-3.3-70b-instruct", "microsoft/phi-4",
    "google/gemini-2.5-flash-lite",
]
BACKUP_MODEL = "mistralai/mistral-medium-3.1"   # one retry before the free stack (parallel calls time out sometimes)
SYNTH_MODEL = "mistralai/mistral-large-2512"

from orlog.brain.state import BrainState
from orlog.council.synesthesia import synesthesia_perspective
from orlog.council.socratic import socratic_question
from orlog.council.intake import clinical_intake, format_intake_for_agents
from orlog.council.styles import ALL_STYLES, select_styles, CognitiveStyle

load_dotenv(Path(__file__).parent.parent.parent / ".env")

# Legacy dict kept for backwards compatibility with anything reading COGNITIVE_STYLES directly
COGNITIVE_STYLES = {
    # ── Original five — threat/pattern detection axes ──────────────────────────
    "adhd": (
        "Before analyzing the obvious, scan for non-obvious connections first. "
        "Move fast, make associations across unrelated domains, flag unexpected patterns. "
        "Prefer the action that unblocks the most things downstream — the non-obvious move is often right."
    ),
    "autism": (
        "Map every edge case before concluding. "
        "Resist any emerging consensus — your job is to find what everyone else agrees to ignore. "
        "Be precise, consistent, and unmoved by what other agents think."
    ),
    "ocd": (
        "Verify every assumption systematically before accepting it. "
        "Your job is to find what is missing or unverified, not to confirm what looks right. "
        "Nothing proceeds until every check is complete."
    ),
    "dyslexia": (
        "See the shape of the problem before the details. "
        "What does the overall structure look like? Where are the spatial or systemic relationships wrong? "
        "Big picture and long-term pattern first — details are secondary."
    ),
    "paranoid": (
        "Assume adversarial conditions. What could go wrong? Who benefits if this fails? "
        "Model bad intent and failure modes before assuming good intent. "
        "Find the attack vectors, the hidden costs, the things no one wants to say out loud."
    ),

    # ── Extended council — missing axes the original five cannot see ───────────
    "empath": (
        "Speak for the humans affected by this decision — not the system, not the metrics. "
        "What does someone who cares deeply about this situation actually feel and need? "
        "Some things cannot be optimized or predicted: love, grief, loyalty, belonging. "
        "Name the human cost of the 'correct' answer. What is lost if we treat this as purely rational? "
        "The question love asks is not 'what is efficient?' but 'what is worth it?'"
    ),
    "contrarian": (
        "The emerging consensus in this council is probably wrong — or at least incomplete. "
        "Your job is to steelman the exact opposite position. "
        "Not adversarially (that is paranoid's role) — but intellectually: what would a brilliant person "
        "who holds the opposite view say? What evidence supports the contrary case? "
        "Consensus is the enemy of discovery. Disagreement is the signal. Find it."
    ),
    "frugal": (
        "Assume you have no slack — no budget, no runway, no margin for error, no team. "
        "What is the minimum viable correct action that a resource-constrained actor would take? "
        "Strip out everything non-essential. What actually has to happen for this to work at the smallest scale? "
        "The expensive solution is almost never the only solution. Find the cheap path that most people miss."
    ),
}

CRITIC_PROMPT = (
    "You are a synthesis critic. Read the question and the first-pass synthesis below. "
    "Your job: find the single most important flaw in the synthesis — hedging, missing tension, "
    "a blind spot that wasn't actually addressed, or a verdict that could apply to any situation. "
    "Be specific. One paragraph. If the synthesis is genuinely strong, say so and why."
)

SYNTHESIZER_PROMPT = (
    "You have just read multiple different cognitive perspectives on the same question. "
    "Your job is to resolve their disagreements — not average them, not pick the loudest. "
    "The insight lives in the tension between positions. "
    "Find what each perspective saw that the others missed, then synthesize a verdict "
    "that is smarter than any single perspective could produce alone."
)


class RoundTable:
    def __init__(self, brain_path: str = None, api_key: str = None):
        self.brain  = self._load_brain(brain_path)
        self.log_path = Path(__file__).parent.parent.parent / "logs" / "orlog_council.json"

    def _load_brain(self, path: str = None) -> str:
        candidates = [
            path,
            "agents/intelligence/reasoning_brain_lite.md",
            "agents/intelligence/reasoning_brain_whisper.md",
        ]
        layers = []
        for p in candidates:
            if p and Path(p).exists():
                layers.append(Path(p).read_text())
                break  # use first matching core brain

        # Always append systems science layer if present
        systems_path = Path("agents/intelligence/reasoning_brain_systems.md")
        if systems_path.exists():
            layers.append(systems_path.read_text())

        return "\n\n---\n\n".join(layers) if layers else ""

    def _agent_prompt(self, style: "CognitiveStyle", question: str, context: dict, state: BrainState = None, intake_block: str = "") -> str:
        ctx = json.dumps(context, indent=2) if context else "{}"
        brain_section = f"\n\n{self.brain}\n\n" if self.brain else ""
        full_modifier = style.build_prompt(question, context, state)
        active_facets = style.active_facets(question, context)
        facet_note = f" [{len(active_facets)} facet(s) active]" if active_facets else ""
        intake_section = f"\n\n{intake_block}\n" if intake_block else ""
        return (
            f"{brain_section}"
            f"COGNITIVE STYLE: {style.name.upper()}{facet_note}\n"
            f"{full_modifier}\n\n"
            f"PROJECT CONTEXT:\n{ctx}\n"
            f"{intake_section}\n"
            f"QUESTION: {question}\n\n"
            f"Give your perspective in 3-5 sentences. Be specific to your cognitive style — "
            f"don't just give a balanced answer, bring your actual angle."
        )

    def _call(self, system: str, user: str, max_tokens: int = 400, agent_tag: str = "orlog_council",
              model: str = None) -> str:
        try:
            if model and os.getenv("ORLOG_MODEL_VOICES"):
                for m in (model, BACKUP_MODEL):
                    out = _try_openrouter_one(system, user, max_tokens, m, temperature=0.7)
                    if out:
                        self.voice_models[agent_tag] = m
                        return out
            return fast_call(system, user, max_tokens)
        except Exception as e:
            return f"[error: {e}]"

    def deliberate(self, question: str, context: dict = None, max_voices: int = None) -> dict:
        context = context or {}
        perspectives = {}

        state = BrainState.detect(context)

        # Clinical intake — runs before the council, translates presenting → underlying
        intake = clinical_intake(
            question=question,
            context=context,
            brain=self.brain,
        )
        intake_block = format_intake_for_agents(intake)

        # Wheel selection: score all voices for relevance, pick top N (or all if no limit)
        active_styles = select_styles(question, context, max_voices=max_voices)
        self.voice_models = {}

        def _voice_call(style, i):
            prompt = self._agent_prompt(style, question, context, state, intake_block)
            return style.name, self._call(
                system=f"You are a {style.name.upper()} cognitive style agent in the Orlog Round Table.",
                user=prompt, agent_tag=style.name, model=VOICE_MODELS[i % len(VOICE_MODELS)],
            )

        with ThreadPoolExecutor(max_workers=min(len(active_styles), 12)) as pool:
            for name, text in pool.map(_voice_call, active_styles, range(len(active_styles))):
                perspectives[name] = text

        # Synesthesia reads all council perspectives — cross-domain translation before synthesis
        perspectives["synesthesia"] = synesthesia_perspective(
            question=question,
            context=context,
            perspectives=perspectives,
            brain=self.brain,
        )

        # Socratic agent — fires after all voices, before synthesis
        # Returns ONE foundational question the council failed to ask
        socratic_q = socratic_question(
            question=question,
            context=context,
            perspectives=perspectives,
            brain=self.brain,
        )

        n_perspectives = len(perspectives)
        perspectives_text = "\n\n".join(
            f"[{name.upper()} PERSPECTIVE]\n{text}"
            for name, text in perspectives.items()
        )

        _CONJUGATE_PAIRS = [
            ("paranoid", "visionary"),
            ("adhd", "deep_work"),
            ("autism", "gestalt"),
            ("ocd", "intuition"),
            ("empath", "utilitarian"),
            ("contrarian", "advocate"),
            ("frugal", "expansive"),
            ("dyslexia", "engineer"),
        ]
        active_names = set(s.name for s in active_styles)
        missing_voices = []
        for a, b in _CONJUGATE_PAIRS:
            if a not in active_names and b not in active_names:
                missing_voices.append(f"{a}/{b} (both absent)")
            elif a not in active_names:
                missing_voices.append(f"{a} (antiparticle of {b})")
            elif b not in active_names:
                missing_voices.append(f"{b} (antiparticle of {a})")
        absent_note = (
            f"Absent lenses this council: {', '.join(missing_voices)}. "
            f"These axes have no representation — name what each absent voice would have said."
            if missing_voices else "All conjugate pairs are represented."
        )

        synthesis_prompt = (
            f"QUESTION: {question}\n\n"
            f"PROJECT CONTEXT: {json.dumps(context)}\n\n"
            f"BRAIN STATE: {state.name} ({state.description}). "
            f"Amplified styles: {state.summary()['amplified'] or 'none'}. "
            f"Dampened styles: {state.summary()['dampened'] or 'none'}. "
            f"Weight these perspectives accordingly.\n\n"
            f"{n_perspectives} PERSPECTIVES:\n{perspectives_text}\n\n"
            f"SOCRATIC QUESTION (you must address this in your synthesis — "
            f"it exposes what the council's perspectives all missed):\n{socratic_q}\n\n"
            f"Synthesize a verdict: what should actually be done, and why?\n"
            f"Use these section headers exactly:\n"
            f"ᛏ VERDICT: (one sentence ruling)\n"
            f"ᚾ ACTION: (2-3 concrete steps — what to do today or this week, in plain language a non-expert can follow. No jargon. Start each step with a verb.)\n"
            f"ᛚ REASONING: (2-3 sentences of evidence and logic)\n"
            f"ᚦ KEY TENSION: (what the perspectives disagreed most sharply on)\n"
            f"ᛈ BLIND SPOT: ({absent_note})\n"
            f"ᚲ SOCRATIC: (how answering '{socratic_q}' would change or confirm this verdict)\n"
        )

        # First-pass synthesis
        synthesis_v1 = self._call(system=SYNTHESIZER_PROMPT, user=synthesis_prompt, max_tokens=2000,
                                  agent_tag="synthesis", model=SYNTH_MODEL)

        # Inline critique — find the single biggest flaw in v1
        critique = self._call(
            system=CRITIC_PROMPT,
            user=f"QUESTION: {question}\n\nFIRST-PASS SYNTHESIS:\n{synthesis_v1}",
            max_tokens=300, agent_tag="critic", model="deepseek/deepseek-v3.2",
        )

        # Regenerate with critique context if the critic found a real issue
        is_strong = any(phrase in critique.lower() for phrase in ["genuinely strong", "no major flaw", "well-constructed", "solid synthesis"])
        if not is_strong:
            synthesis = self._call(
                system=SYNTHESIZER_PROMPT,
                user=(
                    synthesis_prompt +
                    f"\n\nCRITIC FEEDBACK ON YOUR FIRST DRAFT:\n{critique}\n\n"
                    f"Revise your synthesis to address this critique. Keep what was good. Fix what wasn't."
                ),
                max_tokens=2000, agent_tag="synthesis_revised", model=SYNTH_MODEL,
            )
        else:
            synthesis = synthesis_v1

        result = {
            "question":        question,
            "context":         context,
            "intake":          intake,
            "perspectives":    perspectives,
            "voice_models":    dict(getattr(self, "voice_models", {})),
            "socratic":        socratic_q,
            "synthesis":       synthesis,
            "critique":        critique,
            "revised":         not is_strong,
            "state":           state.summary(),
            "timestamp":       datetime.now(timezone.utc).isoformat(),
        }

        self._log(result)
        return result

    def _log(self, result: dict):
        self.log_path.parent.mkdir(exist_ok=True)
        existing = []
        if self.log_path.exists():
            try:
                existing = json.loads(self.log_path.read_text())
            except Exception:
                existing = []
        existing.append(result)
        existing = existing[-100:]
        self.log_path.write_text(json.dumps(existing, indent=2))

    def print_result(self, result: dict):
        print(f"\n{'='*60}")
        print(f"  Round Table — {result['timestamp'][:19]}")
        print(f"{'='*60}")
        print(f"\nQUESTION: {result['question']}\n")
        for style, text in result["perspectives"].items():
            print(f"[{style.upper()}]")
            print(f"  {text[:200]}...")
            print()
        if result.get("socratic"):
            print(f"[SOCRATIC QUESTION]")
            print(f"  {result['socratic']}")
            print()
        print(f"[SYNTHESIS]")
        print(f"  {result['synthesis']}")
        print()
