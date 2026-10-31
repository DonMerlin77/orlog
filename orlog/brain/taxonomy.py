"""
orlog/brain/taxonomy.py — Canonical task type definitions.

Single source of truth for all valid task_type values in Orlog.
Router validates and normalizes against this before selecting lenses.

To add a task type:
  1. Add it here with description + domains + aliases
  2. Add it to TASK_LENS_MODIFIERS in router.py
"""

# ── Canonical task types ──────────────────────────────────────────────────────

TASK_TYPES = {
    "strategy": {
        "description": "High-level direction, prioritization, roadmap decisions",
        "domains":     "all",
        "aliases":     ["plan", "planning", "roadmap", "direction", "prioritize"],
    },
    "debug": {
        "description": "Diagnose failures, trace root causes, fix broken systems",
        "domains":     ["software", "hardware", "mlops", "cybersecurity", "manufacturing", "healthcare"],
        "aliases":     ["fix", "diagnose", "troubleshoot", "repair", "broken"],
    },
    "launch": {
        "description": "Go-to-market, release, publish, ship a product or feature",
        "domains":     "all",
        "aliases":     ["ship", "release", "publish", "go-to-market", "gtm", "deploy"],
    },
    "research": {
        "description": "Investigate unknowns, survey options, synthesize findings",
        "domains":     "all",
        "aliases":     ["investigate", "survey", "explore", "analyze", "study"],
    },
    "copy": {
        "description": "Write or rewrite persuasive text — ads, listings, emails, landing pages",
        "domains":     ["store", "content", "social_media", "creative_writing", "music", "events"],
        "aliases":     ["write", "writing", "content", "copywrite", "copywriting", "email", "ad"],
    },
    "ops": {
        "description": "Day-to-day operations, process optimization, system maintenance",
        "domains":     "all",
        "aliases":     ["operations", "process", "workflow", "maintenance", "automate", "automation"],
    },
    "build": {
        "description": "Implement a specific feature, component, or system",
        "domains":     ["software", "hardware", "game_dev", "mlops", "manufacturing"],
        "aliases":     ["implement", "code", "develop", "create", "make"],
    },
    "council": {
        "description": "Meta-deliberation — evaluate Orlog itself, audit past decisions",
        "domains":     "all",
        "aliases":     ["meta", "audit", "evaluate", "review", "assess"],
    },
    "general": {
        "description": "Catch-all — use when no other type fits",
        "domains":     "all",
        "aliases":     [],
    },
}

# ── Alias index (built once at import) ───────────────────────────────────────

_ALIAS_INDEX: dict[str, str] = {}
for _type, _meta in TASK_TYPES.items():
    _ALIAS_INDEX[_type] = _type
    for _alias in _meta["aliases"]:
        _ALIAS_INDEX[_alias.lower()] = _type


def normalize(task_type: str, domain: str = None) -> tuple[str, bool]:
    """
    Normalize a task_type string to a canonical TASK_TYPES key.

    Returns (canonical_type, was_remapped).
    If the type is unknown and can't be aliased, returns ("general", True).
    If the type is valid but not recommended for the domain, still returns it
    (caller decides whether to warn).
    """
    raw = task_type.lower().strip()
    canonical = _ALIAS_INDEX.get(raw)
    if canonical:
        return canonical, (canonical != raw)
    return "general", True


def is_valid_for_domain(task_type: str, domain: str) -> bool:
    """Return True if task_type is applicable to the given domain."""
    meta = TASK_TYPES.get(task_type)
    if not meta:
        return False
    domains = meta["domains"]
    return domains == "all" or domain in domains


def describe(task_type: str) -> str:
    """Return human-readable description of a task type."""
    meta = TASK_TYPES.get(task_type)
    if not meta:
        return f"Unknown task type: {task_type!r}"
    domains = meta["domains"] if meta["domains"] != "all" else "all domains"
    return f"{task_type}: {meta['description']} (domains: {domains})"


def all_types() -> list[str]:
    return list(TASK_TYPES.keys())


def valid_for_domain(domain: str) -> list[str]:
    """Return all task types valid for a given domain."""
    return [t for t in TASK_TYPES if is_valid_for_domain(t, domain)]
