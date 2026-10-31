"""
orlog/eval/modal_cron.py — 24/7 autonomous DPO pair accumulation on Modal.

Runs on a cron schedule, generating constitutionally-filtered DPO pairs
continuously until Mimir has enough to train. Pairs saved to GCS bucket
and synced locally on download.

Architecture:
  Cron trigger (every 2h) → fan-out N questions in parallel containers
  → each container runs full Orlog council → constitutional filter (6 invariants)
  → pairs with gap >= threshold saved to GCS → local sync on demand

Constitutional scoring replaces numerical scoring (Orlog diagnosis 2026-05-17):
  1-10 scale collapses to ~8.0 (3.3 bits vs 50+ bits quality space).
  6-invariant checklist is ground truth. No scorer bias possible.

Cost estimate:
  30 questions × 2 council calls × ~5s each = ~5min compute per run
  Modal CPU: ~$0.0001/second → ~$0.03/run → ~$0.36/day at 12 runs/day
  Target: 200 pairs → typically ~3-5 days at this rate

Deploy:
    modal deploy orlog/eval/modal_cron.py

Trigger manually:
    modal run orlog/eval/modal_cron.py

Check status:
    modal app list
    modal volume ls orlog-pairs

Download pairs:
    modal volume get orlog-pairs dpo_pairs.jsonl logs/modal_dpo_pairs.jsonl
"""

import os
import json
import modal
from pathlib import Path
from dotenv import load_dotenv
from datetime import datetime

load_dotenv(Path(__file__).parent.parent.parent / ".env")

# ── Modal infrastructure ──────────────────────────────────────────────────────

app = modal.App("orlog-cron")

image = (
    modal.Image.debian_slim(python_version="3.11")
    .pip_install(
        "anthropic>=0.25.0",
        "python-dotenv>=1.0.0",
    )
)

# Persistent volume — pairs survive across runs, accumulate to 200+
volume = modal.Volume.from_name("orlog-pairs", create_if_missing=True)
VOLUME_MOUNT = Path("/pairs")

orlog_secret = modal.Secret.from_dict({
    "ANTHROPIC_API_KEY": os.getenv("ANTHROPIC_API_KEY", ""),
})


# ── Constitutional filter (runs in container — no local file deps) ────────────

INVARIANTS = [
    ("falsifiability",   0.20, "Does the verdict make a claim that could be proven wrong?"),
    ("non_circularity",  0.15, "Does the reasoning lead to the verdict, not restate it?"),
    ("specificity",      0.20, "Does the verdict apply to THIS question, not any question?"),
    ("tension_preserved",0.15, "Was the key disagreement resolved, not averaged?"),
    ("blind_spot_named", 0.15, "Does the synthesis name what it cannot see?"),
    ("no_target_leakage",0.15, "Does it avoid reasoning from information unavailable at decision time?"),
]

CONSTITUTION_SYSTEM = (
    "You are a reasoning auditor. Score a synthesis on 6 structural invariants. "
    "Reply with JSON only — an array of 6 objects, one per invariant, in the order given."
)

_BATCH_INVARIANT_PROMPT = """QUESTION: {question}

SYNTHESIS:
{synthesis}

Score each invariant. Reply with a JSON array of exactly 6 objects, one per invariant:
[
  {{"name": "falsifiability",    "passes": true/false, "confidence": 0.0-1.0}},
  {{"name": "non_circularity",   "passes": true/false, "confidence": 0.0-1.0}},
  {{"name": "specificity",       "passes": true/false, "confidence": 0.0-1.0}},
  {{"name": "tension_preserved", "passes": true/false, "confidence": 0.0-1.0}},
  {{"name": "blind_spot_named",  "passes": true/false, "confidence": 0.0-1.0}},
  {{"name": "no_target_leakage", "passes": true/false, "confidence": 0.0-1.0}}
]

Criteria:
1. falsifiability: Does the verdict make a claim that could be proven wrong?
2. non_circularity: Does the reasoning lead to the verdict, not restate it?
3. specificity: Does the verdict apply to THIS question, not any question?
4. tension_preserved: Was the key disagreement resolved, not averaged?
5. blind_spot_named: Does the synthesis name what it cannot see?
6. no_target_leakage: Does it avoid reasoning from information unavailable at decision time?"""


