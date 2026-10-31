# Orlog — Unified Architecture (the six-layer stack)

> The plan for connecting every piece we have into one system, built **one layer at a time**, where
> each layer exposes a clean **seam** so the next one drops onto it without a rewrite. This is the
> target; `ARCHITECTURE.md` is the current system. Build in the order at the bottom, not all at once.

## The thesis (the novel thing, in one line)
A **sparse routed council of interpretable persona-experts that deliberate**, with a **calibrated
hold/revise dial**, running on **deterministic integer inference**. Nobody has built this. It's the
superset of MoE — MoE routes to experts and *sums* them; Orlog routes to voices and *lets them argue*.
That interaction is where reasoning lives, and it's exactly what a weighted sum can't produce.

Why it can be small + fast + good at once: knowledge is offloaded (retrieval), only a few voices fire
per decision (sparse), the base is quantized + integer (fast, deterministic), and the one thing kept
expensive — deliberation — is the one thing that actually needs judgment.

## The stack

| # | Layer | Role | Status | Gate (what it needs) |
|---|---|---|---|---|
| 1 | **Calibration** | DV-DPO hold/revise dial — combat agreeableness without stubbornness | eval validated; sweep next | nothing — UNBLOCKED |
| 2 | **Decode** | damped greedy — no loops, better free-form on a small model | testing now | damped-greedy eval result |
| 3 | **Inference** | Mimir quantized on SuperSLM — speed + bit-identical determinism | convert pipeline built | a *trained* Mimir checkpoint (needs L1) |
| 4 | **Memory** | SuperFAISS + top-k — knowledge offloaded so the model stays small | external, in planning | SuperEmbedder (Tom now collaborator) |
| 5 | **Voices** | council personas as runtime-LoRA adapters — the "MoE", but interpretable + swappable | design only | Mimir trained + persona adapters + SuperSLM adapter-switch (exists) |
| 6 | **Deliberation** | council cross-exam + synthesis — the reasoning MoE-sum can't do | **built today** (cloud) | migrate backend from API → the L5 adapter council |

## Each layer, with the seam it leaves open

### 1. Calibration — `mimir/` (DV-DPO)
Trains the base's hold/revise operating point. The current, honest verdict: DV-DPO shifts conservatism,
and genuine discrimination needs ~7B (the 3B→7B cliff). The immediate work is the **pressure:evidence
ratio sweep** — sweep the ratio in the training pairs, watch the hold↔revise dial on the counterfactual
eval, find the setpoint that stops caving without becoming a mule.
- **Seam out:** a trained checkpoint + the **hardened eval as the acceptance gate** (`reasoning-assay`,
  gate + probe self-test). Any base from any later change must pass the same gate. That's the contract.

### 2. Decode — `aioos/superslm_modal.py` (damped greedy)
A decode-time setting on SuperSLM (`--decode-mode damped-greedy`, ruled defaults a=2/n=2/k=6). Kills
degenerate loops and reduces early over-commit on small models. Being measured against greedy now.
- **Seam out:** a decode config that rides *any* `.sslm` artifact. Independent of which model/adapter is
  loaded, so it composes with every later layer for free.

### 3. Inference — `aioos/superslm_modal.py` + `aioos/engine/decision.py`
Mimir converted to `.sslm` and run through SuperSLM's integer path (deterministic across backends). The
conversion + differential-verify harness exists; what's missing is a *trained* Mimir to convert (L1).
- **Seam out:** the `superslm` **mind backend** already stubbed in `decision.py` behind the same call
  signature as the `cloud` backend, with the decision **schema** (`aioos/engine/schema.py`) threaded
  through. So swapping cloud→local is a backend flip, not a rewrite. This seam is already open.

### 4. Memory — SuperFAISS + SuperEmbedder
Holds context/belief state in named channels; top-k retrieval feeds the relevant channel to the active
voice so the model doesn't have to *know* the world, just reason over what's retrieved.
- **Seam out:** a retrieval interface `retrieve(query, channel) -> top_k context` that the deliberation
  orchestrator calls before a voice runs. Keep it behind one function so today's hosted-API retrieval
  and tomorrow's local SuperFAISS are interchangeable. (The goal: decision-model memory fully local
  instead of on a hosted API.)

### 5. Voices — persona-LoRA adapter council (`orlog/council/styles.py` → adapters)
The council's cognitive styles become **LoRA adapters** on one shared Mimir base, switched at runtime
(runtime adapter swap, even mid-generation). Router picks which voices fire (sparse, like MoE top-k);
each voice is an adapter, not a separate model. Interpretable specialists, near-free to switch.
- **Seam out:** a `Voice` interface `run(prompt, context) -> position` that's identical whether the
  voice is a cloud persona-prompt (today) or a local adapter (later). `styles.py` defines the roster;
  the backend behind each voice is swappable.

### 6. Deliberation — `orlog/council/council.py` (+ `socratic.py`, `synesthesia.py`, `intake.py`)
The part that already works: voices cross-examine, Socratic pass exposes the unstated assumption,
synthesis renders a verdict. This is the differentiator and it's **built today**, running on cloud
models. Migration = point its per-voice calls at the L5 adapter council instead of the multi-provider API.
- **Seam out:** the orchestrator is **backend-agnostic by construction** — it calls the `Voice`
  interface (L5) and the `retrieve` interface (L4), so migrating the substrate underneath it changes
  nothing about the deliberation logic on top.

## Build order (one at a time, each leaving its seam open)
1. **Calibration sweep (NOW)** — pressure:evidence sweep, find the persistence setpoint on the validated
   eval. Unblocked, cheap, and it's the layer everything sits on. → leaves a checkpoint + gate contract.
2. **Fold in damped greedy** — once the eval result lands, lock the decode config. → composes with all.
3. **Train Mimir on the swept recipe** — this is the one that trips the **Popper falsification pass**
   (training a new version on changed data). Run it. → produces the checkpoint L3 needs.
4. **Local inference** — convert the trained Mimir → `.sslm`, flip the `superslm` mind backend on. The
   seam's already there; this is wiring, not design.
5. **SuperFAISS memory** — as SuperEmbedder matures (Tom's collaboration), put retrieval behind the
   `retrieve` seam and pull memory local.
6. **Adapter council + deliberation migration** — train persona adapters, route + switch them on one
   base, point the existing deliberation orchestrator at them. This is the whole thing assembled.

## Design principles (why this stays clean)
- **Every layer sits behind one interface** (`Voice.run`, `retrieve`, the mind backend, the eval gate).
  Build and test each in isolation; swap substrates without touching the layer above.
- **Determinism end to end** — integer inference + damped greedy + no sampling randomness, so the
  council's diversity comes from *real different voices at temp 0*, not from dice. Reproducible.
- **Knowledge vs reasoning stays split** — retrieval/adapters carry knowledge; deliberation + the
  calibrated dial carry reasoning. Never make the model memorize what a lookup can hand it.
- **Deliberation is the one expensive thing, and it's the only thing that earns its cost.** Everything
  else is made cheap (sparse, quantized, offloaded) so the budget goes where judgment actually happens.

## Not in this stack (parked, on purpose)
- "Fully dressed meaning" / rune reasoning-mode tokens — research side-track, not on the critical path.
- Token-level MoE *inside* Mimir — no reasoning benefit (established); the "MoE" here is the adapter
  council at L5, not a second mechanism bolted into the base.
