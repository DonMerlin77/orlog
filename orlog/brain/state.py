"""
orlog/brain/state.py — Lifecycle state layer.

Modulates council cognitive style weights based on the current operational
state of the system or the user. Each state amplifies some styles and
dampens others — changing which angles get pushed hardest.

Five states mapped to neurochemical dominants:
  focus      → dopamine   : precision work, deep execution
  stress     → cortisol   : threat detection, risk-first thinking
  stable     → serotonin  : balanced deliberation, no bias
  reflective → melatonin  : pattern recognition, long-arc thinking
  flow       → norepinephrine : fast synthesis, high-signal connections

Usage:
    from orlog.brain.state import BrainState

    state = BrainState.detect(context)          # auto-detect from context signals
    state = BrainState("focus")                 # explicit
    modifier = state.modifier_for("adhd")       # inject into agent prompt
    summary  = state.summary()                  # log-friendly dict
"""

from dataclasses import dataclass
from typing import Optional


# ── State definitions ─────────────────────────────────────────────────────────
# amplify: these styles push harder (more confident, more forceful)
# dampen:  these styles pull back (more qualified, more deferential)
# neutral: all other styles — unchanged

STATE_MAP = {
    "focus": {
        "dominant":    "dopamine",
        "hormone_map": {
            # Dopamine: reward-anticipation, precision execution, deep work
            "amplify_voices": ["autism", "ocd", "deep_work"],
            "dampen_voices":  ["adhd", "dyslexia", "gestalt"],
        },
        "description": "Deep execution mode — precision and verification dominate",
        "amplify":   ["autism", "ocd", "deep_work"],
        "dampen":    ["adhd", "dyslexia", "gestalt"],
        "signals":   ["build", "debug", "implement", "precise", "exact", "specification"],
    },
    "stress": {
        "dominant":    "cortisol",
        "hormone_map": {
            # Cortisol: threat amplification, worst-case modeling, urgency
            "amplify_voices": ["paranoid", "ocd", "utilitarian"],
            "dampen_voices":  ["adhd", "visionary", "expansive"],
        },
        "description": "Threat-first mode — risk detection and exits are prioritized",
        "amplify":   ["paranoid", "ocd", "utilitarian"],
        "dampen":    ["adhd", "visionary", "expansive"],
        "signals":   ["deadline", "urgent", "broken", "failing", "crisis", "blocked", "critical"],
    },
    "stable": {
        "dominant":    "serotonin",
        "hormone_map": {
            # Serotonin: balanced, no strong push in any direction
            "amplify_voices": ["advocate"],
            "dampen_voices":  [],
        },
        "description": "Balanced deliberation — all styles equally weighted",
        "amplify":   [],
        "dampen":    [],
        "signals":   [],
    },
    "reflective": {
        "dominant":    "melatonin",
        "hormone_map": {
            # Melatonin: consolidation, long-arc pattern recognition, slow synthesis
            "amplify_voices": ["gestalt", "dyslexia", "deep_work"],
            "dampen_voices":  ["ocd", "paranoid", "engineer"],
        },
        "description": "Long-arc mode — pattern recognition and big picture dominate",
        "amplify":   ["gestalt", "dyslexia", "deep_work"],
        "dampen":    ["ocd", "paranoid", "engineer"],
        "signals":   ["review", "retrospective", "what did we learn", "long term", "vision", "pattern"],
    },
    "flow": {
        "dominant":    "norepinephrine",
        "hormone_map": {
            # Norepinephrine: narrow focus beam, fast signal processing, high-alert synthesis
            "amplify_voices": ["adhd", "intuition", "visionary"],
            "dampen_voices":  ["paranoid", "frugal", "engineer"],
        },
        "description": "High-signal synthesis — fast connections, low friction",
        "amplify":   ["adhd", "dyslexia", "intuition"],
        "dampen":    ["paranoid"],
        "signals":   ["brainstorm", "explore", "what if", "creative", "generate", "ideate"],
    },
    "bonding": {
        "dominant":    "oxytocin",
        "hormone_map": {
            # Oxytocin: relational weight, trust, long-term relationship thinking
            "amplify_voices": ["empath", "advocate", "contrarian"],
            "dampen_voices":  ["utilitarian", "frugal", "paranoid"],
        },
        "description": "Relational mode — human cost and trust are foregrounded",
        "amplify":   ["empath", "advocate"],
        "dampen":    ["utilitarian", "paranoid"],
        "signals":   ["team", "partner", "relationship", "trust", "culture", "people", "hire", "co-founder"],
    },
    "risk_on": {
        "dominant":    "testosterone",
        "hormone_map": {
            # Testosterone: dominance, bet-sizing, conviction, bias toward action
            "amplify_voices": ["expansive", "visionary", "contrarian"],
            "dampen_voices":  ["frugal", "ocd", "empath"],
        },
        "description": "High-conviction mode — bias toward bold action and large bets",
        "amplify":   ["expansive", "visionary", "contrarian"],
        "dampen":    ["frugal", "ocd"],
        "signals":   ["bet", "invest", "all-in", "conviction", "bold", "aggressive", "double down", "raise"],
    },
    "learning": {
        "dominant":    "acetylcholine",
        "hormone_map": {
            # Acetylcholine: memory encoding, pattern-locking, precise recall, systematic mapping
            "amplify_voices": ["autism", "ocd", "engineer"],
            "dampen_voices":  ["adhd", "intuition", "gestalt"],
        },
        "description": "Learning mode — systematic mapping and precise encoding dominate",
        "amplify":   ["autism", "engineer"],
        "dampen":    ["adhd", "intuition"],
        "signals":   ["learn", "understand", "study", "how does", "explain", "teach", "course", "training"],
    },
}