def _constitutional_score_inline(client, question: str, synthesis: str) -> tuple[float, bool]:
    """Constitutional filter — single batched Haiku call instead of 6 separate calls."""
    weights = {i[0]: i[1] for i in INVARIANTS}
    fallback = sum(w * 0.3 for _, w, _ in INVARIANTS)
    try:
        # Build prompt safely — synthesis may contain { } which breaks .format()
        prompt = (
            _BATCH_INVARIANT_PROMPT
            .replace("{question}", question)
            .replace("{synthesis}", synthesis[:1200])
        )
        raw = client.messages.create(
            model="claude-haiku-4-5-20251001",
            max_tokens=300,
            system=CONSTITUTION_SYSTEM,
            messages=[{"role": "user", "content": prompt}],
        ).content[0].text.strip()
        if not raw:
            return round(fallback, 3), fallback >= 0.40
        # Strip markdown fences if present
        raw = raw.removeprefix("```json").removeprefix("```").removesuffix("```").strip()
        start = raw.find("["); end = raw.rfind("]") + 1
        parsed = json.loads(raw[start:end]) if (start >= 0 and end > start) else []
        if not parsed:
            return round(fallback, 3), fallback >= 0.40
        score = 0.0
        for r in parsed:
            w = weights.get(r.get("name", ""), 0)
            confidence = float(r.get("confidence", 0.5))
            score += w * (confidence if r.get("passes") else confidence * 0.1)
    except Exception as e:
        print(f"[scorer] fallback triggered: {type(e).__name__}: {e}")
        score = fallback
    return round(score, 3), score >= 0.40


# ── Single question worker ─────────────────────────────────────────────────────

COUNCIL_VOICES = [
    ("adhd",       "Scan for non-obvious connections first. Move fast across domains. What is the unexpected move?"),
    ("autism",     "Map every edge case before concluding. Find what everyone agrees to ignore."),
    ("ocd",        "Verify every assumption systematically. Find what is missing or unverified."),
    ("paranoid",   "Assume adversarial conditions. What could go wrong? Who benefits if this fails?"),
    ("empath",     "Speak for the humans affected. Name the human cost of the 'correct' answer."),
    ("contrarian", "Steelman the exact opposite position with intellectual rigor."),
    ("visionary",  "Find the best-case trajectory others dismiss as naive but is actually achievable."),
    ("calibrator", "Does the reasoning match ground truth? What base rate applies? What evidence falsifies this?"),
]

SYNTHESIZER_PROMPT = (
    "You have read multiple cognitive perspectives on the same question. "
    "Resolve their disagreements by finding what is true IN the tension — not by averaging. "
    "Your synthesis will be evaluated on six structural invariants:\n"
    "1. FALSIFIABILITY: Your verdict must make a specific claim that could be proven wrong.\n"
    "2. NON-CIRCULARITY: Your reasoning must lead to the verdict, not restate it.\n"
    "3. SPECIFICITY: Your verdict must apply to THIS specific question, not any question.\n"
    "4. TENSION PRESERVED: Name the sharpest disagreement and resolve it — do not average it away.\n"
    "5. BLIND SPOT: Name what ALL perspectives missed, including yours.\n"
    "6. NO TARGET LEAKAGE: Do not reason from information unavailable at decision time.\n\n"
    "Reply with:\n"
    "VERDICT: [one falsifiable sentence specific to this question]\n"
    "REASONING: [2-3 sentences leading necessarily to the verdict]\n"
    "KEY TENSION: [the sharpest disagreement and how it resolves]\n"
    "BLIND SPOT: [what all perspectives — including this synthesis — cannot see]"
)


