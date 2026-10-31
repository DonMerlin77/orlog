# Orlog

A decision tool built around a council: you bring a hard question, and instead of one answer you get several
perspectives that argue, a synthesis, and the blind spots. It ran as a web app (orlog.fyi) and as a set of tools the
rest of my projects reused.

## How it works
1. **Loki** asks what you actually want before anything is decided (the goal behind the question).
2. **The council** gives the question to voices with different cognitive styles (skeptic, builder, contrarian, ...).
   A router picks which voices fire.
3. **Cross-examination:** voices challenge the draft synthesis. A position that holds stays; one that changes is
   recorded, along with whether it changed because of evidence or because of pressure.
4. **The verdict** commits to an answer and keeps the reasons and the blind spots.

`orlog/core.py` ties the layers together: Brain (reasoning lenses), GoalLayer, RoundTable (the council), Hermes
(research), Argus (code scanning), Sentinel, CrystalBall (forecasting), Router (logs decisions for training).
`orlog/brainstorm.py` runs a council from the command line; `HOW_TO_RUN.md` has the commands.

## What testing showed (the honest part)
- **On answer quality, the council ties one good prompt.** A 20-voice council lost to a single well-written prompt
  on the same model; routed 3-voice councils didn't reliably beat it either. On "surface every angle," even the union
  of six voices covered no more than one good prompt: voices on the same model converge.
- **What it's actually good for:** turning a vague question into good-prompt-quality analysis for people who don't
  write prompts, and keeping the decision legible (who argued what, what changed, why).
- **Not tested yet:** its original purpose, resisting sycophancy (holding a position under pressure from the user
  while still changing on real evidence). That's the open question.
- The cross-examination step was later used to generate training data; that line of work is written up honestly in
  [dv-dpo](https://github.com/DonMerlin77/dv-dpo).

## Running it
Python 3.11+. Copy `.env.example` to `.env` and add any of the free-tier provider keys (Groq, Cerebras, NVIDIA,
Gemini, OpenRouter); `llm_router.py` falls back between them. Some modules (`orlog/app`, the Mimir training hooks)
depend on a hosted setup (Modal, Clerk, Stripe) or on code that isn't in this repo, so treat those as reference.

User data, logs and session history from the live site are not included.
