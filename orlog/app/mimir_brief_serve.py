"""
orlog/app/mimir_brief_serve.py — Brief card generation on Qwen2.5-1.5B, CPU-based.

Takes a Loki conversation transcript + surfaced goal, returns a brief card:
  {question, tension, summary}

No GPU needed at serve time. LoRA adapter on 1.5B base.

Deploy:
    modal deploy orlog/app/mimir_brief_serve.py

Train the adapter first:
    python3 mimir/reddit_brief_pairs.py --count 200
    modal run mimir/modal_train.py::run_brief_sft
"""

import json
import modal

app = modal.App("mimir-brief")
vol = modal.Volume.from_name("mimir-checkpoints", create_if_missing=False)

image = (
    modal.Image.debian_slim(python_version="3.11")
    .pip_install(
        "fastapi>=0.110.0",
        "uvicorn>=0.29.0",
        "torch>=2.4.0",
        "transformers>=4.40.0",
        "peft>=0.10.0",
        "accelerate>=0.29.0",
        "sentencepiece>=0.1.99",
    )
)

BASE           = "Qwen/Qwen2.5-1.5B-Instruct"
BRIEF_ADAPTER  = "/checkpoints/brief_v1/adapter"

BRIEF_SYS = (
    "You are a brief card generator. Given a conversation transcript and a surfaced goal, "
    "output a brief card as JSON with these exact fields: "
    "question (the real decision in one plain sentence), "
    "tension (core tension in 8 words or fewer), "
    "summary (2-3 sentences: what they started with, what shifted, what got named). "
    "Output ONLY valid JSON. No preamble, no explanation."
)


@app.function(
    image=image,
    cpu=2,
    memory=3072,
    timeout=90,
    volumes={"/checkpoints": vol},
    min_containers=0,
    max_containers=30,
)
@modal.concurrent(max_inputs=100)
@modal.asgi_app()
def serve():
    import os
    import torch
    from fastapi import FastAPI
    from fastapi.responses import JSONResponse
    from transformers import AutoModelForCausalLM, AutoTokenizer
    from peft import PeftModel

    web = FastAPI(title="Mimir-Brief", version="1.0")

    adapter_ready = os.path.exists(BRIEF_ADAPTER)

    if not adapter_ready:
        print("[brief] Adapter missing — run: modal run mimir/modal_train.py::run_brief_sft")

        @web.get("/health")
        def health_down():
            return {"status": "adapter_missing", "adapter": BRIEF_ADAPTER}

        @web.post("/generate")
        def generate_down(body: dict):
            return JSONResponse({"error": "brief adapter not trained yet"}, status_code=503)

        return web

    print("[brief] Loading base model + brief adapter (CPU, float16)...")

    tok = AutoTokenizer.from_pretrained(BASE, trust_remote_code=True)

    base_model = AutoModelForCausalLM.from_pretrained(
        BASE,
        torch_dtype=torch.float16,
        device_map="cpu",
        trust_remote_code=True,
    )

    model = PeftModel.from_pretrained(base_model, BRIEF_ADAPTER, adapter_name="brief")
    print("[brief] Adapter loaded. Ready.")

    def _generate(conversation: str, goal: str) -> str:
        user_text = (
            f"LOKI CONVERSATION:\n{conversation[:700]}\n\n"
            f"GOAL EXTRACTED: {goal[:300]}\n\n"
            "Write the brief card as JSON:"
        )
        messages = [
            {"role": "system", "content": BRIEF_SYS},
            {"role": "user",   "content": user_text},
        ]
        prompt = tok.apply_chat_template(messages, tokenize=False, add_generation_prompt=True)
        inputs = tok(prompt, return_tensors="pt")
        with torch.no_grad():
            out = model.generate(
                **inputs,
                max_new_tokens=200,
                do_sample=False,
                temperature=None,
                top_p=None,
                pad_token_id=tok.eos_token_id,
            )
        return tok.decode(out[0][inputs["input_ids"].shape[1]:], skip_special_tokens=True).strip()

    @web.get("/health")
    def health():
        return {"status": "ok", "model": "mimir-brief-v1", "base": BASE}

    @web.post("/generate")
    def generate(body: dict):
        conversation = body.get("conversation", "").strip()
        goal         = body.get("goal", "").strip()
        if not goal:
            return JSONResponse({"error": "goal required"}, status_code=400)

        raw = _generate(conversation, goal)

        # Strip markdown fences if present
        if raw.startswith("```"):
            parts = raw.split("```")
            raw = parts[1].lstrip("json").strip() if len(parts) > 1 else raw

        try:
            card = json.loads(raw)
            if not all(k in card for k in ("question", "tension", "summary")):
                raise ValueError("missing fields")
            return JSONResponse(card)
        except Exception:
            # Fallback: return goal as question with empty tension/summary
            return JSONResponse({"question": goal, "tension": "", "summary": "", "raw": raw[:200]})