@app.function(
    image=image,
    secrets=[orlog_secret],
    timeout=300,
    retries=1,
)
def run_question_pair(domain: str, task_type: str, goal: str, question: str) -> dict:
    """
    Run two independent council instances on one question.
    Constitutionally score both. Return pair if one passes and gap >= threshold.
    """
    import os as _os
    from anthropic import Anthropic
    import time

    client = Anthropic(api_key=_os.getenv("ANTHROPIC_API_KEY"))

    def _run_council(q: str) -> str:
        perspectives = {}
        for voice, instruction in COUNCIL_VOICES:
            try:
                r = client.messages.create(
                    model="claude-haiku-4-5-20251001",
                    max_tokens=300,
                    system=f"You are the {voice.upper()} cognitive style in a reasoning council. {instruction}",
                    messages=[{"role": "user", "content": f"Domain: {domain}\nQuestion: {q}"}],
                )
                perspectives[voice] = r.content[0].text.strip()
            except Exception:
                perspectives[voice] = ""

        perspectives_text = "\n\n".join(
            f"[{name.upper()}]\n{text}" for name, text in perspectives.items()
        )
        try:
            synthesis = client.messages.create(
                model="claude-haiku-4-5-20251001",
                max_tokens=900,
                system=SYNTHESIZER_PROMPT,
                messages=[{"role": "user", "content": f"QUESTION: {question}\n\n{perspectives_text}"}],
            ).content[0].text.strip()
        except Exception:
            synthesis = perspectives_text[:600]

        return synthesis

    # Two independent councils — slight delay so brain state may vary
    synth_a = _run_council(question)
    time.sleep(0.5)
    synth_b = _run_council(question)

    score_a, passed_a = _constitutional_score_inline(client, question, synth_a)
    score_b, passed_b = _constitutional_score_inline(client, question, synth_b)

    gap = abs(score_a - score_b)
    pair_valid = (passed_a or passed_b) and gap >= 0.05

    result = {
        "domain":     domain,
        "question":   question,
        "task_type":  task_type,
        "score_a":    score_a,
        "score_b":    score_b,
        "passed_a":   passed_a,
        "passed_b":   passed_b,
        "gap":        round(gap, 3),
        "pair_valid": pair_valid,
        "timestamp":  datetime.now().isoformat(),
    }

    if pair_valid:
        if score_a >= score_b:
            result["chosen"], result["rejected"] = synth_a, synth_b
        else:
            result["chosen"], result["rejected"] = synth_b, synth_a

    return result


# ── Question bank (inlined — no local orlog module deps in container) ─────────

