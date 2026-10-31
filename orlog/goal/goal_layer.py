"""
GoalLayer — First thing Orlog runs. Defines or elicits the project end state.
No domain context loaded until the goal is clear.
"""
import os
import sys
import json
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parents[2]))
from llm_router import fast_call


DOMAIN_BINS = {
    "store":            "toybox/stores",
    "game_dev":         "toybox/game_dev",
    "video_cgi":        "toybox/video_cgi",
    "music":            "toybox/music",
    "software":         "toybox/software",
    "hardware":         "toybox/hardware",
    "content":          "toybox/content",
    "creative_writing": "toybox/creative_writing",
    "finance":          "toybox/finance",
    "real_estate":      "toybox/real_estate",
    "healthcare":       "toybox/healthcare",
    "legal":            "toybox/legal",
    "manufacturing":    "toybox/manufacturing",
    "education":        "toybox/education",
    "research":         "toybox/research",
    "social_media":     "toybox/social_media",
    "events":           "toybox/events",
    "nonprofit":        "toybox/nonprofit",
    "cybersecurity":    "toybox/cybersecurity",
    "agtech":           "toybox/agtech",
    "hr":               "toybox/hr",
    "mlops":            "toybox/mlops",
    "water_env":        "toybox/water_env",
    "generic":          "toybox/generic",
}

CLARIFYING_QUESTIONS = {
    "store": [
        "What products are you selling, and are they physical or digital?",
        "Are you dropshipping, holding inventory, or doing print-on-demand?",
        "What platform — Shopify, Etsy, Amazon, or your own site?",
    ],
    "game_dev": [
        "What genre and perspective — first-person shooter, top-down RPG, puzzle platformer?",
        "Single-player, co-op, or multiplayer? Any target platform (PC, mobile, console)?",
        "Are you building the engine from scratch, or using Unity/Unreal/Godot?",
    ],
    "video_cgi": [
        "Live action, CGI, or a mix? Short-form (social) or long-form (film/series)?",
        "What's the distribution target — YouTube, TikTok, streaming, or client delivery?",
        "Do you need AI generation, motion capture, or traditional animation?",
    ],
    "music": [
        "Are you creating original music, teaching, or building a music app?",
        "What genre and instruments are central to the project?",
        "Do you need distribution, licensing, or just production tools?",
    ],
    "software": [
        "Is this a web app, mobile app, desktop tool, or API/backend service?",
        "Who is the end user — consumers, businesses, or developers?",
        "What's the core action the user takes — what does 'done' look like for them?",
    ],
    "hardware": [
        "What is the physical product — vehicle, device, component, or system?",
        "Is this a prototype, a production run, or an existing product you're improving?",
        "What energy source or key technology is at the core (electric, hydrogen, combustion, embedded systems)?",
    ],
    "content": [
        "What format — blog, newsletter, podcast, social media, or YouTube?",
        "Who is the audience and what topic or niche are you covering?",
        "Is the goal to build an audience, generate leads, or drive direct revenue?",
    ],
    "finance": [
        "Are you tracking personal finances, managing investments, or building a fintech product?",
        "What asset classes are involved — stocks, crypto, real estate, options?",
        "Do you need automated trading, reporting, or alerts?",
    ],
    "education": [
        "Are you building a course, a learning platform, or a tutoring tool?",
        "Who are the learners — students, professionals, or general public?",
        "What subject matter and what does mastery look like for a learner?",
    ],
    "research": [
        "What is the research domain — scientific, market, academic, or competitive?",
        "What is the output — a report, a dataset, a model, or ongoing monitoring?",
        "What data sources are you pulling from — APIs, web scraping, databases, or primary collection?",
    ],
    "creative_writing": [
        "What format — novel, short story, screenplay, comic script, or something else?",
        "What genre and tone — sci-fi, fantasy, thriller, literary fiction, humor?",
        "Is the goal to finish a draft, publish, build a world, or collaborate with others?",
    ],
    "generic": [
        "What does success look like in 90 days — what's the measurable outcome?",
        "Who is the end user or beneficiary of this project?",
        "What is the single biggest obstacle between where you are now and that outcome?",
    ],
}

