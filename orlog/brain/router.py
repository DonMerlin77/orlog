"""
orlog/brain/router.py — Lens + agent selector per task.

Reads goal + domain from GoalLayer → selects which lenses apply →
selects which Toy Box agents to assemble → logs every decision.

CRITICAL: Build logging before logic. Router decisions + outcome labels
are DPO training pairs for Phase 2. Every selection is logged with an
outcome slot so calibration happens automatically as outcomes arrive.

Current state: heuristic selection (keyword + domain map).
Target: ML-driven selection trained on logged outcome-labeled decisions.

KNOWN GAP: Router has no feedback loop yet. It selects but never learns
if it was wrong. Fix: record_outcome() closes the loop.

Usage:
    from orlog.brain.router import Router

    r = Router()
    selection = r.select(
        goal="I want to build a Shopify store for handmade jewelry",
        domain="store",
        task_type="strategy",
    )
    # selection = {id, lenses, agents, reasoning, ...}
    r.record_outcome(selection["id"], score=8.2, notes="good lens fit")
"""

import json
import uuid
from datetime import datetime
from pathlib import Path
from dotenv import load_dotenv

from orlog.brain.taxonomy import normalize as _normalize_task_type, is_valid_for_domain

load_dotenv(Path(__file__).parent.parent.parent / ".env")

ROUTER_LOG = Path("logs/router_decisions.json")

# ── Lens catalogue ────────────────────────────────────────────────────────────
# Source of truth: agents/intelligence/reasoning_brain.md
# Format: lens_id → {name, tags}

LENSES = {
    "loop_hunting":          {"name": "Loop Hunting",              "tags": ["all"]},
    "independence_test":     {"name": "Independence Test",         "tags": ["business", "store", "finance", "content"]},
    "bottleneck_scan":       {"name": "Bottleneck Scan",           "tags": ["all"]},
    "workforce_health":      {"name": "Workforce Health",          "tags": ["software", "hr", "manufacturing"]},
    "meta_questioning":      {"name": "Meta-Questioning",          "tags": ["all"]},
    "revenue_filter":        {"name": "Revenue Filter",            "tags": ["store", "finance", "content", "social_media"]},
    "deterministic_repair":  {"name": "Deterministic Repair First","tags": ["software", "hardware", "mlops", "cybersecurity"]},
    "crash_amnesia":         {"name": "Crash Amnesia Check",       "tags": ["software", "mlops", "cybersecurity", "hardware"]},
    "deliberation_gate":     {"name": "Deliberation Gate",         "tags": ["all"]},
    "telephone_problem":     {"name": "Telephone Problem",         "tags": ["software", "mlops", "research", "healthcare"]},
    "brain_pruning":         {"name": "Brain Pruning",             "tags": ["mlops", "research", "ai"]},
    "task_taxonomy":         {"name": "Task Taxonomy",             "tags": ["all"]},
    "council_reasoning":     {"name": "Individual vs Council",     "tags": ["all"]},
    "goal_layer":            {"name": "Goal Layer — End State",    "tags": ["all"]},
    "publish_early":         {"name": "Publish Early Principle",   "tags": ["software", "content", "store", "music", "game_dev"]},
}