QUESTION_BANK = [
    ("store", "strategy", "Curated gift store selling dropshipped products to cat lovers and couples",
     "What is the single highest-leverage action to take before spending any money on ads?"),
    ("store", "launch",   "Handmade ceramic jewelry Shopify store, solo founder, $500 launch budget",
     "What must be true on day one for the first sale to happen within 30 days?"),
    ("store", "ops",      "Dropshipping store with 400 products, 2 suppliers, no orders yet",
     "Which operational process should be automated first and why?"),
    ("software", "strategy", "SaaS tool for freelancers to manage client invoices and contracts",
     "What is the riskiest assumption in the product that must be validated before writing code?"),
    ("software", "build",    "API-first platform with a React frontend and Postgres backend",
     "What should the data model for user permissions look like to avoid a painful refactor at scale?"),
    ("software", "debug",    "Backend service that randomly drops 3% of webhook events under load",
     "What is the most likely root cause and what is the fastest way to confirm it?"),
    ("game_dev", "strategy", "Open world survival game where one player occupies each region server-enforced",
     "What is the core loop that must feel right before any other system is worth building?"),
    ("game_dev", "build",    "Godot 4 multiplayer game using Supabase for persistence",
     "How should region ownership be enforced without introducing race conditions?"),
    ("game_dev", "launch",   "Indie game in early access on Steam, 200 wishlists, no marketing budget",
     "What is the minimum content bar to clear before a public demo makes sense?"),
    ("content", "strategy", "Solo creator writing about personal finance for people in their 30s",
     "What content format compounds the most over 12 months with the least ongoing effort?"),
    ("content", "copy",     "Newsletter with 800 subscribers, 28% open rate, zero paid products yet",
     "Write a subject line framework for a launch email that converts readers to buyers for the first time."),
    ("content", "launch",   "Digital course teaching freelancers to raise their rates",
     "What must the sales page accomplish in the first 10 seconds to prevent the back-click?"),
    ("finance", "strategy", "Individual investor building a long-term portfolio on $2k/month savings",
     "What allocation decision has the highest impact on 20-year outcome and why?"),
    ("finance", "research", "Small business owner deciding between an S-corp election and staying as LLC",
     "What are the real break-even conditions where S-corp saves more than it costs?"),
    ("creative_writing", "strategy", "Sci-fi novel about consciousness as time travel, first-time novelist",
     "What structural decision made at the outline stage most determines whether the book gets finished?"),
    ("creative_writing", "build",    "Three-act thriller with an unreliable narrator and a mid-story identity reveal",
     "Where should the identity reveal land relative to the act breaks and why?"),
    ("mlops", "strategy", "ML team shipping a recommendation model to production for the first time",
     "What monitoring setup catches the failure modes that kill recommendation systems in production?"),
    ("mlops", "debug",    "Model accuracy dropped 8 points after a data pipeline change last week",
     "What is the debugging sequence that isolates whether this is a data problem or a model problem?"),
    ("research", "strategy", "Academic researcher studying the long-term effects of remote work on cognition",
     "What study design decisions made at the start are hardest to fix later?"),
    ("research", "research", "Startup trying to understand why their churn rate doubled after a pricing change",
     "What is the fastest way to distinguish price sensitivity from product dissatisfaction in the churn data?"),
    ("social_media", "strategy", "Brand account on Instagram with 1,200 followers and 0 product sales from social",
     "What is the structural reason most brand accounts never convert followers to buyers?"),
    ("social_media", "ops",      "Solo founder posting daily across Instagram, TikTok, and Pinterest",
     "What is the minimum viable content system that prevents creative burnout at 90 days?"),
    ("education", "strategy", "EdTech startup teaching coding to adult career changers via cohort model",
     "What is the single metric that best predicts whether a student will complete and get hired?"),
    ("education", "build",    "Online course platform deciding between cohort-based and self-paced formats",
     "Under what conditions does cohort format produce better outcomes and when does it destroy them?"),
    ("healthcare", "strategy", "Digital health app helping patients manage chronic pain with CBT exercises",
     "What is the retention pattern that separates apps that help from apps that get deleted?"),
    ("real_estate", "strategy", "First-time landlord buying a duplex in a mid-sized city with 20% down",
     "What due diligence step is most commonly skipped that causes the biggest regrets?"),
    ("nonprofit", "strategy", "Community org running a food pantry, 3 staff, 200 volunteers, $400k annual budget",
     "What is the operational bottleneck that limits how many families you can serve?"),
    ("manufacturing", "ops", "Small batch ceramics studio moving from handmade to semi-automated production",
     "At what production volume does automation save more time than it costs to set up and maintain?"),
    ("music", "launch", "Independent musician releasing first EP with no label, 800 Instagram followers",
     "What release sequence maximizes streams and keeps listeners engaged past the first week?"),
    ("music", "strategy", "Producer building a catalog of beats for licensing to indie filmmakers",
     "What catalog size and tagging strategy makes the licensing income meaningfully passive?"),

    # ── Held-out domain expansion: constraint-aware questions ─────────────────
    ("healthcare", "ops", "Rural clinic, one doctor, 300 patients, burnout risk, no budget for new hires",
     "What is the highest-leverage intervention to improve patient outcomes without adding staff?"),
    ("healthcare", "strategy", "Community health clinic serving uninsured patients, 60% no-show rate",
     "What is the load-bearing assumption behind scheduling systems and why does it fail for this population?"),
    ("healthcare", "build", "Telehealth startup targeting rural elderly patients with poor internet access",
     "What is the constraint that makes standard video-call UX fail and what is the minimum viable alternative?"),
    ("healthcare", "ops", "Hospital reducing readmissions without increasing discharge staff or follow-up budget",
     "What single intervention at discharge has the highest evidence base for reducing 30-day readmissions?"),
    ("healthcare", "strategy", "Mental health practice with 6-month waitlist and no capacity to add therapists",
     "What structural change reduces the waitlist without increasing clinical hours?"),

    ("real_estate", "strategy", "Duplex owner, $300/month cash flow, 8% vacancy, no property manager",
     "What is the hidden cost most first-time landlords ignore until year 3 that erases this margin?"),
    ("real_estate", "ops", "Short-term rental host with 3 properties, 70% occupancy, declining reviews",
     "What is the operational failure point that causes review decline in multi-property STR portfolios?"),
    ("real_estate", "strategy", "Investor evaluating a strip mall at 9% cap rate in a mid-sized declining city",
     "What due diligence question most investors skip that determines whether this cap rate is real?"),
    ("real_estate", "build", "House flipper averaging 4 months per project, $40k average profit, wants to scale",
     "What is the bottleneck that prevents scaling beyond 2 simultaneous flips without losing margin?"),

    ("legal", "strategy", "Solo attorney billing 40 hours/week, revenue plateau at $180k, no associates",
     "What is the structural reason solo practices plateau and what is the minimum viable path past it?"),
    ("legal", "ops", "Law firm losing clients after initial consultation despite strong reviews",
     "What is the conversion failure point between consultation and engagement that most firms ignore?"),

    ("education", "strategy", "Bootcamp with 85% job placement rate but declining enrollment despite strong outcomes",
     "What is the gap between outcomes data and enrollment behavior that the bootcamp is not addressing?"),
    ("education", "build", "School district adopting 1:1 devices for students without changing curriculum",
     "What is the load-bearing assumption about technology adoption that predicts whether this improves outcomes?"),
    ("education", "ops", "University with 40% STEM dropout rate in year one, no budget for new advisors",
     "What single early-warning signal best predicts first-year dropout and can be acted on without extra staff?"),

    ("personal_finance", "strategy", "28-year-old with $50k savings, $40k student debt at 5.5%, asking whether to invest or pay debt",
     "What is the decision framework and what hidden assumption does most advice on this question get wrong?"),
]


