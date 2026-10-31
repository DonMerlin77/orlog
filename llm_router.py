"""
llm_router.py — shared LLM routing layer for all agents, mimir, and orlog.

Replaces direct Anthropic Haiku/Sonnet calls with free-tier providers:
  fast_call()    → Groq 8B → Cerebras Gemma 31B → Gemini → OpenRouter free tier
  quality_call() → Groq 70B → NVIDIA 550B → Gemini → OpenRouter free tier
  deep_call()    → NVIDIA 550B → Groq 70B → Gemini → OpenRouter free tier

OpenRouter is last-resort insurance: one account routing to dozens of free
open-weight models on infrastructure independent of the other four providers,
so a same-day quota exhaustion on Groq+Cerebras+Gemini (which happened once,
see git blame) doesn't stall an entire run. Needs OPENROUTER_API_KEY in .env.

Usage:
    from llm_router import fast_call, quality_call, deep_call
    text = fast_call(system_prompt, user_prompt, max_tokens=400)
    text = quality_call(system_prompt, user_prompt, max_tokens=1000)
    text = deep_call(system_prompt, user_prompt, max_tokens=2000)

Both return a plain string. No message objects.

Files intentionally left on Claude (customer-facing or interactive):
  orlog/app/main.py         — live product responses
  agents/listing_agent.py   — product copy
  agents/email_agent.py     — customer emails
  agents/support_agent.py   — customer support
  agents/council.py         — interactive strategy tool
  agents/oracle_agent.py    — interactive strategy tool
  agents/mirror.py          — interactive strategy tool
"""

import os
import threading
import time
from collections import deque
from pathlib import Path
from dotenv import load_dotenv

load_dotenv(Path(__file__).parent / ".env")

# ── Groq thin client (no dependency on mimir.groq_client) ────────────────────

# 2026-08: this Groq key's account serves gpt-oss / qwen, not the Llama line.
# Check `Groq().models.list()` before changing — asking for an unavailable
# model 404s and silently falls back to a slower provider.
_GROQ_QUALITY = "openai/gpt-oss-120b"
_GROQ_FAST    = "openai/gpt-oss-20b"

_groq_client      = None
_groq_client_lock = threading.Lock()

def _get_groq():
    global _groq_client
    if _groq_client is None:
        with _groq_client_lock:
            if _groq_client is None:
                from groq import Groq
                _groq_client = Groq(api_key=os.getenv("GROQ_API_KEY"), timeout=30.0)
    return _groq_client

_groq_times_fast    = deque()
_groq_times_quality = deque()
_groq_lock_fast     = threading.Lock()
_groq_lock_quality  = threading.Lock()
_GROQ_RPM_FAST      = 28   # 30 RPM limit, stay safe
_GROQ_RPM_QUALITY   = 28

# Sticky provider cooldowns (mirrors mimir/inference_client.py's _DOWN_UNTIL
# pattern, which was never applied here). Found live 2026-07-10: a single
# Orlog council run (19 sequential voice calls, each hitting _groq_call first)
# took over an hour and never finished. Root cause: _groq_call retries 5x on
# rate-limit with 30-60s sleeps -- up to 4 minutes PER CALL when persistently
# rate-limited, which it was after a night of heavy cross-script Groq usage.
# 19 calls x ~4min each is ~76 minutes, matching the observed stall almost
# exactly. Without a cooldown, every subsequent call re-discovers the same
# outage from scratch instead of remembering it for a while.
_DOWN_UNTIL: dict[str, float] = {}
_down_lock = threading.Lock()


def _mark_down(name: str, cooldown: float):
    with _down_lock:
        _DOWN_UNTIL[name] = time.time() + cooldown


def _is_down(name: str) -> bool:
    return time.time() < _DOWN_UNTIL.get(name, 0)


def _groq_wait(times: deque, rpm: int):
    now = time.time()
    while len(times) >= rpm:
        oldest = times[0]
        wait = 60 - (now - oldest)
        if wait > 0:
            time.sleep(wait + 0.1)
            now = time.time()
        times.popleft()


