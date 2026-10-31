"""
orlog/app/mimir_loki_serve.py — Deploy Mimir-Loki v2 as a Modal inference endpoint.

Deploy:
    modal deploy orlog/app/mimir_loki_serve.py

Endpoint: POST /generate
  {"messages": [...], "system": "..."}
  Returns: {"response": "..."}
"""

import modal
from pathlib import Path

app  = modal.App("mimir-loki-serve")
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
ADAPTER = "/checkpoints/loki_v2"


@app.function(
    image=image,
    gpu="A10G",
    timeout=120,
    volumes={"/checkpoints": vol},
    min_containers=0,
    max_containers=20,
)
@modal.asgi_app()
def serve():
    import torch
    from fastapi import FastAPI
    from fastapi.responses import JSONResponse
    from transformers import AutoModelForCausalLM, AutoTokenizer, BitsAndBytesConfig
    from peft import PeftModel

    web = FastAPI(title="Mimir-Loki", version="3.0")

    print("Loading Mimir-Loki v2...")
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
    print("Mimir-Loki v2 ready.")

    @web.get("/health")
    def health():
        return {"status": "ok", "model": "mimir-loki-v2"}

    @web.post("/generate")
    def generate(body: dict):
        messages = body.get("messages", [])
        system   = body.get("system", "")

        if system:
            full_messages = [{"role": "system", "content": system}] + messages
        else:
            full_messages = messages

        text   = tokenizer.apply_chat_template(full_messages, tokenize=False, add_generation_prompt=True)
        inputs = tokenizer(text, return_tensors="pt").to(model.device)

        with torch.no_grad():
            out = model.generate(
                **inputs,
                max_new_tokens=80,
                do_sample=False,
                temperature=None,
                top_p=None,
                pad_token_id=tokenizer.eos_token_id,
            )

        response = tokenizer.decode(out[0][inputs["input_ids"].shape[1]:], skip_special_tokens=True).strip()
        return JSONResponse({"response": response})

    return web
