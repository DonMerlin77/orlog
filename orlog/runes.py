"""
orlog/runes.py — Canonical rune vocabulary for Orlog structured output.

Provides constants and a formatter for the 8-rune vocab defined in RUNE_VOCAB.md.
Import this wherever you want compact, scannable structured output.
"""

# Core 13 runes — meaning fixed, see RUNE_VOCAB.md
PRIORITY   = "ᚠ"   # Fehu     — value / priority rank
EFFORT     = "ᚢ"   # Uruz     — effort / weight
RISK       = "ᚦ"   # Thurisaz — thorn / obstacle / danger
RESEARCH   = "ᚲ"   # Kenaz    — torch / illuminate / investigate first
DEPENDS    = "ᚷ"   # Gebo     — dependency / prerequisite
FROZEN     = "ᛁ"   # Isa      — ice / deliberately paused
CYCLE      = "ᛃ"   # Jera     — year / recurring / scheduled loop
UNKNOWN    = "ᛈ"   # Pertho   — fate / uncertain / outcome unclear
UNBLOCKS   = "ᛉ"   # Algiz    — what this unlocks downstream
VERDICT    = "ᛏ"   # Tiwaz    — ruling / decision
BLOCKED    = "ᚾ"   # Nauthiz  — hard blocked by specific missing resource
ACTIVE     = "ᛚ"   # Laguz    — active / in progress / live
FOUNDATION = "ᛟ"   # Othala   — done / load-bearing / base

# Round 2 additions — Anglo-Saxon Futhorc (all standardized Unicode)
HUMAN       = "ᛗ"   # Mannaz  — human/user-facing; involves a person's decision
OUTPUT      = "ᚩ"   # Os      — output/signal; produces visible external result
DEADLINE    = "ᛞ"   # Dagaz   — time-bound; hard external date; threshold/inflection
IRREVERSIBLE= "ᛠ"   # Ear     — cannot be undone (deploy, delete, force-push)

# Status tags
DONE        = f"{FOUNDATION} DONE"
LIVE        = f"{ACTIVE} LIVE"
HOLD        = f"{BLOCKED} HOLD"
ICE         = f"{FROZEN} FROZEN"
LOOP        = f"{CYCLE} CYCLE"
PROBE       = f"{RESEARCH} PROBE"
GO          = f"{VERDICT} GO"
WAIT        = f"{VERDICT} WAIT"
DROP        = f"{VERDICT} DROP"
HUMAN_CHECK = f"{HUMAN} HUMAN"
SIGNAL      = f"{OUTPUT} SIGNAL"
DUE         = f"{DEADLINE} DUE"
PERMANENT   = f"{IRREVERSIBLE} PERMANENT"


def item(priority: int, effort: str, depends: str, unblocks: str, verdict: str) -> str:
    """Format a structured queue item in compact rune notation."""
    dep = depends if depends else "none"
    unb = unblocks if unblocks else "nothing identified"
    return (
        f"{PRIORITY} {priority}  {EFFORT} {effort}  {DEPENDS} {dep}  {UNBLOCKS} {unb}\n"
        f"{VERDICT} {verdict}"
    )


def blocked_item(blocked_by: str, priority: int, effort: str, depends: str, unblocks: str, verdict: str) -> str:
    """Format a blocked item."""
    dep = depends if depends else "none"
    unb = unblocks if unblocks else "nothing identified"
    return (
        f"{HOLD} — needs: {blocked_by}\n"
        f"{PRIORITY} {priority}  {EFFORT} {effort}  {DEPENDS} {dep}  {UNBLOCKS} {unb}\n"
        f"{VERDICT} {verdict}"
    )


def status(label: str, rune: str = ACTIVE) -> str:
    """Quick status tag: status('orlog-web') → 'ᛚ LIVE — orlog-web'"""
    return f"{rune} — {label}"
