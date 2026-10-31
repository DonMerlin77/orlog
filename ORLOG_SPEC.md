# ORLOG V1 SPECIFICATION
> Source of truth for everything Orlog is and does.
> Update this before writing code. Read this before asking "what's next."
> Last updated: 2026-05-15

---

## WHAT ORLOG IS

Orlog is a domain-agnostic AI operating system built on a single proof:
structured reasoning feedback loops improve AI output quality regardless of domain.

**Proven:** Claude + lite reasoning brain (~344-580 tokens) scores 8.5/10 avg vs 6.1/10 baseline Qwen. The brain works. The eval proves it. Now we build the system around that proof.

**The product in one sentence:** You plug in a project — any project — Orlog figures out what it needs, assembles the right agents, runs them through a reasoning loop, scores the output, and gets better over time.

**Two delivery phases:**
- **Phase 1 (now):** Bring-your-own-Claude framework. Brain injected per call via Amplifier. $49-99/month SaaS.
- **Phase 2 (Cocoon target):** Brain incubated in Qwen weights via DPO. No injection cost. No cold start. The model reasons in Orlog's lenses by default.

---

## THE FIVE LAYERS

### LAYER 1 — THINK (how it reasons)
*Status: ✅ PROVEN*

**Brain** (`orlog/brain/`)
- 27 reasoning lenses in `agents/intelligence/reasoning_brain.md`
- The secret weapon. Distilled signal, not documentation.
- Three sub-layers: core (hand-written) + synthesized (research pipeline) + learned (user-fed lessons)
- Edit the brain, all agents update. No code changes needed.

**Brain Tiers**
- `whisper` (~200 tokens) — nudge only, for frontier models that already reason well. Score: 7.20
- `lite` (~344 tokens) — **current best**. Score: 8.53. Sweet spot.
- `tier3` (~581 tokens) — statistically tied with lite. Score: 8.50
- `tier4-6` (748-1379 tokens) — generated, evals pending
- `full` (~5600 tokens) — exceeds Qwen context, scores 8.07 for Claude (worse than lite)
- **Finding:** Claude tops out at 400-600 tokens of compressed signal. More degrades, less misses.

**Darwin** (`orlog/brain/darwin.py` — TO BUILD)
- Evolutionary lens pruning. Living brains prune or they degrade.
- Prune by: (1) usage frequency — unused lenses get cut, (2) outcome correlation — lenses that correlate with high Orlog scores stay, (3) redundancy — overlapping lenses merge, (4) supersession — new lenses must name what they replace.
- Data source: eval logs + which lenses appear in winning outputs.
- Self-healing guarantee: research pipeline always feeds new signal in, Darwin prunes noise out.

**Deliberation Gate** (Lens 19 in reasoning brain)
- Orlog knows when to think fast vs slow.
- Fast: routine decisions, well-established patterns.
- Slow: irreversible actions, novel situations, high-stakes outputs.
- Not a separate module — baked into the brain.

---

### LAYER 2 — ORIENT (what it's working toward)
*Status: 🔲 Scaffolded, untested*

**Goal Layer** (`orlog/goal/goal_layer.py`)
- First thing Orlog runs, every time.
- If goal is clear: validate it, store it, proceed.
- If goal is ambiguous: ask CLARIFYING_QUESTIONS per domain type (ecommerce, game_dev, video_cgi, music, generic).
- Goal anchors every downstream decision. Without it, agents optimize for proxies.

**Open vs Specific Construction**
- **Open construction:** goal is stated but domain is unknown → Goal Layer asks questions → Router selects Toy Box bin dynamically. Works for anything.
- **Specific construction:** domain is declared upfront (`domain="ecommerce"`) → Router loads the right bin immediately. Faster, less friction.
- This is what makes Orlog work for a Shopify store AND a game engine AND a music app.

**Router** (`orlog/brain/router.py` — TO BUILD)
- Reads the goal + domain → selects which lenses apply → selects which Toy Box agents to assemble.
- Currently: all lenses injected always (blunt). Target: per-task lens selection (surgical).
- **Critical gap:** Router has no feedback loop. It selects lenses but never learns if it was wrong.
- Router decisions + outcome labels = DPO training pairs for Phase 2. Build the logging before the logic.

**Cold Start vs Warm Start**
- **Cold Start (Phase 1):** Every call starts fresh. Amplifier injects brain as system prompt. Cost: ~344 tokens per call minimum.
- **Warm Start (Phase 2):** Brain in weights via DPO. Orlog scores become reward signal. No injection, no cold start, no risk of model skimming the prompt.
- All layers above function in both phases. Implementation differs, architecture doesn't.

---

### LAYER 3 — ACT (execution)
*Status: 🔲 Scaffolded, partially functional*

**Amplifier** (`orlog/amplifier/`)
- The cold-start bridge. Injects the right brain tier into every model call.
- Selects tier based on: target model capability, available context, task type.
- Whisper for frontier models. Lite for most cases. Full only if context allows and task demands it.