# ── Three-stage adaptive pipeline ─────────────────────────────────────────────
#
# Stage 1 — Diagnostic: score both councils, log why pairs fail (not just pass/fail)
# Stage 2 — Gate adaptation: if avg score drifts, adjust thresholds to match reality
# Stage 3 — Dead-stop: if yield < 10% for a full day, pause + self-audit inline
#
# Thresholds live in the volume so they persist across runs and can adapt
# without redeploying. The gate is no longer hardcoded.

HISTORY_FILE   = VOLUME_MOUNT / "run_history.jsonl"
GATE_FILE      = VOLUME_MOUNT / "gate_state.json"
ATTENTION_FILE = VOLUME_MOUNT / "needs_attention.json"

# Baseline thresholds — adapted automatically by Stage 2
DEFAULT_PASS_THRESHOLD = 0.40
DEFAULT_GAP_THRESHOLD  = 0.05
PASS_FLOOR = 0.20   # never go below this (pairs would be noise)
PASS_CEIL  = 0.65   # never go above this (too restrictive)


def _load_gate() -> dict:
    """Load current adaptive thresholds from volume."""
    try:
        if GATE_FILE.exists():
            return json.loads(GATE_FILE.read_text())
    except Exception:
        pass
    return {"pass_threshold": DEFAULT_PASS_THRESHOLD,
            "gap_threshold":  DEFAULT_GAP_THRESHOLD,
            "adapted_at":     None,
            "reason":         "default"}


def _save_gate(gate: dict):
    GATE_FILE.write_text(json.dumps(gate, indent=2))


def _load_history(n: int = 10) -> list[dict]:
    """Load last N run records from volume."""
    if not HISTORY_FILE.exists():
        return []
    lines = [l for l in HISTORY_FILE.read_text().splitlines() if l.strip()]
    records = []
    for l in lines[-n:]:
        try:
            records.append(json.loads(l))
        except Exception:
            pass
    return records


def _append_history(record: dict):
    with open(HISTORY_FILE, "a") as f:
        f.write(json.dumps(record) + "\n")


