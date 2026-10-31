"""
orlog/app/mimir_gate_serve.py — Deploy Mimir-Gate v1 as a Modal inference endpoint.

Deploy:
    modal deploy orlog/app/mimir_gate_serve.py

Endpoint: POST /classify
  {"text": "..."}
  Returns: {"score": 0-10, "is_decision": true/false, "nudge": "..."}
"""

import modal

app  = modal.App("mimir-gate-serve")
vol  = modal.Volume.from_name("mimir-checkpoints", create_if_missing=False)

image = (
    modal.Image.debian_slim(python_version="3.11")
    .pip_install(
        "fastapi>=0.110.0",
        "uvicorn>=0.29.0",
        "torch>=2.4.0",
        "transformers>=4.40.0",
        "peft>=0.10.0",
        "bitsandbytes>=0.46.1",
        "accelerate>=0.29.0",
        "sentencepiece>=0.1.99",
    )
)

BASE    = "Qwen/Qwen2.5-7B-Instruct"
ADAPTER = "/checkpoints/gate_v1"

GATE_SYS = (
    "You are a confidence gate for a decision-reasoning engine. "
    "Given an input, determine whether it contains a real personal decision the user is trying to make. "
    "Respond with JSON only — no other text:\n"
    '{"score": 0-10, "is_decision": true/false, "nudge": "short question if not a decision, else empty string"}\n'
    "score 0-3: not a decision (news, venting, statements, stories)\n"
    "score 4-6: partial or ambiguous\n"
    "score 7-10: clear personal decision ready for reasoning"
)


@app.function(
    image=image,
    gpu="A10G",
    timeout=180,
    volumes={"/checkpoints": vol},
    min_containers=0,
    max_containers=20,
)
@modal.asgi_app()
def serve():
    import json
    import torch
    from fastapi import FastAPI
    from fastapi.responses import JSONResponse
    from transformers import AutoModelForCausalLM, AutoTokenizer, BitsAndBytesConfig
    from peft import PeftModel

    web = FastAPI(title="Mimir-Gate", version="1.0")

    print("Loading Mimir-Gate v1...")
    bnb = BitsAndBytesConfig(
        load_in_4bit=True,
        bnb_4bit_quant_type="nf4",
        bnb_4bit_compute_dtype=torch.float16,
        bnb_4bit_use_double_quant=True,
    )
    base_tok  = AutoTokenizer.from_pretrained(BASE, trust_remote_code=True)
    tokenizer = AutoTokenizer.from_pretrained(ADAPTER, trust_remote_code=True)
    tokenizer.chat_template = base_tok.chat_template

    model = AutoModelForCausalLM.from_pretrained(
        BASE, quantization_config=bnb, device_map="auto", trust_remote_code=True,
    )
    model.resize_token_embeddings(len(tokenizer))
    model = PeftModel.from_pretrained(model, ADAPTER)
    model.eval()
    print("Mimir-Gate v1 ready.")

    @web.get("/health")
    def health():
        return {"status": "ok", "model": "mimir-gate-v1"}

    @web.post("/classify")
    def classify(body: dict):
        text = body.get("text", "").strip()
        if not text:
            return JSONResponse({"error": "text required"}, status_code=400)

        messages = [
            {"role": "system", "content": GATE_SYS},
            {"role": "user",   "content": text[:600]},
        ]
        prompt = tokenizer.apply_chat_template(messages, tokenize=False, add_generation_prompt=True)
        inputs = tokenizer(prompt, return_tensors="pt").to(model.device)

        with torch.no_grad():
            out = model.generate(
                **inputs,
                max_new_tokens=80,
                do_sample=False,
                temperature=None,
                top_p=None,
                pad_token_id=tokenizer.eos_token_id,
            )

        raw = tokenizer.decode(out[0][inputs["input_ids"].shape[1]:], skip_special_tokens=True).strip()

        try:
            result = json.loads(raw)
        except Exception:
            # fallback if model output isn't clean JSON
            result = {"score": 5, "is_decision": True, "nudge": ""}

        return JSONResponse(result)

    return web