DOMAIN_SIGNALS = {
    "store":            ["shop", "store", "sell", "product", "ecommerce", "shopify", "etsy", "dropship", "inventory", "retail", "customer", "order", "woocommerce"],
    "game_dev":         ["game", "engine", "unity", "unreal", "godot", "level", "player", "shooter", "rpg", "multiplayer", "sprite", "3d", "2d", "indie"],
    "video_cgi":        ["video", "film", "animation", "cgi", "vfx", "render", "motion", "footage", "edit", "visual effects", "storyboard"],
    "music":            ["music", "song", "audio", "beat", "track", "instrument", "midi", "daw", "mixing", "guitar", "piano", "producer", "album", "spotify"],
    "software":         ["app", "software", "api", "backend", "frontend", "saas", "mobile", "web app", "platform", "tool", "dashboard", "database", "startup"],
    "hardware":         ["car", "vehicle", "robot", "drone", "device", "circuit", "sensor", "hardware", "embedded", "motor", "engine", "h2o", "hydrogen", "electric", "iot", "prototype", "firmware"],
    "creative_writing": ["novel", "book", "story", "fiction", "screenplay", "worldbuilding", "narrative", "character", "plot", "chapter", "author", "fantasy", "sci-fi"],
    "content":          ["blog", "newsletter", "podcast", "article", "seo", "audience", "creator", "post", "publish", "affiliate", "substack", "beehiiv"],
    "finance":          ["invest", "trading", "stock", "crypto", "portfolio", "finance", "market", "fund", "options", "algo", "backtest", "alpaca"],
    "real_estate":      ["real estate", "property", "house", "apartment", "rental", "landlord", "tenant", "mls", "airbnb", "realty", "mortgage"],
    "healthcare":       ["health", "medical", "patient", "clinical", "hospital", "ehr", "fhir", "hipaa", "pharma", "biotech", "diagnosis"],
    "legal":            ["legal", "law", "contract", "compliance", "attorney", "gdpr", "regulatory", "litigation", "clause", "nda"],
    "manufacturing":    ["manufacturing", "factory", "supply chain", "production", "logistics", "erp", "warehouse", "assembly", "shipment"],
    "education":        ["course", "learn", "teach", "tutorial", "student", "curriculum", "lesson", "quiz", "lms", "cohort"],
    "research":         ["research", "experiment", "dataset", "academic", "science", "lab", "publication", "hypothesis", "paper"],
    "social_media":     ["instagram", "tiktok", "linkedin", "influencer", "follower", "engagement", "viral", "social media", "content creator"],
    "events":           ["event", "conference", "wedding", "venue", "ticket", "attendee", "registration", "hospitality", "meetup"],
    "nonprofit":        ["nonprofit", "charity", "donation", "donor", "grant", "volunteer", "fundraise", "mission", "ngo"],
    "cybersecurity":    ["security", "cyber", "vulnerability", "soc", "siem", "threat", "firewall", "penetration", "soc 2", "iso 27001"],
    "agtech":           ["farm", "crop", "agriculture", "soil", "irrigation", "harvest", "precision farming", "yield", "livestock"],
    "hr":               ["hr", "hiring", "recruit", "employee", "payroll", "onboarding", "talent", "workforce", "performance review", "attrition"],
    "mlops":            ["ml", "model training", "fine-tune", "mlops", "gpu", "llm", "embedding", "weights", "hugging face", "inference"],
    "water_env":        ["water", "wastewater", "utility", "leak", "irrigation", "epa", "environmental monitoring", "water quality"],
}


class GoalLayer:
    def __init__(self, goal: str, api_key: str = None):
        self.goal    = goal
        self.defined = bool(goal and len(goal) > 10)
        self.domain  = None

    def validate(self) -> str:
        if not self.defined:
            raise ValueError(
                "Orlog requires a goal before running. "
                "Pass goal= to Orlog() or call goal_layer.set_goal()"
            )
        return self.goal

    def set_goal(self, goal: str):
        self.goal    = goal
        self.defined = True
        self.domain  = None  # reset on new goal

    def detect_domain(self) -> str:
        """Classify the goal into a domain using keyword signals + Claude fallback."""
        if self.domain:
            return self.domain

        goal_lower = self.goal.lower()

        # Fast keyword pass first
        scores = {}
        for domain, signals in DOMAIN_SIGNALS.items():
            scores[domain] = sum(1 for s in signals if s in goal_lower)

        best = max(scores, key=scores.get)
        if scores[best] >= 2:
            self.domain = best
            return self.domain

        # Ambiguous — ask LLM
        detected = fast_call(
            "You are a domain classifier. Return only the domain name, nothing else.",
            f"Classify this project into exactly one domain: {list(DOMAIN_BINS.keys())}\n"
            f"Project: {self.goal}\n"
            f"Return only the domain name, nothing else.",
            50,
        ).strip().lower()
        self.domain = detected if detected in DOMAIN_BINS else "generic"
        return self.domain

    def needs_clarification(self) -> bool:
        """Return True if goal is too vague to act on."""
        if not self.defined:
            return True
        goal_lower = self.goal.lower()
        vague_signals = ["something", "anything", "not sure", "maybe", "kind of", "sort of", "idk"]
        if any(s in goal_lower for s in vague_signals):
            return True
        # Goals under 8 words are almost always too thin
        if len(self.goal.split()) < 8:
            return True
        return False

    def clarify(self, domain: str = None) -> list:
        """Return clarifying questions for this domain type."""
        d = domain or self.detect_domain()
        return CLARIFYING_QUESTIONS.get(d, CLARIFYING_QUESTIONS["generic"])

    def toybox_bin(self) -> str:
        """Return the toy box bin path for this goal's domain."""
        return DOMAIN_BINS.get(self.detect_domain(), DOMAIN_BINS["generic"])

    def assess_goal_alignment(self, action: str) -> dict:
        """Check if a proposed action moves toward the goal."""
        text = fast_call(
            "You are a goal alignment checker. Answer: toward/away/neutral + one sentence why.",
            f"Goal: {self.goal}\nProposed action: {action}\n"
            f"Does this move toward the goal? Answer: toward/away/neutral + one sentence why.",
            200,
        )
        direction = "toward" if "toward" in text.lower() else "away" if "away" in text.lower() else "neutral"
        return {"direction": direction, "reasoning": text}

    def summary(self) -> dict:
        return {
            "goal":               self.goal,
            "defined":            self.defined,
            "domain":             self.detect_domain(),
            "toybox_bin":         self.toybox_bin(),
            "needs_clarification": self.needs_clarification(),
            "clarifying_questions": self.clarify(),
        }