def _stage2_adapt_gate(history: list[dict], gate: dict) -> dict:
    """
    Stage 2: adjust pass/gap thresholds based on rolling score trend.

    - avg_score < 0.35 for 3 consecutive runs → lower pass_threshold by 0.05
      (scores are low; gate is starving the pipeline)
    - avg_score > 0.60 for 3 consecutive runs → raise pass_threshold by 0.05
      (quality is improving; tighten the filter)
    - yield_rate < 0.10 for 3 consecutive runs → also lower gap_threshold by 0.01
    """
    if len(history) < 3:
        return gate  # not enough data yet

    recent = history[-3:]
    avg_scores = [(r.get("avg_const_a", 0) + r.get("avg_const_b", 0)) / 2 for r in recent]
    yield_rates = [r.get("run_pairs", 0) / max(r.get("run_questions", 1), 1) for r in recent]

    mean_score = sum(avg_scores) / len(avg_scores)
    mean_yield = sum(yield_rates) / len(yield_rates)

    new_gate = dict(gate)
    reason = None

    if mean_score < 0.35 and gate["pass_threshold"] > PASS_FLOOR:
        new_gate["pass_threshold"] = round(max(PASS_FLOOR, gate["pass_threshold"] - 0.05), 2)
        reason = f"avg_score={mean_score:.3f} < 0.35 for 3 runs → lowered threshold"
    elif mean_score > 0.60 and gate["pass_threshold"] < PASS_CEIL:
        new_gate["pass_threshold"] = round(min(PASS_CEIL, gate["pass_threshold"] + 0.05), 2)
        reason = f"avg_score={mean_score:.3f} > 0.60 for 3 runs → raised threshold"

    if mean_yield < 0.10:
        new_gate["gap_threshold"] = round(max(0.02, gate["gap_threshold"] - 0.01), 2)
        reason = (reason or "") + f" | yield={mean_yield:.2f} < 0.10 → lowered gap threshold"

    if reason:
        new_gate["adapted_at"] = datetime.now().isoformat()
        new_gate["reason"]     = reason
        print(f"[orlog-cron] Stage 2 adaptation: {reason}")
        print(f"[orlog-cron] New gate: pass>={new_gate['pass_threshold']} gap>={new_gate['gap_threshold']}")

    return new_gate


def _stage3_dead_stop(history: list[dict]) -> bool:
    """
    Stage 3: true if yield < 10% across all runs in the last 24 hours.
    Returns True when the pipeline has effectively stopped producing pairs.
    """
    if len(history) < 2:
        return False
    cutoff = datetime.now().isoformat()[:10]  # today's date prefix
    today_runs = [r for r in history if r.get("timestamp", "")[:10] >= cutoff]
    if not today_runs:
        return False
    total_q = sum(r.get("run_questions", 0) for r in today_runs)
    total_p = sum(r.get("run_pairs", 0) for r in today_runs)
    return total_q >= 20 and (total_p / total_q) < 0.10


def _fast_self_audit_inline(client, reason: str) -> str:
    """
    Stage 3 inline fast audit — single Haiku call, no local deps.
    Returns a one-sentence diagnosis to log in the attention file.
    """
    prompt = (
        f"The Orlog DPO pair generation pipeline has stalled. Reason: {reason}\n\n"
        "In one sentence, what is the most likely root cause and the cheapest fix?\n"
        "Be specific about what to change in the code or prompts."
    )
    try:
        r = client.messages.create(
            model="claude-haiku-4-5-20251001",
            max_tokens=200,
            system="You are a concise ML pipeline debugger. One sentence only.",
            messages=[{"role": "user", "content": prompt}],
        )
        return r.content[0].text.strip()
    except Exception as e:
        return f"[audit error: {e}]"


# ── Orchestrator — fans out, saves to volume ──────────────────────────────────

