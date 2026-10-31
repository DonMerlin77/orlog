# How we run Orlog (2026-09-30)

## Brainstorm (the use that earns its keep)

```bash
cd orlog   # this repo's root
PYTHONPATH=. python orlog/brainstorm.py --name npc_voices \
  --goal "what we want to end up with" \
  --question @question.txt          # or the text inline
```

- Prints the synthesis, then EVERY voice's full perspective with the model that spoke it. The voices are the value:
  read them, not just the verdict.
- Saves the whole run to `logs/orlog_runs/<date>_<name>.json`.
- Run it in the FOREGROUND (1-3 min). Backgrounding long Orlog calls and polling stalls (memory:
  feedback_orlog_background_wait_loop).
- A good question names the context, the constraints, and the angles you want (pitfalls, tests, concrete ideas).
  Example: the question in `logs/orlog_runs/2026-09-30_npc_literary_voices.json`.

## Model-diverse voices (Tom's idea, 2026-09-30)

`ORLOG_MODEL_VOICES=1` (brainstorm.py sets it) gives each council voice its own model family on OpenRouter
(`orlog/council/council.py` VOICE_MODELS: Llama 4 Maverick, DeepSeek V3.2, Mistral Large, Qwen3 235B, Gemma 4 31B,
Kimi K2, Command A Plus, Llama 3.3 70B, Phi-4, Gemini 2.5 Flash Lite). Synthesis = Mistral Large, critic = DeepSeek
(a different family from the synthesizer). Synesthesia = Qwen3 235B. A failed call retries once on Mistral Medium 3.1,
then falls back to the free stack. Cost: a few cents per run. Without the flag, Orlog runs on `llm_router.fast_call`
as before.

Why: the Sept fair tests (memory: project_orlog_honest_status) found the council added nothing over one good prompt
because every voice was the SAME model in a costume, so they converged. Different families = errors that don't move
together. **Untested**: re-run `coverage_eval.py` with model-diverse voices vs one good prompt before claiming it helps.

## Gotchas found 2026-09-30

- **Truncated voices** (a perspective that stops after one line): the free stack fell through to `gemini-2.5-flash`,
  a thinking model whose hidden reasoning eats the 400-token budget. Model-diverse mode avoids it.
- **Reasoning leaks**: `nvidia/nemotron-3-super` printed its own reasoning ("We need to answer as ADVOCATE...").
  Keep reasoning/thinking models out of VOICE_MODELS.
- `qwen/qwen3.7-flash` fails on OpenRouter; `x-ai/grok-4-fast` too. Probe a model before adding it.
- Model ids change: list current ones from `https://openrouter.ai/api/v1/models`.

## What Orlog is and isn't (keep honest)

- Decisions: the 20-voice council LOST to one good prompt (~29% vs 69%). For a decision, use one good prompt.
- Coverage: same-model council ~= one good prompt (34% vs 32%). Model-diverse voices are the open test.
- Where it helps: brainstorming, where seeing ten different angles side by side is the point.
