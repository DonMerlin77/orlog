# Orlog

A decision tool built around a council: you bring a hard question, and instead of one answer you get several
perspectives that argue, a synthesis, and the blind spots. It ran as a web app (orlog.fyi) and as a set of tools the
rest of my projects reused.

## How it works
1. **Loki** (in the web app) asks what you actually want before anything is decided: the goal behind the question.
2. **Intake** translates the question as asked into the underlying question.
3. **The wheel** scores all 19 cognitive-style voices (skeptic, builder, contrarian, ...) for relevance and picks the
   top 9. They answer in parallel, optionally each on a different model family.
4. **Synesthesia** reads every perspective and translates across domains; **the Socratic agent** names the one
   foundational question the council failed to ask; opposing voice pairs are flagged as the tensions to resolve.
5. **Synthesis**, then **a critic** looks for the single biggest flaw, and the synthesis is revised unless the critic
   finds it strong. The verdict keeps the reasons and the blind spots.
6. The run is scored against a written constitution and logged, so routing and training can learn from it.

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

## Related
- [orlog-mcp](https://github.com/DonMerlin77/orlog-mcp): how Orlog was exposed to Claude as an MCP server (the hosted
  service is offline).
- [dv-dpo](https://github.com/DonMerlin77/dv-dpo): training a model on the council's cross-examinations, and why it
  didn't hold up.