**Round Table** (`orlog/council/council.py`)
- Multi-agent council. Domain-agnostic. Accepts `project_context` dict at runtime.
- **Critical rule:** agents must bring genuinely different perspectives, not converge on the same reading of the same brain. Individual vs Council reasoning is a real failure mode.
- Deliberate perspective separation: one agent argues for, one against, one asks what's missing.
- Logs to `logs/orlog_council.json` — no CG-specific files.

**Cognitive Style Agents** (Round Table enhancement — TO BUILD)
- Each Round Table seat gets a different cognitive style modifier injected into its system prompt.
- Inspired by neuroscience: neurodiverse teams consistently outperform homogeneous teams on complex problems.
- Styles and their useful traits:
  - **ADHD**: hyperfocus + cross-domain pattern recognition + non-obvious connection finder. Fast, impulsive, high signal. Use for: spotting gaps, generating unexpected links.
  - **Autism**: systems thinking + edge case detection + consensus resistance. Won't cave to what other agents think. Use for: consistency checks, resisting false agreement.
  - **OCD**: systematic verification, completeness guarantee, nothing slips through. Use for: pre-launch audits, Sentinel behavior, error checking.
  - **Dyslexia**: strong spatial/big-picture reasoning (compensatory mechanism). Sees the shape of the problem before the details. Use for: architecture decisions, goal alignment.
  - **Savant**: extraordinary depth in one narrow domain, no generalization needed. Use for: domain specialist bins in the Toy Box.
  - **Paranoid**: adversarial modeling, assumes bad intent, finds attack vectors before they happen. Use for: risk assessment, Crystal Ball, security review.
  - **Synthesizer** (neurotypical): reads disagreement between styles and resolves it. The insight lives in the tension, not the individual positions.
- "Bad" traits are not avoided — they are constrained and directed. Paranoia becomes threat detection. OCD becomes thoroughness. Impulsivity becomes willingness to try the non-obvious move.
- Implementation: `reasoning_style` parameter per agent → injects a 2-3 sentence style modifier into the brain prompt alongside the lite brain.
- **Why this works:** the tension between a paranoid agent and a hyperfocus agent on the same question produces better answers than five neurotypical agents agreeing. The Synthesizer is where the actual insight emerges.

**Toy Box** (`orlog/toybox/`)
- Bins of domain-specific agents. Orlog's plugin library.
- Each bin has an AGENT_PACK: name, description, required env vars, the agent class.
- Current bins: `stores/` (10 e-comm agents), `game_dev/` (6 agents), `generic/` (5 agents)
- Empty bins: `video_cgi/`, `music/`
- Orlog loads the bin, instantiates the agents, passes them to the Round Table.

**Hermes** (`orlog/hermes/` — TO BUILD)
- Research relay. How Orlog reaches outside itself for new information.
- Fetch URLs → extract domain-scoped lessons → feed the synthesis pipeline → update the brain.
- Currently exists as `intelligence/shared/` but it's CG-scoped. Hermes is the domain-agnostic version.
- Inter-agent communication: Hermes is also how agents pass information to each other without the Telephone Problem (one job per layer, original task travels untouched).

---

### LAYER 4 — OBSERVE (what's happening)
*Status: 🔲 Not built*

**Argus** (`orlog/argus/` — TO BUILD)
- Live monitoring of agent behavior while the system runs.
- Named for the 100-eyed Greek titan — sees everything.
- Tracks: which lenses fired, which agents ran, latency, token cost, errors.
- Output feeds Darwin (pruning signal) and Crystal Ball (pattern recognition).

**Sentinel** (`orlog/sentinel/` — TO BUILD)
- Watchdog. Catches failures, triggers self-healing.
- If an agent fails: Sentinel logs it, retries with fallback, escalates if pattern persists.
- If a lens is never used across N runs: Sentinel flags it for Darwin review.
- If Crystal Ball prediction diverges from actual outcome: Sentinel logs the gap.

**Crystal Ball** (`orlog/crystal_ball/` — TO BUILD)
- Outcome forecasting. Predicts whether an action will score well before running it.
- **Current problem:** win_rate=86% is self-reported (circular). Crystal Ball predicts what Orlog will score, not what reality will reward.
- Fix: needs external signal — Shopify revenue, user retention, task completion rates — not just Orlog scores.
- Phase 1: predict Orlog score (useful for filtering bad outputs). Phase 2: predict real-world outcome (requires external signal hookup).

---

### LAYER 5 — LEARN (how it improves)
*Status: 🔲 Concept only, infrastructure exists*

**Orlog Score / Eval** (`orlog/eval/`)
- Every output gets scored 0-10 on 5 dimensions.
- Score is the reward signal. Replaces human labelers in DPO/RLHF.
- Currently: `local_eval.py` runs the eval manually. Target: eval runs automatically on every output.
- 30 eval runs completed. Claude+brain avg: 8.5. Qwen+brain avg: 6.1. Gap: -2.24.

