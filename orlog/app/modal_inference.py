"""
orlog/app/modal_inference.py — Warm Modal inference endpoint for dpo_v4.

Deploy:
    modal deploy orlog/app/modal_inference.py

Call from orlog app via function lookup:
    fn = modal.Function.lookup("orlog-inference", "infer")
    result = fn.remote(question)
"""

import os
from pathlib import Path
from dotenv import load_dotenv

load_dotenv(Path(__file__).parent / ".env", override=False)

import modal

app = modal.App("orlog-inference")

image = (
    modal.Image.debian_slim(python_version="3.11")
    .pip_install(
        "torch>=2.4.0",
        "transformers>=4.40.0",
        "peft>=0.10.0",
        "accelerate>=0.29.0",
        "bitsandbytes>=0.43.0",
        "sentencepiece>=0.1.99",
        "tiktoken>=0.7.0",
        "python-dotenv>=1.0.0",
    )
)

volume = modal.Volume.from_name("mimir-checkpoints", create_if_missing=False)
CHECKPOINT_MOUNT = Path("/checkpoints")

SYSTEM = (
    "You are Mimir, a reasoning synthesizer. "
    "When given a decision or situation, reason through it carefully: "
    "identify the single load-bearing assumption the situation rests on, "
    "the two forces in conflict, "
    "what information is missing before a decision can be made, "
    "and what breaks first if the core assumption is wrong. "
    "End with one falsifiable verdict — what to do and why. "
    "Be specific to this situation. Name real things. No hedging."
)


@app.function(
    image=image,
    gpu="A10G",
    timeout=120,
    volumes={str(CHECKPOINT_MOUNT): volume},
    memory=32768,
    min_containers=0,
)
def infer(question: str) -> str:
    import torch
    model, tok = _load_model()

    messages = [
        {"role": "system", "content": SYSTEM},
        {"role": "user",   "content": question},
    ]
    text   = tok.apply_chat_template(messages, tokenize=False, add_generation_prompt=True)
    inputs = tok(text, return_tensors="pt").to(model.device)

    with torch.no_grad():
        out = model.generate(
            **inputs,
            max_new_tokens=500,
            temperature=0.3,
            do_sample=True,
            pad_token_id=tok.eos_token_id,
        )
    return tok.decode(out[0][inputs["input_ids"].shape[1]:], skip_special_tokens=True)


def _load_model():
    import torch
    from transformers import AutoTokenizer, AutoModelForCausalLM, BitsAndBytesConfig
    from peft import PeftModel

    MODEL_ID  = "Qwen/Qwen2.5-7B-Instruct"
    ADAPTER   = str(CHECKPOINT_MOUNT / "dpo_v5")
    CANON_TOK = str(CHECKPOINT_MOUNT / "lora_v2")

    bnb = BitsAndBytesConfig(
        load_in_4bit=True,
        bnb_4bit_compute_dtype=torch.bfloat16,
        bnb_4bit_use_double_quant=True,
        bnb_4bit_quant_type="nf4",
    )
    base_tok = AutoTokenizer.from_pretrained(MODEL_ID, trust_remote_code=True)
    try:
        tok = AutoTokenizer.from_pretrained(CANON_TOK, trust_remote_code=True)
    except Exception:
        tok = base_tok
    if not tok.chat_template and base_tok.chat_template:
        tok.chat_template = base_tok.chat_template

    model = AutoModelForCausalLM.from_pretrained(
        MODEL_ID, quantization_config=bnb, device_map="auto", trust_remote_code=True,
    )
    if model.config.vocab_size != len(tok):
        model.resize_token_embeddings(len(tok))
    model = PeftModel.from_pretrained(model, ADAPTER)
    model.eval()
    return model, tok


TRANSLATE_SYSTEM = (
    "You are a plain-English translator for a reasoning trace. "
    "A reasoning trace uses four rune operations (Anchor, Tension, Gap, Risk) and ends with a Verdict. "
    "Translate the trace into 3-4 plain sentences a non-expert can act on immediately. "
    "Do not use the rune names or technical labels. Just explain what was found and what to do. "
    "Be direct. No hedging."
)


@app.function(
    image=image,
    gpu="A10G",
    timeout=120,
    volumes={str(CHECKPOINT_MOUNT): volume},
    memory=32768,
    min_containers=0,
)
def translate(trace: str) -> str:
    import torch
    model, tok = _load_model()

    messages = [
        {"role": "system", "content": TRANSLATE_SYSTEM},
        {"role": "user",   "content": trace},
    ]
    text   = tok.apply_chat_template(messages, tokenize=False, add_generation_prompt=True)
    inputs = tok(text, return_tensors="pt").to(model.device)

    with torch.no_grad():
        out = model.generate(
            **inputs,
            max_new_tokens=200,
            temperature=0.2,
            do_sample=True,
            pad_token_id=tok.eos_token_id,
        )
    return tok.decode(out[0][inputs["input_ids"].shape[1]:], skip_special_tokens=True)