AMPLIFY_MODIFIER = (
    "\n\n[STATE MODIFIER — AMPLIFIED] Your perspective carries extra weight in this session. "
    "Push harder on your instincts. Be more forceful, less qualified. "
    "If you see something, say it directly — don't hedge."
)

DAMPEN_MODIFIER = (
    "\n\n[STATE MODIFIER — TEMPERED] Your perspective is tempered in this session. "
    "Lead with listening. Qualify your claims more. "
    "Yield to stronger signals from other angles before asserting your view."
)


@dataclass
class BrainState:
    name: str

    def __post_init__(self):
        if self.name not in STATE_MAP:
            self.name = "stable"
        self._meta = STATE_MAP[self.name]

    @classmethod
    def detect(cls, context: dict) -> "BrainState":
        """
        Auto-detect state from context signals.
        Checks question text, task_type, and any explicit 'state' key.
        Falls back to 'stable'.
        """
        if not context:
            return cls("stable")

        # Explicit override always wins
        if "state" in context:
            return cls(context["state"])

        # Scan question + task_type for signal words — score all states, pick strongest
        text = " ".join([
            str(context.get("question", "")),
            str(context.get("task_type", "")),
            str(context.get("goal", "")),
        ]).lower()

        scores = {}
        for state_name, meta in STATE_MAP.items():
            if state_name == "stable":
                continue
            scores[state_name] = sum(1 for sig in meta["signals"] if sig in text)

        best = max(scores, key=scores.get)
        if scores[best] > 0:
            return cls(best)

        return cls("stable")

    def modifier_for(self, style: str) -> str:
        """Return the prompt modifier for a given cognitive style in this state."""
        if style in self._meta["amplify"]:
            return AMPLIFY_MODIFIER
        if style in self._meta["dampen"]:
            return DAMPEN_MODIFIER
        return ""

    @property
    def dominant(self) -> str:
        return self._meta["dominant"]

    @property
    def description(self) -> str:
        return self._meta["description"]

    def summary(self) -> dict:
        return {
            "state":       self.name,
            "dominant":    self.dominant,
            "description": self.description,
            "amplified":   self._meta["amplify"],
            "dampened":    self._meta["dampen"],
        }


def all_states() -> list[str]:
    return list(STATE_MAP.keys())