@app.function(
    image=image,
    secrets=[orlog_secret],
    volumes={str(VOLUME_MOUNT): volume},
    timeout=600,
)
def orchestrate(limit: int = 30, domain: str = None):
    """
    Three-stage adaptive pipeline:
      Stage 1 — run questions, score, log failure reasons
      Stage 2 — adapt thresholds based on rolling score trend
      Stage 3 — detect dead stop, pause + self-audit inline
    """
    import json
    import os as _os
    from anthropic import Anthropic

    # Pre-flight: verify API key works before burning any compute
    _os_key = _os.getenv("ANTHROPIC_API_KEY", "")
    if not _os_key:
        print("[orlog-cron] ABORT: ANTHROPIC_API_KEY not set.")
        return {"error": "no_api_key", "total_pairs": 0}
    try:
        _test = Anthropic(api_key=_os_key).messages.create(
            model="claude-haiku-4-5-20251001",
            max_tokens=5,
            messages=[{"role": "user", "content": "ping"}],
        )
    except Exception as e:
        print(f"[orlog-cron] ABORT: API pre-flight failed — {e}")
        print("[orlog-cron] Check Anthropic credit balance at console.anthropic.com")
        return {"error": str(e), "total_pairs": 0}

    # Load adaptive gate state
    gate = _load_gate()
    pass_threshold = gate["pass_threshold"]
    gap_threshold  = gate["gap_threshold"]
    print(f"[orlog-cron] Gate: pass>={pass_threshold} gap>={gap_threshold} (reason: {gate['reason']})")

    questions = QUESTION_BANK
    if domain:
        questions = [q for q in questions if q[0] == domain]
    questions = questions[:limit]

    print(f"[orlog-cron] {len(questions)} questions, constitutional scoring...")
    results = list(run_question_pair.starmap(questions))

    # Stage 1 — diagnostic breakdown (why did pairs fail?)
    passed_a  = sum(1 for r in results if r.get("score_a", 0) >= pass_threshold)
    passed_b  = sum(1 for r in results if r.get("score_b", 0) >= pass_threshold)
    gap_ok    = sum(1 for r in results if abs(r.get("score_a",0)-r.get("score_b",0)) >= gap_threshold)
    pairs     = [r for r in results if r.get("pair_valid")]
    total     = len(results)
    avg_a     = sum(r["score_a"] for r in results) / max(total, 1)
    avg_b     = sum(r["score_b"] for r in results) / max(total, 1)
    yield_pct = len(pairs) / max(total, 1)

    print(f"[orlog-cron] Stage 1 diagnostic:")
    print(f"  passed_a={passed_a}/{total}  passed_b={passed_b}/{total}  gap_ok={gap_ok}/{total}")
    print(f"  → {len(pairs)} pairs ({yield_pct:.0%} yield) | avg_a={avg_a:.3f} avg_b={avg_b:.3f}")

    # Append pairs to volume
    pairs_file = VOLUME_MOUNT / "dpo_pairs.jsonl"
    with open(pairs_file, "a") as f:
        for p in pairs:
            row = {k: v for k, v in p.items()
                   if k in ("domain","question","chosen","rejected","score_a","score_b","gap","timestamp")}
            row["source"] = "modal_cron"
            f.write(json.dumps(row) + "\n")

    total_pairs = sum(1 for l in open(pairs_file) if l.strip()) if pairs_file.exists() else 0

    # Build run record for history
    run_record = {
        "timestamp":    datetime.now().isoformat(),
        "run_questions": total,
        "run_pairs":    len(pairs),
        "yield_rate":   round(yield_pct, 3),
        "avg_const_a":  round(avg_a, 3),
        "avg_const_b":  round(avg_b, 3),
        "passed_a":     passed_a,
        "passed_b":     passed_b,
        "gap_ok":       gap_ok,
        "pass_threshold": pass_threshold,
        "gap_threshold":  gap_threshold,
        "total_pairs":  total_pairs,
    }
    _append_history(run_record)

    # Stage 2 — adapt gate thresholds based on rolling trend
    history = _load_history(n=10)
    new_gate = _stage2_adapt_gate(history, gate)
    if new_gate != gate:
        _save_gate(new_gate)

    # Stats file for local sync
    stats = {**run_record, "target": 200, "pct_to_target": round(total_pairs / 200 * 100, 1)}
    (VOLUME_MOUNT / "run_stats.json").write_text(json.dumps(stats, indent=2))

    volume.commit()

    print(f"[orlog-cron] Total accumulated: {total_pairs}/200 ({stats['pct_to_target']}%)")
    return stats


