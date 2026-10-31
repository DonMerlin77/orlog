"""
orlog/app/mimir_classifier_serve.py — Gate + Loki extract on Qwen2.5-1.5B, CPU-based.

One endpoint, two adapters, no GPU. Handles both gate classification and goal
extraction in a single call — replaces two separate A10G cold starts with one
always-warm CPU container.

Deploy:
    modal deploy orlog/app/mimir_classifier_serve.py

Endpoint: POST /classify
  {"text": "..."}
  Returns:
    {
      "is_decision": true/false,
      "score": 0-10,
      "nudge": "...",           # if not a decision
      "clean": "..."            # reframed question (only if is_decision=true)
    }

Train the adapters first:
    modal run mimir/modal_train.py::run_gate_small
    modal run mimir/modal_train.py::run_loki_small
"""

import modal

app = modal.App("mimir-classifier")
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

BASE                   = "Qwen/Qwen2.5-1.5B-Instruct"
GATE_ADAPTER           = "/checkpoints/gate_v1_small"
LOKI_ADAPTER           = "/checkpoints/loki_v1_small"
ANSWERABILITY_ADAPTER  = "/checkpoints/answerability_v1_small"
WYRD_ADAPTER           = "/checkpoints/wyrd_v1_small"

GATE_SYS = (
    "You are a confidence gate for a decision-reasoning engine. "
    "Given an input, determine whether it contains a real personal decision. "
    "Respond with JSON only:\n"
    '{"score": 0-10, "is_decision": true/false, "nudge": "short question if not a decision, else empty string"}\n'
    "score 0-3: not a decision  score 4-6: partial  score 7-10: clear decision"
)

LOKI_SYS = (
    "You are a goal-extraction layer. Given a user's raw input, restate their actual decision "
    "as one clear, specific sentence. If already clear, return it with minimal changes. "
    "Output only the restatement. No preamble."
)

ANSWERABILITY_SYS = (
    "You are an answerability gate for a decision-reasoning engine. "
    "Given a question, determine if it is specific enough to produce a concrete recommendation. "
    "Respond with JSON only:\n"
    '{"answerable": true/false, "reason": "one sentence", "needs": "what is missing if unanswerable, else empty string"}\n'
    "answerable=true: has enough context to take a side. answerable=false: too vague, speculative, or missing critical context."
)

WYRD_SYS = (
    "You are a signal extractor for a decision memory system. "
    "Given a question and verdict, extract structured signal in JSON.\n\n"
    "Respond with ONLY a JSON object — no explanation, no markdown:\n"
    '{"topic": "<business_build|relationship|mental_health|creative|learning|identity|financial|other>", '
    '"heat": <float 0.0-1.0>, '
    '"resolved": <true/false>, '
    '"tension": "<core tension in 8 words or fewer>", '
    '"recurring": <true/false>, '
    '"anchor": "<standout phrase or empty string>"}'
)