def _groq_call(name: str, model: str, lock: threading.Lock, times: deque, rpm: int,
               system: str, user: str, max_tokens: int) -> str | None:
    if _is_down(name):
        return None
    for attempt in range(5):
        # Lock only the rate-limit window bookkeeping -- never the HTTP request or the backoff sleep.
        # Holding it across either serializes all parallel callers (e.g. Orlog council's 19 voices)
        # behind one in-flight request.
        with lock:
            _groq_wait(times, rpm)
            times.append(time.time())
        try:
            kwargs = dict(
                model=model,
                messages=[
                    {"role": "system", "content": system},
                    {"role": "user",   "content": user},
                ],
                max_tokens=max_tokens,
                temperature=0.3,
            )
            # gpt-oss is a reasoning model: without a low effort it burns the
            # token budget on hidden reasoning and returns empty content.
            if model.startswith("openai/gpt-oss"):
                kwargs["reasoning_effort"] = "low"
            r = _get_groq().chat.completions.create(**kwargs)
            return (r.choices[0].message.content or "").strip()
        except Exception as e:
            msg = str(e)
            if "tokens per day" in msg.lower() or "tpd" in msg.lower():
                _mark_down(name, 6 * 3600)
                return None
            if "429" in msg or "rate_limit" in msg.lower() or "503" in msg:
                with lock:
                    times.clear()
                wait = 30 if attempt < 2 else 60
            else:
                return None
        time.sleep(wait)
    _mark_down(name, 120)
    return None


# ── Provider functions ────────────────────────────────────────────────────────

def _try_groq_fast(system: str, user: str, max_tokens: int) -> str | None:
    return _groq_call("groq_fast", _GROQ_FAST, _groq_lock_fast, _groq_times_fast,
                      _GROQ_RPM_FAST, system, user, max_tokens)


def _try_groq_quality(system: str, user: str, max_tokens: int) -> str | None:
    return _groq_call("groq_quality", _GROQ_QUALITY, _groq_lock_quality, _groq_times_quality,
                      _GROQ_RPM_QUALITY, system, user, max_tokens)


def _try_cerebras(system: str, user: str, max_tokens: int) -> str | None:
    key = os.getenv("CEREBRAS_API_KEY")
    if not key:
        return None
    try:
        from cerebras.cloud.sdk import Cerebras
        client = Cerebras(api_key=key)
        r = client.chat.completions.create(
            model="gemma-4-31b",
            messages=[
                {"role": "system", "content": system},
                {"role": "user",   "content": user},
            ],
            max_tokens=max_tokens,
            temperature=0.3,
        )
        content = r.choices[0].message.content
        return content.strip() if content else None
    except Exception as e:
        if "429" in str(e) or "rate" in str(e).lower():
            return None
        return None


def _try_nvidia_550b(system: str, user: str, max_tokens: int) -> str | None:
    key = os.getenv("NVIDIA_API_KEY")
    if not key:
        return None
    try:
        import re as _re
        from openai import OpenAI
        client = OpenAI(base_url="https://integrate.api.nvidia.com/v1",
                        api_key=key, timeout=90.0, max_retries=0)
        # Nemotron 550B reasons before answering — enforce floor so output isn't truncated mid-thought
        tokens = max(max_tokens, 250)
        r = client.chat.completions.create(
            model="nvidia/nemotron-3-ultra-550b-a55b",
            messages=[
                {"role": "system", "content": system},
                {"role": "user",   "content": user},
            ],
            max_tokens=tokens,
            temperature=0.3,
        )
        content = r.choices[0].message.content or ""
        # Safety strip for any thinking delimiters that surface
        content = _re.sub(r"<think>[\s\S]*?</think>", "", content, flags=_re.IGNORECASE)
        content = _re.sub(r"<\|thinking\|>[\s\S]*?</\|thinking\|>", "", content, flags=_re.IGNORECASE)
        return content.strip() or None
    except Exception as e:
        msg = str(e)
        if "429" in msg or "quota" in msg.lower() or "rate" in msg.lower() or "timeout" in msg.lower():
            return None
        return None


def _try_gemini(system: str, user: str, max_tokens: int) -> str | None:
    # SKIP_GEMINI (2026-07-22): see matching note in mimir/inference_client.py --
    # manual escape hatch for a run where Gemini's daily quota is already known to
    # be exhausted, avoiding a wasted round-trip on every call for the whole run.
    if os.getenv("SKIP_GEMINI"):
        return None
    key = os.getenv("GEMINI_API_KEY")
    if not key:
        return None
    try:
        import google.generativeai as genai
        genai.configure(api_key=key)
        model = genai.GenerativeModel(
            "gemini-2.5-flash",
            system_instruction=system,
        )
        r = model.generate_content(
            user,
            generation_config={"max_output_tokens": max_tokens, "temperature": 0.3},
        )
        return r.text.strip()
    except Exception:
        return None