# Domain → lens ids that fire for that domain
DOMAIN_LENS_MAP = {
    "store":            ["loop_hunting", "independence_test", "bottleneck_scan", "revenue_filter", "deliberation_gate", "goal_layer"],
    "game_dev":         ["loop_hunting", "bottleneck_scan", "meta_questioning", "deliberation_gate", "publish_early", "goal_layer"],
    "video_cgi":        ["loop_hunting", "bottleneck_scan", "meta_questioning", "deliberation_gate", "goal_layer"],
    "music":            ["loop_hunting", "independence_test", "meta_questioning", "publish_early", "goal_layer"],
    "software":         ["loop_hunting", "bottleneck_scan", "deterministic_repair", "crash_amnesia", "telephone_problem", "deliberation_gate", "publish_early", "goal_layer"],
    "hardware":         ["loop_hunting", "bottleneck_scan", "deterministic_repair", "crash_amnesia", "deliberation_gate", "goal_layer"],
    "content":          ["loop_hunting", "independence_test", "revenue_filter", "publish_early", "deliberation_gate", "goal_layer"],
    "creative_writing": ["loop_hunting", "meta_questioning", "publish_early", "deliberation_gate", "goal_layer"],
    "finance":          ["loop_hunting", "independence_test", "revenue_filter", "meta_questioning", "deliberation_gate", "goal_layer"],
    "real_estate":      ["loop_hunting", "independence_test", "revenue_filter", "meta_questioning", "deliberation_gate", "goal_layer"],
    "healthcare":       ["loop_hunting", "bottleneck_scan", "crash_amnesia", "telephone_problem", "deliberation_gate", "goal_layer"],
    "legal":            ["loop_hunting", "meta_questioning", "deliberation_gate", "goal_layer"],
    "manufacturing":    ["loop_hunting", "bottleneck_scan", "workforce_health", "deterministic_repair", "deliberation_gate", "goal_layer"],
    "education":        ["loop_hunting", "meta_questioning", "independence_test", "deliberation_gate", "goal_layer"],
    "research":         ["loop_hunting", "meta_questioning", "brain_pruning", "telephone_problem", "deliberation_gate", "goal_layer"],
    "social_media":     ["loop_hunting", "revenue_filter", "independence_test", "publish_early", "deliberation_gate", "goal_layer"],
    "events":           ["loop_hunting", "bottleneck_scan", "deliberation_gate", "goal_layer"],
    "nonprofit":        ["loop_hunting", "independence_test", "meta_questioning", "deliberation_gate", "goal_layer"],
    "cybersecurity":    ["loop_hunting", "crash_amnesia", "deterministic_repair", "meta_questioning", "deliberation_gate", "goal_layer"],
    "agtech":           ["loop_hunting", "bottleneck_scan", "meta_questioning", "deliberation_gate", "goal_layer"],
    "hr":               ["loop_hunting", "workforce_health", "meta_questioning", "deliberation_gate", "goal_layer"],
    "mlops":            ["loop_hunting", "brain_pruning", "crash_amnesia", "deterministic_repair", "telephone_problem", "deliberation_gate", "goal_layer"],
    "water_env":        ["loop_hunting", "bottleneck_scan", "meta_questioning", "deliberation_gate", "goal_layer"],
    "generic":          ["loop_hunting", "meta_questioning", "bottleneck_scan", "deliberation_gate", "goal_layer"],
}

# Task type → additional lenses that apply regardless of domain
TASK_LENS_MODIFIERS = {
    "strategy":     ["meta_questioning", "independence_test", "deliberation_gate"],
    "debug":        ["deterministic_repair", "crash_amnesia", "bottleneck_scan"],
    "launch":       ["revenue_filter", "independence_test", "publish_early"],
    "research":     ["meta_questioning", "brain_pruning", "loop_hunting"],
    "council":      ["council_reasoning", "task_taxonomy", "telephone_problem"],
    "copy":         ["revenue_filter", "loop_hunting"],
    "ops":          ["bottleneck_scan", "workforce_health", "deterministic_repair"],
    "build":        ["deterministic_repair", "bottleneck_scan", "publish_early"],
    "general":      [],
}


# ── Toy Box agent selection ───────────────────────────────────────────────────

def _select_agents(domain: str, task_type: str) -> list[str]:
    """Return agent names from the domain's AGENT_PACK."""
    try:
        import importlib
        from orlog.goal.goal_layer import DOMAIN_BINS
        bin_path = DOMAIN_BINS.get(domain, f"toybox/{domain}")
        module_name = "orlog." + bin_path.replace("/", ".")
        mod    = importlib.import_module(module_name)
        agents = getattr(mod, "AGENT_PACK", [])
        if task_type == "debug":
            return [a for a in agents if any(w in a.lower() for w in ["monitor", "audit", "sentinel", "check"])] or agents[:3]
        if task_type == "launch":
            return [a for a in agents if any(w in a.lower() for w in ["launch", "marketing", "publish"])] or agents[:3]
        return agents[:5]
    except Exception:
        return []


# ── Core selection ────────────────────────────────────────────────────────────