@app.function(
    image=image,
    cpu=2,
    memory=4096,
    timeout=120,
    volumes={"/checkpoints": vol},
    min_containers=0,
    max_containers=50,
)
@modal.concurrent(max_inputs=200)
@modal.asgi_app()
def serve():
    import json
    import torch
    from fastapi import FastAPI
    from fastapi.responses import JSONResponse
    from transformers import AutoModelForCausalLM, AutoTokenizer
    from peft import PeftModel

    web = FastAPI(title="Mimir-Classifier", version="1.0")

    import os
    gate_ready           = os.path.exists(GATE_ADAPTER)
    loki_ready           = os.path.exists(LOKI_ADAPTER)
    answerability_ready  = os.path.exists(ANSWERABILITY_ADAPTER)
    wyrd_ready           = os.path.exists(WYRD_ADAPTER)

    if not gate_ready or not loki_ready:
        print(f"[classifier] Adapters missing — gate:{gate_ready} loki:{loki_ready} answerability:{answerability_ready}")
        print("[classifier] Run: modal run mimir/modal_train.py::run_gate_small && run_loki_small")

        @web.get("/health")
        def health_down():
            return {"status": "adapters_missing", "gate": gate_ready, "loki": loki_ready}

        @web.post("/classify")
        def classify_down(body: dict):
            return JSONResponse({"error": "adapters not trained yet"}, status_code=503)

        return web

    print("[classifier] Loading base model + adapters (CPU, float16)...")

    tok = AutoTokenizer.from_pretrained(BASE, trust_remote_code=True)

    base_model = AutoModelForCausalLM.from_pretrained(
        BASE,
        torch_dtype=torch.float16,
        device_map="cpu",
        trust_remote_code=True,
    )

    # Load all adapters onto the same base model using named adapters
    model = PeftModel.from_pretrained(base_model, GATE_ADAPTER, adapter_name="gate")
    model.load_adapter(LOKI_ADAPTER, adapter_name="loki")
    if answerability_ready:
        model.load_adapter(ANSWERABILITY_ADAPTER, adapter_name="answerability")
    if wyrd_ready:
        model.load_adapter(WYRD_ADAPTER, adapter_name="wyrd")
    adapters = ["gate", "loki"] + (["answerability"] if answerability_ready else []) + (["wyrd"] if wyrd_ready else [])
    print(f"[classifier] Adapters loaded: {', '.join(adapters)}. Ready.")

    def _run(adapter_name, system, user_text, max_tokens=80):
        model.set_adapter(adapter_name)
        messages = [
            {"role": "system", "content": system},
            {"role": "user",   "content": user_text[:600]},
        ]
        prompt = tok.apply_chat_template(messages, tokenize=False, add_generation_prompt=True)
        inputs = tok(prompt, return_tensors="pt")
        with torch.no_grad():
            out = model.generate(
                **inputs,
                max_new_tokens=max_tokens,
                do_sample=False,
                temperature=None,
                top_p=None,
                pad_token_id=tok.eos_token_id,
            )
        return tok.decode(out[0][inputs["input_ids"].shape[1]:], skip_special_tokens=True).strip()

    @web.get("/health")
    def health():
        return {"status": "ok", "model": "mimir-classifier-v1", "base": BASE}

    @web.post("/classify")
    def classify(body: dict):
        text = body.get("text", "").strip()
        if not text:
            return JSONResponse({"error": "text required"}, status_code=400)

        # Stage 1: Gate
        gate_raw = _run("gate", GATE_SYS, text)
        try:
            gate = json.loads(gate_raw)
        except Exception:
            gate = {"score": 5, "is_decision": True, "nudge": ""}

        score       = gate.get("score", 5)
        is_decision = gate.get("is_decision", True)
        nudge       = gate.get("nudge", "")

        if not is_decision or score < 5:
            return JSONResponse({"is_decision": False, "score": score, "nudge": nudge, "clean": None})

        # Stage 2: Answerability (only if gate passes + adapter trained)
        if answerability_ready:
            ans_raw = _run("answerability", ANSWERABILITY_SYS, text, max_tokens=80)
            try:
                ans = json.loads(ans_raw)
            except Exception:
                ans = {"answerable": True, "reason": "", "needs": ""}
            if not ans.get("answerable", True):
                needs = ans.get("needs", "")
                nudge = f"Orlog needs more to take a side. {needs}".strip() if needs else "Can you add what's specifically at stake for you here?"
                return JSONResponse({"is_decision": True, "score": score, "nudge": nudge, "clean": None, "answerable": False})

        # Stage 3: Loki extract
        clean = _run("loki", LOKI_SYS, text, max_tokens=60)
        return JSONResponse({"is_decision": True, "score": score, "nudge": "", "clean": clean or text, "answerable": True})

    @web.post("/extract_wyrd")
    def extract_wyrd(body: dict):
        if not wyrd_ready:
            return JSONResponse({"error": "wyrd adapter not trained yet"}, status_code=503)
        text = body.get("text", "").strip()
        if not text:
            return JSONResponse({"error": "text required"}, status_code=400)
        raw = _run("wyrd", WYRD_SYS, text, max_tokens=120)
        try:
            start = raw.find("{"); end = raw.rfind("}") + 1
            signal = json.loads(raw[start:end]) if start >= 0 else {}
        except Exception:
            signal = {}
        return JSONResponse(signal if signal else {"error": "parse_failed", "raw": raw[:200]})

    return web