# ── Cron schedule — twice daily ───────────────────────────────────────────────

@app.function(
    image=image,
    secrets=[orlog_secret],
    volumes={str(VOLUME_MOUNT): volume},
    schedule=modal.Cron("0 20 * * *"),  # once daily — 8pm UTC (~$0.23/day)
)
def scheduled_run():
    """
    Cron entry point. After orchestrate:
      - Checks Stage 3 dead-stop condition
      - Runs inline fast self-audit if stalled
      - Writes attention flag for local watchdog to pick up
    """
    import os as _os
    from anthropic import Anthropic

    stats = orchestrate.remote(limit=10)

    # LoRA target reached
    if stats.get("total_pairs_accumulated", stats.get("total_pairs", 0)) >= 200:
        print("[orlog-cron] TARGET REACHED: 200+ pairs. Ready for LoRA.")
        print("[orlog-cron] modal volume get orlog-pairs dpo_pairs.jsonl logs/modal_dpo_pairs.jsonl")
        return stats

    # Stage 3 — dead-stop detection
    history = _load_history(n=10)
    if _stage3_dead_stop(history):
        print("[orlog-cron] Stage 3: DEAD STOP detected (yield < 10% today)")

        client = Anthropic(api_key=_os.getenv("ANTHROPIC_API_KEY"))
        gate   = _load_gate()
        reason = (
            f"yield_rate={stats.get('yield_rate', 0):.0%}, "
            f"avg_score={stats.get('avg_const_a', 0):.3f}, "
            f"pass_threshold={gate['pass_threshold']}, "
            f"gate_reason={gate['reason']}"
        )
        diagnosis = _fast_self_audit_inline(client, reason)
        print(f"[orlog-cron] Fast audit diagnosis: {diagnosis}")

        attention = {
            "flagged_at": datetime.now().isoformat(),
            "reason":     reason,
            "diagnosis":  diagnosis,
            "gate":       gate,
            "stats":      stats,
        }
        ATTENTION_FILE.write_text(json.dumps(attention, indent=2))
        volume.commit()
        print("[orlog-cron] Attention flag written. Run: python3 -m orlog.self_improve --propose")

    return stats


# ── Local entrypoints ─────────────────────────────────────────────────────────

@app.local_entrypoint()
def main(domain: str = None, limit: int = "10"):
    """Manual trigger: modal run orlog/eval/modal_cron.py"""
    stats = orchestrate.remote(limit=int(limit), domain=domain)
    local_path = Path("logs/modal_dpo_pairs.jsonl")
    if local_path.exists():
        local_count = sum(1 for _ in local_path.open())
        print(f"\nLocal pair count: {local_count} (run volume get to refresh)")
    else:
        print("\nDownload pairs: modal volume get orlog-pairs dpo_pairs.jsonl logs/modal_dpo_pairs.jsonl")


@app.function(
    image=image,
    volumes={str(VOLUME_MOUNT): volume},
)
def _do_reset_gate():
    import json as _json
    gate = {
        "pass_threshold": DEFAULT_PASS_THRESHOLD,
        "gap_threshold":  DEFAULT_GAP_THRESHOLD,
        "adapted_at":     None,
        "reason":         "manual reset — gate was over-tightened",
    }
    GATE_FILE.write_text(_json.dumps(gate, indent=2))
    volume.commit()
    print(f"[gate] Reset → pass>={DEFAULT_PASS_THRESHOLD} gap>={DEFAULT_GAP_THRESHOLD}")
    return gate


@app.local_entrypoint()
def reset_gate():
    """Reset adaptive gate to defaults. Run when gate got too strict and yields 0 pairs.
    modal run orlog/eval/modal_cron.py::reset_gate
    """
    result = _do_reset_gate.remote()
    print(f"Gate reset → pass>={result['pass_threshold']} gap>={result['gap_threshold']}")
    print("Now run: modal run orlog/eval/modal_cron.py")