class Router:
    def __init__(self, log_path: str = None):
        self.log_path = Path(log_path or ROUTER_LOG)
        self.log_path.parent.mkdir(exist_ok=True)

    def _load_log(self) -> list:
        if self.log_path.exists():
            try:
                return json.loads(self.log_path.read_text())
            except Exception:
                pass
        return []

    def _save_log(self, entries: list):
        self.log_path.write_text(json.dumps(entries[-5000:], indent=2))

    def select(self, goal: str, domain: str, task_type: str = "general") -> dict:
        """
        Select lenses and agents for a given goal + domain + task type.
        Logs the decision immediately — outcome recorded later via record_outcome().
        """
        domain    = domain.lower().strip()

        # Normalize task_type against taxonomy — remap aliases, catch unknowns
        canonical_type, was_remapped = _normalize_task_type(task_type, domain)
        domain_valid = is_valid_for_domain(canonical_type, domain)

        domain_lenses   = DOMAIN_LENS_MAP.get(domain, DOMAIN_LENS_MAP["generic"])
        task_modifiers  = TASK_LENS_MODIFIERS.get(canonical_type, [])

        selected_ids    = list(dict.fromkeys(domain_lenses + task_modifiers))
        selected_lenses = [
            {"id": lid, "name": LENSES[lid]["name"]}
            for lid in selected_ids
            if lid in LENSES
        ]
        selected_agents = _select_agents(domain, canonical_type)

        remap_note = f" (remapped from '{task_type}')" if was_remapped else ""
        domain_note = f" [not canonical for domain '{domain}']" if not domain_valid else ""

        decision = {
            "id":              str(uuid.uuid4())[:8],
            "timestamp":       datetime.now().isoformat(),
            "goal":            goal[:200],
            "domain":          domain,
            "task_type":       canonical_type,
            "task_type_raw":   task_type,
            "lenses_selected": selected_lenses,
            "agents_selected": selected_agents,
            "n_lenses":        len(selected_lenses),
            "reasoning":       f"Domain={domain} → {len(domain_lenses)} base lenses; task={canonical_type}{remap_note}{domain_note} → +{len(task_modifiers)} modifiers.",
            "outcome_score":   None,
            "outcome_notes":   None,
            "dpo_usable":      False,
        }

        log = self._load_log()
        log.append(decision)
        self._save_log(log)

        return decision

    def record_outcome(self, decision_id: str, score: float, notes: str = ""):
        """
        Label a prior routing decision with its actual Orlog score.
        Once labeled, dpo_usable=True — this entry is a valid DPO training pair.
        """
        log = self._load_log()
        for entry in log:
            if entry.get("id") == decision_id:
                entry["outcome_score"]  = score
                entry["outcome_notes"]  = notes
                entry["dpo_usable"]     = True
                entry["labeled_at"]     = datetime.now().isoformat()
                break
        self._save_log(log)

    def dpo_pairs(self, min_score: float = 7.0) -> list[dict]:
        """
        Return labeled decisions suitable as DPO training pairs.
        Pairs: same goal+domain, higher score = winner, lower = loser.
        """
        log     = self._load_log()
        labeled = [e for e in log if e.get("dpo_usable") and e.get("outcome_score") is not None]

        pairs = []
        seen  = {}
        for entry in labeled:
            key = (entry["domain"], entry["task_type"])
            if key not in seen:
                seen[key] = []
            seen[key].append(entry)

        for key, entries in seen.items():
            entries.sort(key=lambda x: x["outcome_score"], reverse=True)
            for i in range(len(entries) - 1):
                winner = entries[i]
                loser  = entries[i + 1]
                if winner["outcome_score"] - loser["outcome_score"] >= 1.0:
                    pairs.append({
                        "domain":           key[0],
                        "task_type":        key[1],
                        "winner_id":        winner["id"],
                        "winner_lenses":    [l["id"] for l in winner["lenses_selected"]],
                        "winner_score":     winner["outcome_score"],
                        "loser_id":         loser["id"],
                        "loser_lenses":     [l["id"] for l in loser["lenses_selected"]],
                        "loser_score":      loser["outcome_score"],
                    })

        return pairs

    def stats(self) -> dict:
        log     = self._load_log()
        labeled = [e for e in log if e.get("outcome_score") is not None]
        by_domain = {}
        for e in labeled:
            d = e["domain"]
            if d not in by_domain:
                by_domain[d] = []
            by_domain[d].append(e["outcome_score"])

        return {
            "total_decisions":  len(log),
            "labeled":          len(labeled),
            "dpo_pairs":        len(self.dpo_pairs()),
            "by_domain":        {d: {"n": len(scores), "avg": round(sum(scores)/len(scores), 2)} for d, scores in by_domain.items()},
        }