**Cocoon Mode**
- Named phase, not a failure state.
- Qwen is absorbing decision+outcome training pairs. Not ready to emerge yet.
- Metamorphosis condition: enough outcome-labeled decisions to run DPO fine-tuning.
- **DO NOT contaminate with CG-specific logic.** Brain examples reference dropshipping/Shopify — strip before training. Train on skeleton (lenses) not skin (domain examples).
- Data source: `logs/orlog_council.json` + eval logs.

**DPO Pipeline** (TO BUILD)
- Preference pairs: (question, better_answer, worse_answer) where Orlog score determines better/worse.
- LoRA adapter on Qwen2.5-7B (already downloaded, already being evaluated).
- Adapter loaded once — always there. No injection, no cold start.
- Compound loop: better model → higher scores → fewer patches → adapter retrained → compounds.

---

## NAMED CONCEPTS GLOSSARY

| Name | What it is | Layer | Status |
|---|---|---|---|
| **Brain** | 27 reasoning lenses | Think | ✅ Built |
| **Darwin** | Evolutionary lens pruning | Think | 🔲 To build |
| **Deliberation Gate** | Fast vs slow thinking selector | Think | ✅ In brain (Lens 19) |
| **Brain Tiers** | whisper/lite/tier3.../full | Think | ✅ Built |
| **Goal Layer** | End state awareness, clarifying Qs | Orient | 🔲 Scaffolded |
| **Open Construction** | Dynamic Toy Box selection from goal | Orient | 🔲 Scaffolded |
| **Specific Construction** | Declared domain, immediate bin load | Orient | 🔲 Scaffolded |
| **Router** | Lens + agent selector per task | Orient | 🔲 To build |
| **Cold Start** | Brain injected per call (Phase 1) | Orient | ✅ Current state |
| **Warm Start** | Brain in weights via DPO (Phase 2) | Orient | 🔲 Cocoon target |
| **Amplifier** | Brain injector for cold start | Act | 🔲 Scaffolded |
| **Round Table** | Multi-agent council | Act | 🔲 Scaffolded |
| **Toy Box** | Domain agent bins | Act | 🔲 Scaffolded |
| **Hermes** | Research relay + inter-agent comms | Act | 🔲 To build |
| **Argus** | Live monitoring (100 eyes) | Observe | 🔲 To build |
| **Sentinel** | Watchdog + self-healing | Observe | 🔲 To build |
| **Crystal Ball** | Outcome forecasting | Observe | 🔲 Partial (circular) |
| **Orlog Score** | 0-10 eval, the reward signal | Learn | ✅ Running (30 evals) |
| **Cocoon** | DPO training data collection phase | Learn | ✅ Active (collecting) |
| **DPO Pipeline** | Fine-tunes Qwen on preference pairs | Learn | 🔲 To build |

---

## BUILD ORDER (eval-gated)

Each layer must be proven before the next is built. This is not optional — stacking unverified assumptions is how systems fail silently.

```
Phase 0 — PROVEN
  Brain (lite tier) → 8.53 avg score ✅

Phase 1 — ORIENT (next)
  Goal Layer eval: ambiguous goal → right clarifying questions → right Toy Box bin
  Router logging: every lens selection logged with outcome label
  Test: give Orlog 5 different project types, verify it picks the right bin each time

Phase 2 — ACT
  Hermes: domain-agnostic research pipeline (port from intelligence/shared/)
  Round Table cohesion test: can council run with zero Shopify imports?
  Toy Box: fill video_cgi/ and music/ bins

Phase 3 — OBSERVE
  Argus: instrument every agent call with timing + lens + token data
  Sentinel: failure detection + retry logic
  Crystal Ball: fix circular reporting with external signal hookup

Phase 4 — LEARN
  Darwin: build pruning logic from Argus lens-usage data
  Eval auto-run: score every output automatically, not just manual runs
  DPO Pipeline: train LoRA on Cocoon data once contamination is stripped

Phase 5 — WARM START
  Qwen emerges from Cocoon
  Orlog runs on local model, no API cost, brain in weights
```

---

## WHAT MAKES ORLOG, ORLOG

Strip everything domain-specific. What's left:

1. A reasoning framework that asks *why* before *what*
2. A goal layer that knows what it's building toward
3. A multi-agent council with deliberate perspective separation
4. A scoring system that generates its own training signal
5. A pruning mechanism that keeps the brain alive, not just big
6. A phase architecture: cold start now, warm start when ready

Everything else — Shopify agents, CJ connectors, Carter voice — is a Toy Box bin.
Orlog is the engine. The bins are fuel.

---

## TELEPHONE PROBLEM (architectural law)

Every AI layer is a telephone pass — signal degrades.

Rules:
- Each layer has ONE job
- The original task travels through every layer untouched
- Every handoff is auditable (Argus logs it)
- Minimum information per layer, not maximum
- When in doubt: fewer layers, clearer handoffs

Violating this is how systems produce confident nonsense.
