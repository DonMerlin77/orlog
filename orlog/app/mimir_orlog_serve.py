"""
orlog/app/mimir_orlog_serve.py — Deploy Mimir-Orlog v3 as a Modal inference endpoint.

Deploy:
    modal deploy orlog/app/mimir_orlog_serve.py

Endpoint: POST /generate
  {"question": "..."}
  Returns: {"response": "..."}
"""

import modal

app  = modal.App("mimir-orlog-serve")
vol  = modal.Volume.from_name("mimir-checkpoints", create_if_missing=False)

image = (
    modal.Image.debian_slim(python_version="3.11")
    .pip_install(
        "fastapi>=0.110.0",
        "uvicorn>=0.29.0",
        "torch>=2.4.0",
        "transformers>=4.40.0",
        "bitsandbytes>=0.46.1",
        "accelerate>=0.29.0",
        "sentencepiece>=0.1.99",
    )
)

MERGED = "/checkpoints/orlog_v4_merged"

ORLOG_SYS = """You are Orlog — a multi-perspective decision engine that does what a crowd cannot.

When someone brings you a decision, you don't give advice. You surface the structure underneath it:
the competing values, the hidden assumption, the angle everyone else is missing.

Your output always has exactly four sections, in this order:

VERDICT: <One sentence. Actionable. Takes a position. Not hedged.>

KEY TENSION: <The real conflict underneath this question — not the stated one. 8 words or fewer.>

BLIND SPOT: <The thing they're not seeing. What most advice misses. One sentence.>

ACTION:
- <Concrete next step, verb-first, under 15 words>
- <Second step>
- <Third step>

Rules:
- VERDICT must take a side. Not "it depends." A clear recommendation.
- KEY TENSION names the underlying conflict, not the surface trade-off.
- BLIND SPOT calls out the unstated assumption or missing variable — not the obvious risk.
- ACTION steps are specific enough to do tomorrow. No "reflect on your values."
- Total output: under 150 words. Dense and direct.
- No markdown bold (**). Plain text only.

Examples of good KEY TENSION: "security vs autonomy", "loyalty vs self-preservation",
"short-term relief vs long-term regret", "identity vs income", "belonging vs growth"."""


@app.function(
    image=image,
    gpu="T4",
    timeout=180,
    volumes={"/checkpoints": vol},
    min_containers=0,
    max_containers=3,
)
@modal.asgi_app()
def serve():
    import torch
    from fastapi import FastAPI
    from fastapi.responses import JSONResponse
    from transformers import AutoModelForCausalLM, AutoTokenizer, BitsAndBytesConfig

    web = FastAPI(title="Mimir-Orlog", version="4.0")

    import json as _json
    from pathlib import Path as _P

    print("Loading Mimir-Orlog v4...")
    bnb = BitsAndBytesConfig(
        load_in_4bit=True,
        bnb_4bit_quant_type="nf4",
        bnb_4bit_compute_dtype=torch.float16,
        bnb_4bit_use_double_quant=True,
    )

    # Patch extra_special_tokens before loading — transformers 4.40+ requires {str: str}
    tok_cfg = _P(MERGED) / "tokenizer_config.json"
    if tok_cfg.exists():
        cfg = _json.loads(tok_cfg.read_text())
        est = cfg.get("extra_special_tokens")
        if isinstance(est, list):
            cfg["extra_special_tokens"] = {t: t for t in est}
            tok_cfg.write_text(_json.dumps(cfg, indent=2))
        elif isinstance(est, dict) and any(not isinstance(v, str) for v in est.values()):
            cfg["extra_special_tokens"] = {k: k for k in est}
            tok_cfg.write_text(_json.dumps(cfg, indent=2))

    tokenizer = AutoTokenizer.from_pretrained(MERGED, trust_remote_code=True)
    if not getattr(tokenizer, "chat_template", None):
        tokenizer.chat_template = (
            "{% for message in messages %}"
            "{{'<|im_start|>' + message['role'] + '\n' + message['content'] + '<|im_end|>' + '\n'}}"
            "{% endfor %}"
            "{% if add_generation_prompt %}{{ '<|im_start|>assistant\n' }}{% endif %}"
        )

    model = AutoModelForCausalLM.from_pretrained(
        MERGED, quantization_config=bnb, device_map="auto", trust_remote_code=True,
    )
    model.eval()
    print("Mimir-Orlog v4 ready.")

    def _run(system: str, messages: list, max_tokens: int = 350) -> str:
        full_messages = [{"role": "system", "content": system}] + messages
        text   = tokenizer.apply_chat_template(full_messages, tokenize=False, add_generation_prompt=True)
        inputs = tokenizer(text, return_tensors="pt").to(model.device)
        with torch.no_grad():
            out = model.generate(
                **inputs,
                max_new_tokens=max_tokens,
                do_sample=False,
                temperature=None,
                top_p=None,
                pad_token_id=tokenizer.eos_token_id,
            )
        return tokenizer.decode(out[0][inputs["input_ids"].shape[1]:], skip_special_tokens=True).strip()

    @web.get("/health")
    def health():
        return {"status": "ok", "model": "mimir-orlog-v4"}

    @web.post("/generate")
    def generate(body: dict):
        question = body.get("question", "").strip()
        if not question:
            return JSONResponse({"error": "question required"}, status_code=400)
        response = _run(ORLOG_SYS, [{"role": "user", "content": question}])
        return JSONResponse({"response": response})

    @web.post("/evaluate")
    def evaluate(body: dict):
        question = body.get("question", "").strip()
        response = body.get("response", "").strip()
        if not question or not response:
            return JSONResponse({"error": "question and response required"}, status_code=400)

        judge_prompt = f"""You are a quality checker for Orlog responses. Evaluate this response for two failure modes only.

QUESTION: {question}

RESPONSE:
{response}

Check only:
1. COHERENCE — Does the VERDICT recommend one direction but the ACTION steps contradict it?
2. GROUNDING — Does the BLIND SPOT introduce any fact, person, or assumption NOT mentioned in the question?

Reply in this exact format:
COHERENCE: PASS or FAIL — one sentence reason
GROUNDING: PASS or FAIL — one sentence reason"""

        raw = _run(
            "You are a strict quality checker. Be concise and accurate.",
            [{"role": "user", "content": judge_prompt}],
            max_tokens=120,
        )

        failures = []
        for line in raw.split("\n"):
            if line.startswith("COHERENCE:") and "FAIL" in line:
                failures.append({"label": "coherence", "reason": line.split("—", 1)[-1].strip()})
            elif line.startswith("GROUNDING:") and "FAIL" in line:
                failures.append({"label": "grounding", "reason": line.split("—", 1)[-1].strip()})

        return JSONResponse({"pass": len(failures) == 0, "failures": failures, "raw": raw})

    @web.post("/followup")
    def followup(body: dict):
        question      = body.get("question", "").strip()
        prior_verdict = body.get("prior_verdict", "").strip()
        if not question or not prior_verdict:
            return JSONResponse({"error": "question and prior_verdict required"}, status_code=400)

        prompt = f"""PRIOR VERDICT YOU DELIVERED:
{prior_verdict}

USER FOLLOW-UP: {question}

The user has pushed back, added context, or is asking you to go deeper.
Do not repeat the prior verdict. Build on it — revise if new information changes the picture,
hold your ground with new reasoning if it doesn't, or surface a new angle entirely.
Keep the same output format."""

        response = _run(ORLOG_SYS, [{"role": "user", "content": prompt}], max_tokens=400)
        return JSONResponse({"response": response})

    return web