_OPENROUTER_FREE_MODEL = "meta-llama/llama-3.3-70b-instruct:free"  # confirmed congested
# 2026-07-09 (tier-wide, not model-specific -- see _openrouter_free_down_until below);
# kept as the primary attempt since congestion is often transient, with the cooldown
# below preventing repeated wasted attempts once it's confirmed down for this session.
_OPENROUTER_PAID_FALLBACK = "meta-llama/llama-3.3-70b-instruct"  # paid tier of the SAME
# model used for the free attempt above -- confirmed reasoning_tokens=0 (genuinely
# non-reasoning). Was "deepseek/deepseek-v4-flash" until 2026-07-09: confirmed live
# (via response.usage.completion_tokens_details.reasoning_tokens) that it's a reasoning
# model -- one test burned 53/85 completion tokens on invisible reasoning for a single
# sentence, and under our real (longer) prompts it sometimes consumed the ENTIRE
# max_tokens budget on reasoning alone (finish_reason="length" with zero visible
# content). This was the actual root cause of truncated "chosen"/"rejected" text that
# survived three separate orchestration-layer fixes tonight (completeness checks,
# retry loops) -- none of those could help because the underlying model, not the
# routing logic, was eating the token budget before writing anything visible.

# Sticky cooldown for the free tier, same pattern as inference_client.py's provider
# cooldowns. Found live (2026-07-09): the free tier isn't just this one model being
# down -- a completely different popular free model got the identical "temporarily
# rate-limited upstream" error seconds later. This is tier-wide congestion, not a
# dead model name, so retrying the free tier on every single call just wastes a
# round-trip before falling to paid -- skip it for a while once confirmed down.
_openrouter_free_down_until = 0.0


def _try_openrouter_one(system: str, user: str, max_tokens: int, model: str,
                        temperature: float = 0.3) -> str | None:
    key = os.getenv("OPENROUTER_API_KEY")
    if not key:
        return None
    try:
        from openai import OpenAI
        client = OpenAI(base_url="https://openrouter.ai/api/v1", api_key=key, timeout=60.0, max_retries=0)
        r = client.chat.completions.create(
            model=model,
            messages=[
                {"role": "system", "content": system},
                {"role": "user",   "content": user},
            ],
            max_tokens=max(max_tokens, 60),  # some OpenRouter models need headroom even
            # for short answers (observed: 20 tokens produced an empty completion on the
            # paid fallback where 100 worked fine) -- same shape as NVIDIA 550B's floor.
            temperature=temperature,
        )
        content = r.choices[0].message.content
        return content.strip() if content else None
    except Exception:
        return None


def _try_openrouter(system: str, user: str, max_tokens: int,
                    model: str = _OPENROUTER_FREE_MODEL, temperature: float = 0.3) -> str | None:
    """Last-resort fallback: a single account routing to dozens of models, on
    infrastructure independent of Groq/Cerebras/NVIDIA/Gemini -- added so one day's
    quota exhaustion on our primary providers doesn't stall an entire generation run.
    Free tier first (costs nothing, but shared and sometimes congested upstream --
    observed a "Venice" provider-side 429 unrelated to our account balance); paid
    fallback second (costs a fraction of a cent per call, not congestion-prone since
    it's not competing with every other free-tier user on OpenRouter). Free-model names
    on OpenRouter churn; if the default gets deprecated, check
    openrouter.ai/models?output_modalities=text for a current `:free` replacement."""
    global _openrouter_free_down_until
    if model == _OPENROUTER_FREE_MODEL and time.time() < _openrouter_free_down_until:
        return _try_openrouter_one(system, user, max_tokens, _OPENROUTER_PAID_FALLBACK, temperature)

    result = _try_openrouter_one(system, user, max_tokens, model, temperature)
    if result:
        return result
    if model == _OPENROUTER_FREE_MODEL:
        _openrouter_free_down_until = time.time() + 120
        return _try_openrouter_one(system, user, max_tokens, _OPENROUTER_PAID_FALLBACK, temperature)
    return None


# ── Public API ────────────────────────────────────────────────────────────────

def fast_call(system: str, user: str, max_tokens: int = 400) -> str:
    """Replaces Haiku. Groq 8B → Cerebras → Gemini → OpenRouter free tier."""
    for provider in [_try_groq_fast, _try_cerebras, _try_gemini, _try_openrouter]:
        result = provider(system, user, max_tokens)
        if result:
            return result
    return ""


def quality_call(system: str, user: str, max_tokens: int = 1000) -> str:
    """Bulk structured tasks (pair gen, scoring, classification). Groq 70B → NVIDIA 550B → Gemini → OpenRouter."""
    for provider in [_try_groq_quality, _try_nvidia_550b, _try_gemini, _try_openrouter]:
        result = provider(system, user, max_tokens)
        if result:
            return result
    return ""


def deep_call(system: str, user: str, max_tokens: int = 2000) -> str:
    """Analysis, strategy, council synthesis — max quality, latency ok. NVIDIA 550B → Groq 70B → Gemini → OpenRouter."""
    for provider in [_try_nvidia_550b, _try_groq_quality, _try_gemini, _try_openrouter]:
        result = provider(system, user, max_tokens)
        if result:
            return result
    return ""
