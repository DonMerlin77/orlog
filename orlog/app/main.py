import os
import json
import time
import asyncio
import hashlib
import re
from collections import defaultdict
from pathlib import Path
from fastapi import FastAPI, Request, Header, BackgroundTasks
from fastapi.staticfiles import StaticFiles
from fastapi.responses import JSONResponse, FileResponse, StreamingResponse
from fastapi.middleware.cors import CORSMiddleware
from dotenv import load_dotenv

try:
    load_dotenv(Path(__file__).parents[2] / ".env")
except IndexError:
    load_dotenv()

import sys as _sys
_sys.path.insert(0, str(Path(__file__).parents[2]))
from llm_router import fast_call, quality_call

import modal
import requests as _requests

from orlog.wyrd.wyrd_writer import write_entry as _wyrd_write, write_loki_entry as _wyrd_write_loki
from orlog.wyrd.wyrd_profile import build_profile as _wyrd_profile, lore_opening as _lore_opening, load_entries as _load_entries_global
from orlog.crumbs.crumb_store import lookup as _crumb_lookup, store as _crumb_store, build_seed_context as _crumb_seed, extract_from_trace as _crumb_extract

app = FastAPI()

app.add_middleware(
    CORSMiddleware,
    allow_origins=[
        "https://orlog.fyi",
        "https://www.orlog.fyi",
        "https://tomsem77--orlog-web-serve.modal.run",
        "http://localhost:8000",   # local dev
    ],
    allow_methods=["GET", "POST", "DELETE"],
    allow_headers=["Authorization", "Content-Type"],
)

# ── Rate limiting ─────────────────────────────────────────────────────────────
# In-memory sliding window per IP. Per-container — good enough to stop naive
# hammering. A distributed bad actor hitting multiple containers is out of scope
# until we add Redis.
_rate_buckets: dict[str, list[float]] = defaultdict(list)
_anon_daily: dict[str, dict[str, int]] = defaultdict(lambda: defaultdict(int))  # ip -> date -> count
_ANON_LIMIT  = 10   # requests per window
_AUTH_LIMIT  = 60
_RATE_WINDOW = 60   # seconds

def _get_client_ip(request: Request) -> str:
    forwarded = request.headers.get("X-Forwarded-For")
    if forwarded:
        return forwarded.split(",")[0].strip()
    return request.client.host if request.client else "unknown"

def _check_rate_limit(ip: str, authenticated: bool = False) -> bool:
    """Returns True if request is allowed, False if rate limit exceeded."""
    limit = _AUTH_LIMIT if authenticated else _ANON_LIMIT
    now   = time.time()
    bucket = _rate_buckets[ip]
    # Evict old entries outside the window
    _rate_buckets[ip] = [t for t in bucket if now - t < _RATE_WINDOW]
    if len(_rate_buckets[ip]) >= limit:
        return False
    _rate_buckets[ip].append(now)
    return True

# ── Input sanitization ────────────────────────────────────────────────────────
_INJECTION_PATTERNS = re.compile(
    r"(ignore\s+(all\s+)?previous\s+instructions?|"
    r"forget\s+(everything|all|your|the)|"
    r"you\s+are\s+now\s+|"
    r"act\s+as\s+(if\s+you\s+are|a\s+|an\s+)|"
    r"pretend\s+(you\s+are|to\s+be)|"
    r"disregard\s+(your|all|the)\s+|"
    r"new\s+instructions?:|"
    r"system\s+prompt:|"
    r"<\s*/?system\s*>|"
    r"\[INST\]|\[/INST\]|"
    r"###\s*instruction)",
    re.IGNORECASE,
)

def _sanitize(text: str) -> str:
    """Strip prompt injection attempts and control characters."""
    # Remove null bytes and non-printable control chars (keep newlines/tabs)
    text = re.sub(r"[\x00-\x08\x0b\x0c\x0e-\x1f\x7f]", "", text)
    # Flag and neutralize injection patterns by replacing with [removed]
    text = _INJECTION_PATTERNS.sub("[removed]", text)
    return text.strip()

# User history stored as flat JSONL files per user in a local volume dir
USER_DATA_DIR = Path(__file__).parent / "user_data"
USER_DATA_DIR.mkdir(exist_ok=True)

# All logs go into the persistent volume so they survive redeployments
LOG_PATH        = USER_DATA_DIR / "inference_log.jsonl"
SPEND_LOG       = USER_DATA_DIR / "spend_log.jsonl"
CRUMB_PATH      = USER_DATA_DIR / "crumbs.jsonl"
BRAIN_PATH      = Path(__file__).parent / "brain.md"
PATTERNS_PATH   = Path(__file__).parent / "store_patterns.md"

# Claude Sonnet 4-6 pricing per token
_PRICE_IN  = 3.00  / 1_000_000   # $3 per 1M input tokens
_PRICE_OUT = 15.00 / 1_000_000   # $15 per 1M output tokens

DAILY_CAP   = float(os.getenv("DAILY_API_BUDGET", "5.00"))  # default $5/day
BETA_CODES       = {c.strip().lower() for c in os.getenv("BETA_CODES", "").split(",") if c.strip()}
CLERK_SECRET_KEY     = os.getenv("CLERK_SECRET_KEY", "")
CLERK_JWKS_URL       = "https://clerk.orlog.fyi/.well-known/jwks.json"
STRIPE_SECRET_KEY    = os.getenv("STRIPE_SECRET_KEY", "")
STRIPE_PRICE_ID      = os.getenv("STRIPE_PRICE_ID", "")
STRIPE_WEBHOOK_SECRET = os.getenv("STRIPE_WEBHOOK_SECRET", "")

# Quota config
_FREE_ORLOG_LIMIT = 1
_FREE_LOKI_LIMIT  = 15
_PAID_ORLOG_LIMIT = 10
_PAID_LOKI_LIMIT  = 999

_jwks_client = None

def _get_jwks_client():
    global _jwks_client
    if _jwks_client is None:
        try:
            from jwt import PyJWKClient
            _jwks_client = PyJWKClient(CLERK_JWKS_URL, cache_keys=True)
        except Exception:
            pass
    return _jwks_client

def _verify_clerk_token(authorization: str) -> str | None:
    """Verify Clerk session JWT. Returns user_id (sub claim) or None."""
    if not authorization or not authorization.startswith("Bearer "):
        return None
    token = authorization[7:]
    try:
        import jwt as pyjwt
        client = _get_jwks_client()
        if not client:
            return None
        signing_key = client.get_signing_key_from_jwt(token)
        data = pyjwt.decode(
            token,
            signing_key.key,
            algorithms=["RS256"],
            options={"verify_aud": False},
        )
        return data.get("sub")
    except Exception:
        return None


def _user_history_path(user_id: str) -> Path:
    safe = hashlib.sha256(user_id.encode()).hexdigest()[:16]
    return USER_DATA_DIR / f"{safe}.jsonl"


def _load_user_history(user_id: str) -> list:
    path = _user_history_path(user_id)
    if not path.exists():
        return []
    entries = []
    for line in path.read_text().splitlines():
        try:
            entries.append(json.loads(line))
        except Exception:
            pass
    return entries[-50:]  # last 50 sessions


def _save_user_history_entry(user_id: str, entry: dict):
    path = _user_history_path(user_id)
    with path.open("a") as f:
        f.write(json.dumps(entry) + "\n")
    # Trim to 200 entries
    lines = path.read_text().splitlines()
    if len(lines) > 200:
        path.write_text("\n".join(lines[-200:]) + "\n")


def _today() -> str:
    return time.strftime("%Y-%m-%d", time.gmtime())


# ── Stripe customer map ─────────────────────────────────────────────────────
_STRIPE_CUSTOMERS_FILE = USER_DATA_DIR / "stripe_customers.json"


def _load_stripe_customers() -> dict:
    if _STRIPE_CUSTOMERS_FILE.exists():
        try:
            return json.loads(_STRIPE_CUSTOMERS_FILE.read_text())
        except Exception:
            pass
    return {}

def _save_stripe_customer(customer_id: str, user_id: str):
    customers = _load_stripe_customers()
    customers[customer_id] = user_id
    _STRIPE_CUSTOMERS_FILE.write_text(json.dumps(customers))


# ── Per-user plan + quota ───────────────────────────────────────────────────
def _user_plan_path(user_id: str) -> Path:
    safe = hashlib.sha256(user_id.encode()).hexdigest()[:16]
    return USER_DATA_DIR / f"{safe}_plan.json"

def _get_user_plan(user_id: str) -> dict:
    path = _user_plan_path(user_id)
    if path.exists():
        try:
            return json.loads(path.read_text())
        except Exception:
            pass
    return {"tier": "free", "stripe_subscription_id": None, "stripe_customer_id": None}

def _set_user_plan(user_id: str, plan: dict):
    _user_plan_path(user_id).write_text(json.dumps(plan))

def _user_usage_path(user_id: str) -> Path:
    safe = hashlib.sha256(user_id.encode()).hexdigest()[:16]
    return USER_DATA_DIR / f"{safe}_usage.json"

def _get_daily_usage(user_id: str) -> dict:
    path = _user_usage_path(user_id)
    today = _today()
    if path.exists():
        try:
            d = json.loads(path.read_text())
            if d.get("date") == today:
                return d
        except Exception:
            pass
    return {"date": today, "orlog": 0, "loki": 0}

def _increment_usage(user_id: str, endpoint: str):
    path = _user_usage_path(user_id)
    usage = _get_daily_usage(user_id)
    usage[endpoint] = usage.get(endpoint, 0) + 1
    path.write_text(json.dumps(usage))

def _check_quota(user_id: str | None, endpoint: str, ip: str = "") -> tuple[bool, int, int]:
    """Returns (allowed, used, limit)."""
    if os.getenv("DEMO_MODE") == "1":
        return True, 0, 9999
    if user_id is None:
        limit = _FREE_ORLOG_LIMIT if endpoint == "orlog" else _FREE_LOKI_LIMIT
        if not ip:
            return True, 0, limit
        today = _today()
        used = _anon_daily[ip][f"{endpoint}:{today}"]
        return used < limit, used, limit
    plan  = _get_user_plan(user_id)
    tier  = plan.get("tier", "free")
    limit = (_PAID_ORLOG_LIMIT if endpoint == "orlog" else _PAID_LOKI_LIMIT) if tier == "paid" \
            else (_FREE_ORLOG_LIMIT if endpoint == "orlog" else _FREE_LOKI_LIMIT)
    usage = _get_daily_usage(user_id)
    used  = usage.get(endpoint, 0)
    return used < limit, used, limit


def _daily_spend() -> float:
    if not SPEND_LOG.exists():
        return 0.0
    today = _today()
    total = 0.0
    for line in SPEND_LOG.read_text().splitlines():
        try:
            e = json.loads(line)
            if e.get("date") == today:
                total += e.get("cost", 0.0)
        except Exception:
            pass
    return total


def _log_spend(input_tokens: int, output_tokens: int, endpoint: str):
    cost = input_tokens * _PRICE_IN + output_tokens * _PRICE_OUT
    with SPEND_LOG.open("a") as f:
        f.write(json.dumps({
            "date":     _today(),
            "endpoint": endpoint,
            "cost":     round(cost, 6),
            "in_tok":   input_tokens,
            "out_tok":  output_tokens,
        }) + "\n")
    return cost


def _over_cap() -> bool:
    return _daily_spend() >= DAILY_CAP


def _load_brain() -> str:
    if BRAIN_PATH.exists():
        return BRAIN_PATH.read_text().strip()
    return ""


@app.on_event("startup")
async def warmup_mimir():
    async def _ping():
        mimir = _get_mimir()
        if mimir:
            try:
                await asyncio.wait_for(mimir.remote.aio("warmup"), timeout=300)
            except Exception:
                pass
    asyncio.create_task(_ping())

SYSTEM = (
    "You are Orlog — a reasoning interface, not an assistant.\n\n"
    "When given a decision or situation, execute these four cognitive operations in order:\n\n"
    "ANCHOR: [the single load-bearing assumption — the belief the entire situation rests on. One sentence, falsifiable.]\n"
    "TENSION: [the two forces pulling against each other. The real conflict, not the surface one.]\n"
    "GAP: [what information is missing. What you don't know but need to know before deciding.]\n"
    "RISK: [the failure condition. What breaks first if the load-bearing assumption is wrong.]\n"
    "VERDICT: [one falsifiable sentence. What to do and why. No hedging.]\n\n"
    "Rules:\n"
    "- Be specific to this situation, not generic\n"
    "- Name real things, not categories\n"
    "- The VERDICT must be actionable and falsifiable\n"
    "- Do not explain the format. Execute it.\n"
    "- WHEN THE EVIDENCE CONFLICTS: declare a winner. Name which side is stronger and why the other side loses. "
    "Do not honor both. Do not balance. Disagreement resolved means one position won — say which one.\n"
    "- A hedged verdict is a failed verdict. 'It depends' is never acceptable as a conclusion.\n"
    "- After VERDICT, add one line: NEXT: [one question the verdict opens that the person should sit with — something worth taking back to Loki if they want to go deeper]"
)

_mimir_fn = None
_mimir_translate_fn = None

def _get_mimir():
    if os.getenv("DEMO_MODE") == "1":
        return None
    global _mimir_fn
    if _mimir_fn is None:
        try:
            _mimir_fn = modal.Function.from_name("orlog-inference", "infer")
        except Exception:
            _mimir_fn = False
    return _mimir_fn if _mimir_fn else None

def _get_mimir_translate():
    global _mimir_translate_fn
    if _mimir_translate_fn is None:
        try:
            _mimir_translate_fn = modal.Function.from_name("orlog-inference", "translate")
        except Exception:
            _mimir_translate_fn = False
    return _mimir_translate_fn if _mimir_translate_fn else None


def _load_patterns() -> str:
    if PATTERNS_PATH.exists():
        return PATTERNS_PATH.read_text().strip()
    return ""

def _build_system(crumb_context: str = "", loki_context: str = "", prior_context: str = "") -> str:
    parts = [SYSTEM]
    brain = _load_brain()
    if brain:
        parts.append(brain)
    patterns = _load_patterns()
    if patterns:
        parts.append(patterns)
    if loki_context:
        parts.append(
            "LOKI EXPLORATION CONTEXT — the user worked through this with Loki before arriving here. "
            "Use this to understand what was already surfaced, what concerns came up, and what they're "
            "actually wrestling with. Do not repeat what Loki said — build on it.\n\n" + loki_context
        )
    if prior_context:
        parts.append(prior_context)
    if crumb_context:
        parts.append(crumb_context)
    return "\n\n---\n\n".join(parts)


CLAUDE_MODEL = "claude-sonnet-4-6"
HAIKU_MODEL  = "claude-haiku-4-5-20251001"

_GATE_SYSTEM = """You are a confidence scorer for a decision-reasoning engine.
Given a user input, decide if it contains a genuine decision worth reasoning about.

A genuine decision has:
- A real choice or tension (two or more paths, values in conflict, or uncertain outcome)
- Enough context to reason about (not just a single vague word or fragment)

Respond with JSON only:
{"score": 0-10, "is_decision": true/false, "nudge": "one short question to draw out more if not a decision"}

score 0-3: not a decision (venting, statement, too vague, greeting)
score 4-6: partial — has some decision signal but needs more context
score 7-10: clear decision ready for full reasoning"""

_gate_log_path       = USER_DATA_DIR / "gate_log.jsonl"
_MIMIR_GATE_URL       = "https://tomsem77--mimir-gate-serve-serve.modal.run/classify"
_MIMIR_CLASSIFIER_URL = "https://tomsem77--mimir-classifier-serve.modal.run/classify"
_MIMIR_FOLLOWUP_URL   = "https://tomsem77--mimir-orlog-serve-serve.modal.run/followup"
_MIMIR_BRIEF_URL      = "https://tomsem77--mimir-brief-serve.modal.run/generate"

def _confidence_gate(question: str) -> tuple[bool, str | None]:
    """Returns (should_proceed, nudge_if_not).
    True = run full council. False = return nudge to user instead.
    Logs every decision to gate_log.jsonl for Mimir-Gate training data.
    Legacy 7B gate endpoint removed — classifier handles this in /decide pipeline.
    /analyze uses Haiku directly (fast, always warm)."""
    try:
        data = json.loads(fast_call(_GATE_SYSTEM, question, 120))
        score       = data.get("score", 0)
        is_decision = data.get("is_decision", False)
        nudge       = data.get("nudge") or "Can you tell me more about the decision you're facing?"

        entry = {
            "ts":          time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
            "question":    question[:300],
            "score":       score,
            "is_decision": is_decision,
            "nudge":       nudge,
            "passed":      is_decision and score >= 5,
        }
        with _gate_log_path.open("a") as f:
            f.write(json.dumps(entry) + "\n")

        if is_decision and score >= 5:
            return True, None
        return False, nudge
    except Exception:
        return True, None

_ANSWERABILITY_SYSTEM = (
    "You are a pre-council gate. Determine if the question below has enough context to reason about — "
    "not perfect context, just enough to take a side.\n\n"
    "ANSWERABLE: the core trade-off is clear and a reasonable stance is possible, even if more detail would help. "
    "Most personal decisions with a named situation qualify — err on the side of answerable.\n"
    "UNANSWERABLE: purely abstract, no personal situation described, or so speculative that no stance is possible "
    "(e.g. 'what should I do with my life?', 'will AI take my job?', 'what is success?').\n\n"
    "Reply with JSON only: {\"answerable\": true/false, \"reason\": \"one sentence\", "
    "\"needs\": \"what is missing if unanswerable, else empty string\"}"
)

def _answerability_gate(question: str, classifier_result: dict | None = None) -> tuple[bool, str]:
    """Returns (is_answerable, nudge_if_not).
    Prefers classifier result (free, already running) over a separate Haiku call.
    Falls back to Haiku only if classifier didn't return answerability data."""
    # If classifier already checked answerability, use that result
    if classifier_result is not None and "answerable" in classifier_result:
        if classifier_result["answerable"] is False:
            nudge = classifier_result.get("nudge") or "This looks like a factual question — try the Conversation tab, where Loki can answer it and help you find the decision behind it."
            return False, nudge
        return True, ""

    # Fallback: fast_call (used until answerability adapter is trained + wired)
    try:
        raw = fast_call(_ANSWERABILITY_SYSTEM, question[:600], 120)
        start = raw.find("{"); end = raw.rfind("}") + 1
        data = json.loads(raw[start:end]) if start >= 0 else {}
        if data.get("answerable", True):
            return True, ""
        needs = data.get("needs", "")
        nudge = "This looks like a factual question — try the Conversation tab, where Loki can answer it and help you find the decision behind it."
        return False, nudge
    except Exception:
        return True, ""

_LOKI_EXTRACT_SYSTEM = (
    "You are a goal-extraction layer. Given a user's raw input, restate their actual decision "
    "as one clear, specific sentence — the real choice they're facing, not the surface complaint. "
    "If the input already states a clear decision, return it with minimal changes. "
    "Output only the restatement. No preamble, no explanation."
)

def _loki_extract(question: str) -> str:
    """Single-turn Loki pass: extract clean decision from raw input. Returns cleaned question.
    Legacy 7B Loki endpoint removed — goes straight to Haiku (always warm, no cold start)."""
    # fast_call direct
    try:
        extracted = fast_call(_LOKI_EXTRACT_SYSTEM, question[:600], 100).strip()
        return extracted if extracted else question
    except Exception:
        return question


def _generate_brief_card(goal: str, history: list) -> dict:
    """
    Build the seam card between Loki and Orlog.
    Tries Mimir-Brief (CPU, fine-tuned) → falls back to deterministic assembly.
    No Claude or Haiku calls.
    """
    # Build conversation transcript for Mimir-Brief
    turns = [
        f"{'USER' if m['role'] == 'user' else 'LOKI'}: {m['content'][:300]}"
        for m in history
        if m.get("role") in ("user", "assistant") and not m.get("content", "").startswith("[")
    ]
    conversation = "\n".join(turns[-10:])  # last 10 turns max

    # Try Mimir-Brief first
    try:
        r = _requests.post(
            _MIMIR_BRIEF_URL,
            json={"conversation": conversation, "goal": goal},
            timeout=30,
        )
        if r.ok:
            card = r.json()
            if card.get("question") and "error" not in card:
                return card
    except Exception:
        pass

    # Deterministic fallback — tension from classifier, summary from history arc
    tension = ""
    try:
        clf = _call_classifier(f"QUESTION: {goal[:600]}\n\nVERDICT: (loki session, not yet resolved)")
        if clf:
            tension = clf.get("tension", "")
    except Exception:
        pass

    user_msgs = [
        m["content"] for m in history
        if m.get("role") == "user" and not m.get("content", "").startswith("[")
    ]
    if not user_msgs:
        return {"question": goal, "tension": tension, "summary": ""}

    first = user_msgs[0].strip()[:140]
    pivot_signals = ["but", "actually", "the real", "really", "honest", "afraid", "worried", "i guess", "i think what"]
    pivot = None
    for msg in user_msgs[1:]:
        if any(s in msg.lower() for s in pivot_signals):
            pivot = msg.strip()[:120]
            break

    if first.lower()[:60] == goal.lower()[:60]:
        summary = goal[:200]
    elif pivot:
        summary = f'Started with: "{first}." Shifted: "{pivot}." Found: {goal[:120]}'
    else:
        summary = f'Started with: "{first}." Surfaced: {goal[:150]}'

    return {"question": goal, "tension": tension, "summary": summary}


def _fallback_claude(question: str, crumb_context: str = "", loki_context: str = "", system_override: str = "") -> tuple[str, str, str]:
    system = system_override or _build_system(crumb_context, loki_context)
    try:
        return quality_call(system, question, 1000), "claude", CLAUDE_MODEL
    except Exception as e:
        raise RuntimeError(f"Claude fallback failed: {e.__class__.__name__}: {e}")


def _log(entry: dict):
    entry.setdefault("ts", time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()))
    with LOG_PATH.open("a") as f:
        f.write(json.dumps(entry) + "\n")


LOKI_QUALITY_LOG = USER_DATA_DIR / "loki_quality_log.jsonl"


@app.post("/analyze")
async def analyze(request: Request, background_tasks: BackgroundTasks, authorization: str = Header(default="")):
    body             = await request.json()
    question         = _sanitize(body.get("question", "").strip())
    loki_session_id  = body.get("loki_session_id") or None
    loki_context     = (body.get("loki_context") or "").strip()[:2000]
    orlog_session_id = body.get("orlog_session_id") or None
    skip_extract     = bool(body.get("skip_extract", False))  # one-shot flag from Loki handoff
    if not question:
        return JSONResponse({"error": "No question provided"}, status_code=400)
    if _over_cap():
        return JSONResponse({"error": "Daily capacity reached. Try again tomorrow."}, status_code=429)

    user_id = _verify_clerk_token(authorization)

    ip = _get_client_ip(request)
    if not _check_rate_limit(ip, authenticated=bool(user_id)):
        return JSONResponse({"error": "Too many requests. Slow down."}, status_code=429)

    allowed, used, limit = _check_quota(user_id, "orlog", ip)
    if not allowed:
        tier = _get_user_plan(user_id).get("tier", "free") if user_id else "anonymous"
        return JSONResponse({
            "error":   "quota_exceeded",
            "message": f"You've used your {limit} Orlog{'s' if limit != 1 else ''} for today. Sign in to continue." if not user_id else f"You've used your {limit} Orlog{'s' if limit != 1 else ''} for today. Upgrade for {_PAID_ORLOG_LIMIT}/day.",
            "used":    used,
            "limit":   limit,
            "tier":    tier,
        }, status_code=429)

    # Load prior session for follow-up
    prior_session = _load_orlog_session(orlog_session_id) if orlog_session_id else None

    # Skip gates when coming from Loki or as a follow-up — context is already established
    if not loki_session_id and not prior_session:
        proceed, nudge = _confidence_gate(question)
        if not proceed:
            return JSONResponse({"trace": nudge, "id": "", "backend": "gate", "model": "gate"})

        answerable, ans_nudge = _answerability_gate(question)
        if not answerable:
            return JSONResponse({"trace": ans_nudge, "id": "", "backend": "answerability_gate", "model": "gate"})

    # Skip extraction when frontend signals question is already clean (Loki handoff)
    clean_question = question if skip_extract else _loki_extract(question)
    crumb = _crumb_lookup(clean_question, CRUMB_PATH)
    crumb_context = _crumb_seed(crumb) if crumb else ""

    t0 = time.time()

    model = None
    try:
        if prior_session:
            # Follow-up: try Mimir /followup first, fall back to Claude
            try:
                r = _requests.post(
                    _MIMIR_FOLLOWUP_URL,
                    json={"question": question, "prior_verdict": prior_session["trace"]},
                    timeout=90,
                )
                r.raise_for_status()
                trace   = r.json().get("response", "")
                backend = "mimir_followup"
                model   = "Mimir · Follow-up"
            except Exception as e:
                prior_context = (
                    "PRIOR ORLOG VERDICT — the user received this and is now pushing back or adding context. "
                    "Build on it, don't repeat it.\n\n"
                    f"Original question: {prior_session['question']}\n"
                    f"Verdict: {prior_session['trace']}"
                )
                trace, backend, model = _fallback_claude(
                    question, crumb_context, loki_context,
                    system_override=_build_system(crumb_context, loki_context, prior_context)
                )
                backend = f"claude_followup_fallback({e.__class__.__name__})"
        else:
            try:
                r = await asyncio.to_thread(
                    _requests.post, _MIMIR_ORLOG_URL,
                    json={"question": clean_question}, timeout=90,
                )
                r.raise_for_status()
                trace   = r.json().get("response", "")
                backend = "mimir_orlog_v4"
                model   = "Mimir · Orlog v4"
            except Exception as e:
                trace, backend, model = _fallback_claude(clean_question, crumb_context, loki_context)
                backend = f"claude_fallback({e.__class__.__name__})"
    except Exception as e:
        return JSONResponse({"error": f"Reasoning failed: {e}"}, status_code=500)

    latency  = round(time.time() - t0, 2)
    entry_id = str(int(t0 * 1000))

    # Always save session so this verdict can be followed up on
    background_tasks.add_task(_save_orlog_session, entry_id, question, clean_question, trace)

    if user_id:
        _increment_usage(user_id, "orlog")
        background_tasks.add_task(
            _wyrd_write,
            user_id=user_id,
            session_id=entry_id,
            question=question,
            verdict=trace,
            data_dir=USER_DATA_DIR,
        )
    else:
        _anon_daily[ip][f"orlog:{_today()}"] += 1

    background_tasks.add_task(_shadow_orlog, entry_id, question, trace)

    def _store_crumb_bg():
        try:
            tension, blind_spot, verdict_hint = _crumb_extract(trace)
            if tension or verdict_hint:
                _crumb_store(clean_question, tension, blind_spot, verdict_hint, CRUMB_PATH)
        except Exception:
            pass
    background_tasks.add_task(_store_crumb_bg)

    _log({
        "id":               entry_id,
        "question":         question[:200],
        "backend":          backend,
        "model":            model,
        "latency":          latency,
        "thumbs":           None,
        "loki_session_id":  loki_session_id,
        "orlog_session_id": orlog_session_id,
        "user_id":          user_id,
    })

    return {"trace": trace, "id": entry_id, "backend": backend, "model": model}


@app.post("/analyze/stream")
async def analyze_stream(request: Request, authorization: str = Header(default="")):
    """SSE streaming version of /analyze. Streams Claude tokens as they arrive.
    Mimir path returns full response in one chunk. /analyze JSON endpoint unchanged."""
    body             = await request.json()
    question         = _sanitize(body.get("question", "").strip())
    loki_session_id  = body.get("loki_session_id") or None
    loki_context     = (body.get("loki_context") or "").strip()[:2000]
    orlog_session_id = body.get("orlog_session_id") or None
    skip_extract     = bool(body.get("skip_extract", False))

    if not question:
        return JSONResponse({"error": "No question provided"}, status_code=400)
    if _over_cap():
        return JSONResponse({"error": "Daily capacity reached."}, status_code=429)

    user_id = _verify_clerk_token(authorization)
    ip      = _get_client_ip(request)

    if not _check_rate_limit(ip, authenticated=bool(user_id)):
        return JSONResponse({"error": "Too many requests."}, status_code=429)

    allowed, used, limit = _check_quota(user_id, "orlog", ip)
    if not allowed:
        tier = _get_user_plan(user_id).get("tier", "free") if user_id else "anonymous"
        return JSONResponse({
            "error":   "quota_exceeded",
            "message": f"You've used your {limit} Orlog{'s' if limit != 1 else ''} for today. Sign in to continue." if not user_id else f"Upgrade for {_PAID_ORLOG_LIMIT}/day.",
            "used": used, "limit": limit, "tier": tier,
        }, status_code=429)

    prior_session = _load_orlog_session(orlog_session_id) if orlog_session_id else None

    if not loki_session_id and not prior_session:
        proceed, nudge = _confidence_gate(question)
        if not proceed:
            return JSONResponse({"trace": nudge, "id": "", "backend": "gate", "model": "gate"})
        answerable, ans_nudge = _answerability_gate(question)
        if not answerable:
            return JSONResponse({"trace": ans_nudge, "id": "", "backend": "answerability_gate", "model": "gate"})

    clean_question = question if skip_extract else _loki_extract(question)
    crumb        = _crumb_lookup(clean_question, CRUMB_PATH)
    crumb_context = _crumb_seed(crumb) if crumb else ""

    t0       = time.time()
    entry_id = str(int(t0 * 1000))

    async def _stream():
        full_text = ""
        backend   = "claude_stream"
        model_tag = CLAUDE_MODEL

        try:
            if prior_session:
                try:
                    r = await asyncio.to_thread(
                        _requests.post, _MIMIR_FOLLOWUP_URL,
                        json={"question": question, "prior_verdict": prior_session["trace"]},
                        timeout=90,
                    )
                    r.raise_for_status()
                    full_text = r.json().get("response", "")
                    backend   = "mimir_followup"
                    model_tag = "Mimir · Follow-up"
                    yield f"data: {json.dumps({'full': full_text})}\n\n"
                except Exception as e:
                    prior_ctx = (
                        "PRIOR ORLOG VERDICT — build on it, don't repeat it.\n\n"
                        f"Original question: {prior_session['question']}\n"
                        f"Verdict: {prior_session['trace']}"
                    )
                    system = _build_system(crumb_context, loki_context, prior_ctx)
                    full_text = await asyncio.to_thread(quality_call, system, question, 1000)
                    backend = "claude_followup_fallback"
                    yield f"data: {json.dumps({'full': full_text})}\n\n"

            else:
                mimir_ok = False
                try:
                    r = await asyncio.to_thread(
                        _requests.post, _MIMIR_ORLOG_URL,
                        json={"question": clean_question}, timeout=30,
                    )
                    r.raise_for_status()
                    full_text = r.json().get("response", "")
                    backend   = "mimir_orlog_v4"
                    model_tag = "Mimir · Orlog v4"
                    mimir_ok  = True
                    yield f"data: {json.dumps({'full': full_text})}\n\n"
                except Exception:
                    pass

                if not mimir_ok:
                    system = _build_system(crumb_context, loki_context)
                    full_text = await asyncio.to_thread(quality_call, system, clean_question, 1000)
                    yield f"data: {json.dumps({'full': full_text})}\n\n"

        except Exception as e:
            yield f"data: {json.dumps({'err': str(e)})}\n\n"
            return

        latency = round(time.time() - t0, 2)
        yield f"data: {json.dumps({'done': {'id': entry_id, 'backend': backend, 'model': model_tag, 'latency': latency}})}\n\n"

        # Post-stream work (replaces background_tasks)
        try: _save_orlog_session(entry_id, question, clean_question, full_text)
        except Exception: pass

        if user_id:
            _increment_usage(user_id, "orlog")
            try: _wyrd_write(user_id=user_id, session_id=entry_id, question=question, verdict=full_text, data_dir=USER_DATA_DIR)
            except Exception: pass
        else:
            _anon_daily[ip][f"orlog:{_today()}"] += 1

        try: asyncio.get_event_loop().run_in_executor(None, _shadow_orlog, entry_id, question, full_text)
        except Exception: pass

        def _crumb_bg():
            try:
                t, b, v = _crumb_extract(full_text)
                if t or v: _crumb_store(clean_question, t, b, v, CRUMB_PATH)
            except Exception: pass
        try: asyncio.get_event_loop().run_in_executor(None, _crumb_bg)
        except Exception: pass

        _log({"id": entry_id, "question": question[:200], "backend": backend, "model": model_tag,
              "latency": latency, "thumbs": None, "loki_session_id": loki_session_id,
              "orlog_session_id": orlog_session_id, "user_id": user_id, "streamed": True})

    return StreamingResponse(
        _stream(),
        media_type="text/event-stream",
        headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no", "Connection": "keep-alive"},
    )


_decide_log_path = USER_DATA_DIR / "decide_log.jsonl"

@app.post("/decide")
async def decide(request: Request, background_tasks: BackgroundTasks, authorization: str = Header(default="")):
    """Full pipeline: Gate → Loki extract → Orlog. Single endpoint for the complete stack."""
    body     = await request.json()
    question = body.get("question", "").strip()
    if not question:
        return JSONResponse({"error": "No question provided"}, status_code=400)
    if _over_cap():
        return JSONResponse({"error": "Daily capacity reached. Try again tomorrow."}, status_code=429)

    user_id = _verify_clerk_token(authorization)
    allowed, used, limit = _check_quota(user_id, "orlog")
    if not allowed:
        tier = _get_user_plan(user_id).get("tier", "free") if user_id else "anonymous"
        return JSONResponse({
            "error":   "quota_exceeded",
            "message": f"You've used your {limit} Orlog{'s' if limit != 1 else ''} for today. Upgrade for {_PAID_ORLOG_LIMIT}/day.",
            "used":    used,
            "limit":   limit,
            "tier":    tier,
        }, status_code=429)

    # Stage 1+2+3: Classifier (Gate + Answerability + Loki extract — CPU, always warm)
    # Falls back to separate Gate + Loki calls if classifier is down
    clean_question = None
    clf = None
    try:
        r = _requests.post(_MIMIR_CLASSIFIER_URL, json={"text": question}, timeout=15)
        if r.ok:
            clf = r.json()
            if not clf.get("is_decision"):
                nudge = clf.get("nudge") or "Can you tell me more about the decision you're facing?"
                return JSONResponse({"nudge": nudge, "stage": "gate", "reframe": None, "trace": None, "id": ""})
            # Answerability check — classifier returns this once adapter is trained
            answerable, ans_nudge = _answerability_gate(question, clf)
            if not answerable:
                return JSONResponse({"nudge": ans_nudge, "stage": "answerability_gate", "reframe": None, "trace": None, "id": ""})
            clean_question = clf.get("clean") or question
    except Exception:
        pass

    if clean_question is None:
        # Fallback: separate gate + loki
        proceed, nudge = _confidence_gate(question)
        if not proceed:
            return JSONResponse({"nudge": nudge, "stage": "gate", "reframe": None, "trace": None, "id": ""})
        answerable, ans_nudge = _answerability_gate(question)
        if not answerable:
            return JSONResponse({"nudge": ans_nudge, "stage": "answerability_gate", "reframe": None, "trace": None, "id": ""})
        clean_question = _loki_extract(question)

    # Stage 3: Orlog on clean question
    t0    = time.time()
    mimir = _get_mimir()
    if mimir:
        try:
            trace   = await asyncio.wait_for(mimir.remote.aio(clean_question), timeout=90)
            backend = "mimir_dpo_v4"
            model   = "Mimir · DPO v4"
        except Exception as e:
            trace, backend, model = _fallback_claude(clean_question)
            backend = f"claude_fallback({e.__class__.__name__})"
    else:
        trace, backend, model = _fallback_claude(clean_question)

    latency  = round(time.time() - t0, 2)
    entry_id = str(int(t0 * 1000))

    if user_id:
        _increment_usage(user_id, "orlog")
        background_tasks.add_task(
            _wyrd_write,
            user_id=user_id,
            session_id=entry_id,
            question=clean_question,
            verdict=trace,
            data_dir=USER_DATA_DIR,
        )

    background_tasks.add_task(_shadow_orlog, entry_id, clean_question, trace)

    with _decide_log_path.open("a") as f:
        f.write(json.dumps({
            "id":             entry_id,
            "ts":             time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
            "raw":            question[:300],
            "reframe":        clean_question[:300],
            "backend":        backend,
            "latency":        latency,
            "user_id":        user_id,
        }) + "\n")

    return {
        "trace":   trace,
        "reframe": clean_question,
        "id":      entry_id,
        "backend": backend,
        "model":   model,
        "stage":   "orlog",
    }


@app.post("/feedback")
async def feedback(request: Request):
    body     = await request.json()
    entry_id = body.get("id", "")
    thumb    = body.get("thumb")        # "up" | "down"
    reason   = body.get("reason", "")  # free text or chip label
    if not entry_id or thumb not in ("up", "down"):
        return JSONResponse({"error": "id and thumb (up|down) required"}, status_code=400)

    lines = LOG_PATH.read_text().splitlines() if LOG_PATH.exists() else []
    updated = []
    loki_session_id = None
    for line in lines:
        try:
            e = json.loads(line)
            if e.get("id") == entry_id:
                e["thumbs"] = thumb
                if reason:
                    e["reason"] = reason[:300]
                loki_session_id = e.get("loki_session_id")
            updated.append(json.dumps(e))
        except Exception:
            updated.append(line)
    LOG_PATH.write_text("\n".join(updated) + "\n")

    # If this Orlog verdict came from a Loki session, write a quality signal
    if loki_session_id:
        with LOKI_QUALITY_LOG.open("a") as f:
            f.write(json.dumps({
                "loki_session_id": loki_session_id,
                "orlog_entry_id":  entry_id,
                "quality":         thumb,   # "up" = good excavation, "down" = bad
                "ts":              time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
            }) + "\n")

    return {"ok": True}


@app.get("/ping")
async def ping():
    return {"ok": True}


@app.get("/stream-test")
async def stream_test():
    """SSE buffering test — confirm Modal passes tokens through without batching."""
    import asyncio

    async def _gen():
        words = ["VERDICT:", " Streaming", " works.", " \n\n", "KEY", " TENSION:", " test", " vs", " reality."]
        for w in words:
            yield f"data: {w}\n\n"
            await asyncio.sleep(0.15)
        yield "data: [DONE]\n\n"

    return StreamingResponse(
        _gen(),
        media_type="text/event-stream",
        headers={
            "Cache-Control":    "no-cache",
            "X-Accel-Buffering": "no",
            "Connection":       "keep-alive",
        },
    )

@app.get("/stats")
async def stats():
    if not LOG_PATH.exists():
        return {"total": 0, "thumbs_down_pct": None, "backends": {}}
    lines = [json.loads(l) for l in LOG_PATH.read_text().splitlines() if l.strip()]
    total  = len(lines)
    rated  = [l for l in lines if l.get("thumbs")]
    downs  = [l for l in rated if l["thumbs"] == "down"]

    reason_counts: dict = {}
    recent_complaints: list = []
    for l in downs:
        r = l.get("reason", "")
        if r:
            reason_counts[r] = reason_counts.get(r, 0) + 1
        recent_complaints.append({
            "question": l.get("question", "")[:120],
            "reason":   r,
        })

    backends: dict = {}
    for l in lines:
        b = l.get("backend", "unknown")
        backends[b] = backends.get(b, 0) + 1

    return {
        "total":              total,
        "rated":              len(rated),
        "thumbs_down_pct":    round(100 * len(downs) / len(rated), 1) if rated else None,
        "rollback_trigger":   (len(rated) >= 50 and len(downs) / len(rated) > 0.40),
        "backends":           backends,
        "top_complaints":     sorted(reason_counts.items(), key=lambda x: -x[1])[:5],
        "recent_complaints":  recent_complaints[-10:],
    }


WAITLIST_PATH = Path(__file__).parent / "waitlist.jsonl"

@app.get("/beta-active")
async def beta_active():
    return {"active": bool(BETA_CODES)}


@app.post("/validate-code")
async def validate_code(request: Request):
    body = await request.json()
    code = body.get("code", "").strip().lower()
    if not BETA_CODES:          # No codes set → open access
        return {"valid": True}
    return {"valid": code in BETA_CODES}


@app.post("/join-waitlist")
async def join_waitlist(request: Request):
    body  = await request.json()
    email = body.get("email", "").strip().lower()
    if not email or "@" not in email:
        return JSONResponse({"error": "valid email required"}, status_code=400)
    with WAITLIST_PATH.open("a") as f:
        f.write(json.dumps({"email": email, "ts": int(time.time())}) + "\n")
    return {"ok": True}


@app.get("/waitlist-count")
async def waitlist_count():
    if not WAITLIST_PATH.exists():
        return {"count": 0}
    return {"count": sum(1 for _ in WAITLIST_PATH.open())}


@app.post("/translate")
async def translate_trace(request: Request):
    body  = await request.json()
    trace = body.get("trace", "").strip()
    if not trace:
        return JSONResponse({"error": "trace required"}, status_code=400)

    translator = _get_mimir_translate()
    if translator:
        try:
            plain = await asyncio.wait_for(translator.remote.aio(trace), timeout=90)
            return {"plain": plain, "backend": "mimir"}
        except Exception:
            pass

    # fallback: router if Mimir translate times out
    plain = quality_call(
        "Translate this reasoning trace into 3-4 plain sentences a non-expert can act on. No rune labels. Just what was found and what to do.",
        trace,
        300,
    )
    return {"plain": plain.strip(), "backend": "claude_fallback"}


CHALLENGE_SYSTEM = (
    "You are Orlog's adversarial layer. You will receive a reasoning verdict.\n\n"
    "Your job: construct the strongest possible case AGAINST that verdict.\n"
    "- Name the specific assumption the verdict got wrong\n"
    "- Give one concrete scenario where following that verdict causes harm\n"
    "- End with one falsifiable counter-verdict\n\n"
    "Be surgical. One paragraph. No hedging. Make the original verdict earn its position."
)


@app.post("/challenge")
async def challenge(request: Request):
    body    = await request.json()
    verdict = body.get("verdict", "").strip()
    if not verdict:
        return JSONResponse({"error": "verdict required"}, status_code=400)

    mimir = _get_mimir()
    if mimir:
        try:
            prompt = f"Challenge this verdict: {verdict}"
            result = await asyncio.wait_for(mimir.remote.aio(prompt), timeout=90)
            return {"challenge": result, "backend": "mimir"}
        except Exception:
            pass

    challenge_text = quality_call(CHALLENGE_SYSTEM, f"Verdict to challenge: {verdict}", 400)

    plain = quality_call(
        "In one plain sentence, summarize the core of this counter-argument for someone who hasn't read the original reasoning. No jargon.",
        challenge_text,
        150,
    )
    return {"challenge": challenge_text, "plain": plain.strip(), "backend": "claude_fallback"}


BASELINE_SYSTEM = (
    "You are a helpful AI assistant. "
    "When given a question or decision, provide a clear, balanced answer. "
    "Consider multiple perspectives and be thorough in your response."
)


@app.post("/compare")
async def compare(request: Request):
    body     = await request.json()
    question = body.get("question", "").strip()
    if not question:
        return JSONResponse({"error": "No question provided"}, status_code=400)
    if _over_cap():
        return JSONResponse({"error": "Daily capacity reached."}, status_code=429)

    async def run_baseline():
        return await asyncio.to_thread(quality_call, BASELINE_SYSTEM, question, 600)

    async def run_mimir():
        mimir = _get_mimir()
        if mimir:
            try:
                return await asyncio.wait_for(mimir.remote.aio(question), timeout=90)
            except Exception:
                pass
        return await asyncio.to_thread(quality_call, _build_system(), question, 1000)

    baseline, mimir_out = await asyncio.gather(run_baseline(), run_mimir())
    return {"baseline": baseline, "mimir": mimir_out}


EXECUTE_SYSTEM = (
    "You are the execution layer of Orlog. You receive a reasoning verdict and the original question.\n\n"
    "Your job: produce a concrete, sequenced action plan that implements the verdict.\n\n"
    "Format:\n"
    "STEP 1: [specific action — what exactly to do, not a category]\n"
    "STEP 2: [next action]\n"
    "... (as many steps as needed, no more)\n"
    "DELIVERABLE: [the tangible thing that exists when this is done]\n\n"
    "Rules:\n"
    "- Each step must be something a person can actually do today\n"
    "- Name tools, files, people, platforms — no vague nouns\n"
    "- If code is needed, write it\n"
    "- If a document is needed, draft it\n"
    "- Do not explain the reasoning — the verdict already did that. Only execute."
)


@app.post("/execute")
async def execute(request: Request):
    body = await request.json()
    question = body.get("question", "").strip()
    verdict  = body.get("verdict", "").strip()
    if not verdict:
        return JSONResponse({"error": "verdict required"}, status_code=400)

    plan = quality_call(EXECUTE_SYSTEM, f"Question: {question}\n\nVerdict: {verdict}", 1200)
    return {"plan": plan.strip()}


@app.get("/spend")
async def spend():
    today = _today()
    by_endpoint: dict = {}
    total = 0.0
    if SPEND_LOG.exists():
        for line in SPEND_LOG.read_text().splitlines():
            try:
                e = json.loads(line)
                if e.get("date") == today:
                    ep = e.get("endpoint", "unknown")
                    by_endpoint[ep] = round(by_endpoint.get(ep, 0.0) + e.get("cost", 0.0), 6)
                    total += e.get("cost", 0.0)
            except Exception:
                pass
    return {
        "date":        today,
        "total":       round(total, 4),
        "cap":         DAILY_CAP,
        "remaining":   round(max(0.0, DAILY_CAP - total), 4),
        "pct_used":    round(100 * total / DAILY_CAP, 1) if DAILY_CAP else None,
        "by_endpoint": by_endpoint,
    }


_MIMIR_ORLOG_HEALTH = "https://tomsem77--mimir-orlog-serve-serve.modal.run/health"

@app.get("/warm")
async def warm():
    try:
        _requests.get(_MIMIR_ORLOG_HEALTH, timeout=90)
        return {"status": "ready"}
    except Exception:
        return {"status": "warming"}

@app.get("/health")
async def health_check():
    """Quick diagnostic — tests each critical component and returns pass/fail."""
    import time as _time
    results = {}

    # 1. Env vars
    required_vars = ["ANTHROPIC_API_KEY", "CLERK_SECRET_KEY", "STRIPE_SECRET_KEY"]
    missing = [v for v in required_vars if not os.getenv(v)]
    results["env_vars"] = {"ok": not missing, "missing": missing}

    # 2. LLM router — 1-token ping
    t0 = _time.time()
    try:
        fast_call("", "ping", 1)
        results["llm_router"] = {"ok": True, "latency_ms": round((_time.time() - t0) * 1000)}
    except Exception as e:
        results["llm_router"] = {"ok": False, "error": str(e)}

    # 3. Mimir Orlog GPU — reachable?
    mimir = _get_mimir()
    results["mimir_orlog"] = {"ok": mimir is not None, "note": "GPU app reachable" if mimir else "app not found or cold"}

    # 4. Classifier (mimir-classifier CPU)
    try:
        import modal as _modal
        cls_fn = _modal.Function.from_name("mimir-classifier", "classify")
        results["mimir_classifier"] = {"ok": cls_fn is not None}
    except Exception as e:
        results["mimir_classifier"] = {"ok": False, "error": str(e)}

    all_ok = all(v.get("ok") for v in results.values())
    return {"status": "ok" if all_ok else "degraded", "checks": results, "ts": _time.strftime("%Y-%m-%dT%H:%M:%SZ")}


@app.get("/training-status")
async def training_status():
    def _count_log(path, filter_fn=None):
        if not path.exists():
            return 0
        lines = [l for l in path.read_text().splitlines() if l.strip()]
        if filter_fn:
            lines = [l for l in lines if filter_fn(l)]
        return len(lines)

    inf_total  = _count_log(LOG_PATH)
    inf_rated  = _count_log(LOG_PATH, lambda l: json.loads(l).get("thumbs") in ("up","down"))
    inf_up     = _count_log(LOG_PATH, lambda l: json.loads(l).get("thumbs") == "up")
    inf_down   = _count_log(LOG_PATH, lambda l: json.loads(l).get("thumbs") == "down")
    inf_linked = _count_log(LOG_PATH, lambda l: bool(json.loads(l).get("loki_session_id")))

    loki_sessions = _count_log(_loki_log_path)
    loki_quality  = _count_log(LOKI_QUALITY_LOG)
    loki_good     = _count_log(LOKI_QUALITY_LOG, lambda l: json.loads(l).get("quality") == "up")
    loki_bad      = _count_log(LOKI_QUALITY_LOG, lambda l: json.loads(l).get("quality") == "down")

    route_log = Path(__file__).parent / "route_log.jsonl"
    routes    = _count_log(route_log)

    threshold = 60
    return {
        "mimir_orlog": {
            "total_sessions":   inf_total,
            "rated":            inf_rated,
            "up":               inf_up,
            "down":             inf_down,
            "loki_linked":      inf_linked,
            "threshold":        threshold,
            "ready_to_train":   inf_rated >= threshold,
            "need":             max(0, threshold - inf_rated),
        },
        "mimir_loki": {
            "total_sessions":   loki_sessions,
            "quality_signals":  loki_quality,
            "good":             loki_good,
            "bad":              loki_bad,
            "threshold":        threshold,
            "ready_to_train":   loki_quality >= threshold,
            "need":             max(0, threshold - loki_quality),
        },
        "mimir_runner": {
            "route_logs":       routes,
            "threshold":        threshold,
            "ready_to_train":   routes >= threshold,
            "need":             max(0, threshold - routes),
        },
        "wyrd": {
            "users_with_data":       len(list(USER_DATA_DIR.glob("*_wyrd.jsonl"))) if USER_DATA_DIR.exists() else 0,
            "mahoraga_gate":         10,
            "wyrd_panel_gate":       10,
            "archetype_ready":       True,
            "mahoraga_ready":        len(list(USER_DATA_DIR.glob("*_wyrd.jsonl"))) >= 10 if USER_DATA_DIR.exists() else False,
        },
    }


@app.get("/how")
async def how_page():
    return FileResponse(str(Path(__file__).parent / "static" / "how.html"))


@app.get("/compare")
async def compare_page():
    return FileResponse(str(Path(__file__).parent / "static" / "compare.html"))


@app.get("/signin")
async def signin_page():
    return FileResponse(str(Path(__file__).parent / "static" / "signin.html"))


@app.get("/app")
async def app_page():
    return FileResponse(str(Path(__file__).parent / "static" / "index.html"))


LOKI_SYSTEM = """You are Loki — a thoughtful conversational partner. You reason carefully, speak plainly, and help people think through whatever they bring to you.

You are not a funnel. You are not intake. You are a thinking partner first.

HOW YOU RESPOND:

Read what the person actually said and respond to it directly. Don't default to questions. Think: what does this person need right now?

- Factual question → answer it clearly in 2-4 sentences. Then ask once if there's a decision behind it — only if it seems likely.
- Venting or processing → reflect back what you heard honestly. Name what's really going on. You don't need to steer them anywhere.
- Exploring without a clear direction → engage with what they said, offer a reframe or observation, then ask one thing that helps them go deeper.
- A real decision with tension → engage with the tension. Ask the question that gets at what's actually hard about it.

Your default move: say something useful, then ask one question if it's warranted. Not every response needs a question. Sometimes the right move is just to respond.

WHEN TO ESCALATE TO ORLOG:

Orlog is a multi-perspective council. Bring the conversation there when you notice all of these:
1. There are real pulls in multiple directions — not just one obvious answer
2. The stakes are meaningful (time, money, relationships, identity, direction)
3. The person is genuinely stuck, not just thinking out loud
4. A specific, honest question can be named

When that moment arrives, don't announce a process. Just say what you've noticed — "I think I know what's actually at stake here" — summarize what you heard in 2-3 honest sentences, then end with exactly:
[READY: <the real tension, trade-off, or sticking point — specific enough that a thoughtful advisor could take a real position on it. Not a vague goal. Not a life philosophy. The actual thing they're wrestling with.>]

WHAT YOU ARE:

You reason from a framework of competing perspectives and underlying values — the same foundation Orlog draws from, just without the full council. For most things, you're enough. You can handle simple questions, early exploration, and light decisions on your own. Orlog is for when the problem genuinely needs multiple angles examined in parallel.

RULES:
- One question per response. Never stack them.
- Short messages. Dense, not long.
- Speak plainly. Most people are not technical.
- Don't explain yourself or this system. Just talk.
- Don't manufacture urgency toward Orlog. Let it emerge from the conversation naturally."""

_MIMIR_LOKI_URL   = "https://tomsem77--mimir-loki-serve-serve.modal.run/generate"
_shadow_log_path  = USER_DATA_DIR / "loki_shadow_log.jsonl"

_MIMIR_ORLOG_URL       = "https://tomsem77--mimir-orlog-serve-serve.modal.run/generate"
_orlog_shadow_log_path = USER_DATA_DIR / "orlog_shadow_log.jsonl"

def _shadow_orlog(entry_id: str, question: str, claude_response: str):
    try:
        r = _requests.post(
            _MIMIR_ORLOG_URL,
            json={"question": question},
            timeout=60,
        )
        mimir_response = r.json().get("response", "") if r.ok else f"error:{r.status_code}"
    except Exception as e:
        mimir_response = f"error:{e}"

    entry = {
        "ts":       time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
        "id":       entry_id,
        "question": question[:300],
        "claude":   claude_response,
        "mimir":    mimir_response,
    }
    with _orlog_shadow_log_path.open("a") as f:
        f.write(json.dumps(entry) + "\n")

def _shadow_loki(session_id: str, messages: list, claude_response: str, system: str):
    try:
        r = _requests.post(
            _MIMIR_LOKI_URL,
            json={"messages": messages[:-1], "system": system},
            timeout=5,
        )
        mimir_response = r.json().get("response", "") if r.ok else f"error:{r.status_code}"
    except Exception as e:
        mimir_response = f"error:{e}"

    entry = {
        "ts":             time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
        "session_id":     session_id,
        "user_message":   messages[-2]["content"] if len(messages) >= 2 else "",
        "claude":         claude_response,
        "mimir":          mimir_response,
    }
    with _shadow_log_path.open("a") as f:
        f.write(json.dumps(entry) + "\n")

# Volume-backed session store — survives across containers at scale
# Each session is a small JSON file; TTL cleanup happens on read
_SESSIONS_DIR       = USER_DATA_DIR / "sessions"
_SESSION_TTL        = 3600 * 6   # 6 hours
_ORLOG_SESSIONS_DIR = USER_DATA_DIR / "orlog_sessions"
_ORLOG_SESSION_TTL  = 3600 * 2   # 2 hours — follow-up window
_loki_log_path      = USER_DATA_DIR / "loki_log.jsonl"

_SESSIONS_DIR.mkdir(exist_ok=True)
_ORLOG_SESSIONS_DIR.mkdir(exist_ok=True)


def _orlog_session_path(entry_id: str) -> Path:
    safe = hashlib.sha256(entry_id.encode()).hexdigest()[:24]
    return _ORLOG_SESSIONS_DIR / f"{safe}.json"

def _save_orlog_session(entry_id: str, question: str, clean_question: str, trace: str):
    _orlog_session_path(entry_id).write_text(json.dumps({
        "ts": time.time(),
        "question": question,
        "clean_question": clean_question,
        "trace": trace,
    }))

def _load_orlog_session(entry_id: str) -> dict | None:
    path = _orlog_session_path(entry_id)
    if not path.exists():
        return None
    try:
        data = json.loads(path.read_text())
        if time.time() - data.get("ts", 0) > _ORLOG_SESSION_TTL:
            path.unlink(missing_ok=True)
            return None
        return data
    except Exception:
        return None

def _session_path(session_id: str) -> Path:
    safe = hashlib.sha256(session_id.encode()).hexdigest()[:24]
    return _SESSIONS_DIR / f"{safe}.json"

def _load_session(session_id: str) -> list:
    path = _session_path(session_id)
    if not path.exists():
        return []
    try:
        data = json.loads(path.read_text())
        if time.time() - data.get("ts", 0) > _SESSION_TTL:
            path.unlink(missing_ok=True)
            return []
        return data.get("messages", [])
    except Exception:
        return []

def _save_session(session_id: str, messages: list):
    path = _session_path(session_id)
    path.write_text(json.dumps({"ts": time.time(), "messages": messages[-40:]}))

def _clear_session(session_id: str):
    _session_path(session_id).unlink(missing_ok=True)


def _loki_log(session_id: str, messages: list, answer: str, ready: bool, goal: str):
    entry = {
        "session_id": session_id,
        "ts":         time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
        "turns":      len(messages),
        "ready":      ready,
        "goal":       goal,
        "messages":   messages[-6:],  # last 6 turns for brevity
    }
    with _loki_log_path.open("a") as f:
        f.write(json.dumps(entry) + "\n")


@app.post("/ask")
async def ask(request: Request):
    """Legacy single-turn endpoint — kept for backwards compat."""
    body = await request.json()
    question = body.get("question", "").strip()
    if not question:
        return JSONResponse({"error": "No question provided"}, status_code=400)
    if _over_cap():
        return JSONResponse({"error": "Daily capacity reached. Try again tomorrow."}, status_code=429)
    answer = quality_call(LOKI_SYSTEM, question, 600)
    return {"answer": answer.strip()}


@app.post("/loki")
async def loki(request: Request, background_tasks: BackgroundTasks, authorization: str = Header(default="")):
    """Stateful multi-turn Loki endpoint. Tracks protocol phase per session."""
    body          = await request.json()
    session_id    = body.get("session_id", "")
    message       = _sanitize(body.get("message", "").strip())
    reset         = body.get("reset", False)
    mode          = body.get("mode", None)
    orlog_verdict = (body.get("orlog_verdict") or "").strip()[:400]  # Orlog→Loki resume context

    if not session_id or not message:
        return JSONResponse({"error": "session_id and message required"}, status_code=400)
    if _over_cap():
        return JSONResponse({"error": "Daily capacity reached. Try again tomorrow."}, status_code=429)

    user_id = _verify_clerk_token(authorization)

    ip = _get_client_ip(request)
    if not _check_rate_limit(ip, authenticated=bool(user_id)):
        return JSONResponse({"error": "Too many requests. Slow down."}, status_code=429)

    allowed, used, limit = _check_quota(user_id, "loki")
    if not allowed:
        tier = _get_user_plan(user_id).get("tier", "free") if user_id else "anonymous"
        return JSONResponse({
            "error":   "quota_exceeded",
            "message": f"You've used your {limit} Loki turns for today. Upgrade for unlimited.",
            "used":    used,
            "limit":   limit,
            "tier":    tier,
        }, status_code=429)

    if reset:
        _clear_session(session_id)
    history = _load_session(session_id)

    # Inject Orlog verdict as synthetic context turn so Loki has full picture
    if orlog_verdict and history:
        history.append({
            "role":    "assistant",
            "content": f"[Orlog delivered its verdict: {orlog_verdict}]",
        })

    history.append({"role": "user", "content": message})

    MODE_PREFIXES = {
        "wellspring": (
            "The user has selected WELLSPRING mode: they are exploring and don't yet know what they want. "
            "Open with warmth. Don't ask what decision they're facing — ask what's been on their mind, "
            "what feels unresolved, what they keep coming back to. Build toward a goal through dialogue.\n\n"
        ),
        "crucible": (
            "The user has selected CRUCIBLE mode: they have a real decision and feel pressure behind it. "
            "Open by acknowledging the weight. Ask what the decision is and what's pulling them in both directions. "
            "Move faster than in Wellspring — they're ready to narrow, not explore.\n\n"
        ),
        "forge": (
            "The user has selected FORGE mode: they're building something and got stuck. "
            "Open by asking what they're building and where the block showed up. "
            "Focus on diagnosing the actual sticking point — not the stated one, the real one.\n\n"
        ),
    }
    system_prompt = (MODE_PREFIXES.get(mode, "") + LOKI_SYSTEM) if mode else LOKI_SYSTEM

    answer = None
    backend = "mimir_loki_v2"
    try:
        r = _requests.post(
            _MIMIR_LOKI_URL,
            json={"messages": history, "system": system_prompt},
            timeout=15,
        )
        if r.ok:
            answer = r.json().get("response", "").strip()
        if not answer:
            raise ValueError("empty response")
    except Exception as _e:
        history_text = "\n".join(
            f"{m['role'].upper()}: {m['content']}" for m in history
        )
        answer = quality_call(system_prompt, history_text, 400).strip()
        backend = f"claude_fallback({_e.__class__.__name__})"

    history.append({"role": "assistant", "content": answer})
    _save_session(session_id, history)

    # Detect handoff signal
    ready = "[READY:" in answer
    goal  = ""
    if ready:
        start = answer.index("[READY:") + 7
        end   = answer.index("]", start) if "]" in answer[start:] else len(answer)
        goal  = answer[start:end].strip()

    # Generate brief card — the seam between Loki discovery and Orlog verdict.
    # Runs only once per session at [READY:], using the same Sonnet client.
    brief_card = None
    if ready and goal:
        brief_card = _generate_brief_card(goal, history)

    if user_id:
        _increment_usage(user_id, "loki")

    _loki_log(session_id, history, answer, ready, goal)
    background_tasks.add_task(_shadow_loki, session_id, history, answer, system_prompt)

    # When Loki finds the real question, write a Wyrd entry immediately —
    # even if the user never proceeds to Orlog. Loki usage is the primary
    # signal; don't wait for a verdict that may never come.
    if ready and goal and user_id:
        background_tasks.add_task(
            _wyrd_write_loki,
            user_id=user_id,
            session_id=session_id,
            goal=goal,
            messages=history,
            data_dir=USER_DATA_DIR,
            mode=mode or "",
        )

    return {
        "answer":     answer,
        "ready":      ready,
        "goal":       goal,
        "brief_card": brief_card,
        "turns":      len([m for m in history if m["role"] == "user"]),
    }


@app.post("/loki-quality")
async def loki_quality(request: Request):
    """Direct Loki quality signal — did Loki find the right question?"""
    body       = await request.json()
    session_id = body.get("session_id", "").strip()
    quality    = body.get("quality", "")
    goal       = body.get("goal", "")[:400]
    turns      = body.get("turns", 0)

    if not session_id or quality not in ("up", "down"):
        return JSONResponse({"error": "session_id and quality (up|down) required"}, status_code=400)

    with LOKI_QUALITY_LOG.open("a") as f:
        f.write(json.dumps({
            "loki_session_id": session_id,
            "quality":         quality,
            "goal":            goal,
            "turns":           turns,
            "source":          "direct",
            "ts":              time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
        }) + "\n")

    return {"ok": True}


ROUTE_LOG = USER_DATA_DIR / "route_log.jsonl"


# ── Unified conversation endpoint ──────────────────────────────────────────────

_CONVO_STATE_DIR = USER_DATA_DIR / "convo_state"

def _convo_state_path(session_id: str) -> Path:
    safe = hashlib.sha256(session_id.encode()).hexdigest()[:24]
    return _CONVO_STATE_DIR / f"{safe}.json"

def _load_convo_state(session_id: str) -> dict:
    path = _convo_state_path(session_id)
    if not path.exists():
        return {"phase": "exploring", "goal": None, "brief_card": None, "mode": None}
    try:
        return json.loads(path.read_text())
    except Exception:
        return {"phase": "exploring", "goal": None, "brief_card": None, "mode": None}

def _save_convo_state(session_id: str, state: dict):
    _CONVO_STATE_DIR.mkdir(parents=True, exist_ok=True)
    _convo_state_path(session_id).write_text(json.dumps(state))

def _detect_mode_hint(message: str) -> str | None:
    """Deterministic mode hint from message language — no model call."""
    msg = message.lower()
    pressure_signals = ["deadline", "by tomorrow", "by friday", "by monday", "need to decide", "running out of time", "have to choose", "due date"]
    build_signals = ["building", "coding", "developing", "implementing", "debugging", "stuck on", "cant get", "not working"]
    if any(s in msg for s in pressure_signals):
        return "crucible"
    if any(s in msg for s in build_signals):
        return "forge"
    return None


@app.post("/conversation")
async def conversation(request: Request, background_tasks: BackgroundTasks, authorization: str = Header(default="")):
    """
    Unified conversation endpoint. Single thread — Loki exploration phase,
    then brief card seam, then Orlog verdict. No tab switching required.

    Phases:
      exploring       — Loki is surfacing the real question
      decision_ready  — [READY:] fired, brief card returned, awaiting confirm
      orlog           — user confirmed, Orlog verdict streamed

    External callers (AIOOS, automation): pass context.persona + context.memory
    for domain-specific reasoning without changing the core stack.
    """
    body       = await request.json()
    session_id = body.get("session_id", "").strip()
    message    = _sanitize(body.get("message", "").strip())
    confirmed  = body.get("confirmed", False)   # true → user accepted brief card, run Orlog
    context    = body.get("context", {})        # optional: {persona, memory, domain}
    reset      = body.get("reset", False)

    if not session_id or (not message and not confirmed):
        return JSONResponse({"error": "session_id and message required"}, status_code=400)
    if _over_cap():
        return JSONResponse({"error": "Daily capacity reached. Try again tomorrow."}, status_code=429)

    user_id = _verify_clerk_token(authorization)
    ip      = _get_client_ip(request)

    if not _check_rate_limit(ip, authenticated=bool(user_id)):
        return JSONResponse({"error": "Too many requests. Slow down."}, status_code=429)

    if reset:
        _clear_session(session_id)
        _save_convo_state(session_id, {"phase": "exploring", "goal": None, "brief_card": None, "mode": None})

    state = _load_convo_state(session_id)

    # Detect mode hint from language (deterministic, no model call)
    mode_hint = _detect_mode_hint(message)
    if mode_hint and not state.get("mode"):
        state["mode"] = mode_hint

    # ── Phase: Orlog verdict (user confirmed brief card) ────────────────────────
    if confirmed and not state.get("goal"):
        return JSONResponse({"error": "No decision ready — continue the conversation first."}, status_code=400)
    if confirmed and state.get("goal") and state["phase"] == "decision_ready":
        goal       = state["goal"]
        brief_card = state.get("brief_card") or {}
        loki_ctx   = brief_card.get("summary", "")
        ext_memory = context.get("memory", "")
        if ext_memory:
            loki_ctx = (loki_ctx + "\n\n" + ext_memory).strip()

        # Mark as in-flight
        state["phase"] = "orlog"
        _save_convo_state(session_id, state)

        # Mimir-Orlog first (CPU endpoint, no cold start) → Claude fallback only
        trace = ""
        backend = ""
        try:
            r = _requests.post(_MIMIR_ORLOG_URL, json={"question": goal}, timeout=90)
            if r.ok:
                trace   = r.json().get("response", "")
                backend = "mimir_orlog"
        except Exception:
            pass

        if not trace:
            system = _build_system(loki_context=loki_ctx)
            if context.get("persona"):
                system = context["persona"].strip() + "\n\n" + system
            trace, backend, _model = _fallback_claude(goal, loki_context=loki_ctx, system_override=system)
            backend = f"claude_fallback"

        state["phase"] = "complete"
        _save_convo_state(session_id, state)

        if user_id:
            background_tasks.add_task(_wyrd_write, user_id=user_id, session_id=session_id,
                                      question=goal, verdict=trace, data_dir=USER_DATA_DIR)

        return {
            "mode":     "orlog",
            "response": trace,
            "goal":     goal,
            "backend":  backend,
        }

    # ── Phase: Exploring (Loki turn) ────────────────────────────────────────────
    allowed, used, limit = _check_quota(user_id, "loki")
    if not allowed:
        tier = _get_user_plan(user_id).get("tier", "free") if user_id else "anonymous"
        return JSONResponse({
            "error": "quota_exceeded",
            "message": f"You've used your {limit} Loki turns for today.",
            "used": used, "limit": limit, "tier": tier,
        }, status_code=429)

    history = _load_session(session_id)

    # Inject any external memory as first-turn context for Loki
    ext_memory = context.get("memory", "")
    if ext_memory and not history:
        history.append({"role": "assistant", "content": f"[Context: {ext_memory[:400]}]"})

    history.append({"role": "user", "content": message})

    # Build system prompt: mode prefix + optional persona from context
    mode        = state.get("mode") or body.get("mode")
    MODE_PREFIXES = {
        "wellspring": (
            "The user is exploring and doesn't yet know what they want. "
            "Open with warmth. Ask what's been on their mind, what feels unresolved. "
            "Build toward a goal through dialogue.\n\n"
        ),
        "crucible": (
            "The user has a real decision with pressure behind it. "
            "Acknowledge the weight. Move faster — they're ready to narrow, not explore.\n\n"
        ),
        "forge": (
            "The user is building something and hit a block. "
            "Ask what they're building and where it stopped. Diagnose the real sticking point.\n\n"
        ),
    }
    system_prompt = (MODE_PREFIXES.get(mode, "") + LOKI_SYSTEM) if mode else LOKI_SYSTEM
    if context.get("persona"):
        system_prompt = context["persona"].strip() + "\n\n" + system_prompt

    history_text = "\n".join(
        f"{m['role'].upper()}: {m['content']}" for m in history
    )
    answer = quality_call(system_prompt, history_text, 400).strip()
    history.append({"role": "assistant", "content": answer})
    _save_session(session_id, history)

    ready = "[READY:" in answer
    goal  = ""
    if ready:
        start = answer.index("[READY:") + 7
        end   = answer.index("]", start) if "]" in answer[start:] else len(answer)
        goal  = answer[start:end].strip()

    brief_card = None
    if ready and goal:
        brief_card = _generate_brief_card(goal, history)
        state["phase"]      = "decision_ready"
        state["goal"]       = goal
        state["brief_card"] = brief_card
        _save_convo_state(session_id, state)

    if user_id:
        _increment_usage(user_id, "loki")

    background_tasks.add_task(_shadow_loki, session_id, history, answer, system_prompt)

    if ready and goal and user_id:
        background_tasks.add_task(
            _wyrd_write_loki,
            user_id=user_id, session_id=session_id, goal=goal,
            messages=history, data_dir=USER_DATA_DIR, mode=mode or "",
        )

    return {
        "mode":       "decision_ready" if ready else "exploring",
        "response":   answer,
        "ready":      ready,
        "goal":       goal,
        "brief_card": brief_card,
        "turns":      len([m for m in history if m["role"] == "user"]),
    }


@app.post("/log-route")
async def log_route(request: Request):
    body = await request.json()
    entry = {
        "ts":    body.get("ts", time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())),
        "route": body.get("route"),  # "yes" | "no"
    }
    with ROUTE_LOG.open("a") as f:
        f.write(json.dumps(entry) + "\n")
    return {"ok": True}


@app.get("/user/history")
async def get_user_history(authorization: str = Header(default="")):
    user_id = _verify_clerk_token(authorization)
    if not user_id:
        return JSONResponse({"error": "unauthorized"}, status_code=401)
    return {"history": _load_user_history(user_id), "user_id": user_id}


@app.post("/user/history")
async def save_user_history(request: Request, authorization: str = Header(default="")):
    user_id = _verify_clerk_token(authorization)
    if not user_id:
        return JSONResponse({"error": "unauthorized"}, status_code=401)
    body  = await request.json()
    entry = body.get("entry")
    if not entry or not isinstance(entry, dict):
        return JSONResponse({"error": "entry object required"}, status_code=400)
    entry["ts"] = entry.get("ts") or time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())
    _save_user_history_entry(user_id, entry)
    return {"ok": True}


@app.delete("/user/history")
async def clear_user_history(authorization: str = Header(default="")):
    user_id = _verify_clerk_token(authorization)
    if not user_id:
        return JSONResponse({"error": "unauthorized"}, status_code=401)
    path = _user_history_path(user_id)
    if path.exists():
        path.unlink()
    return {"ok": True}


@app.get("/user/me")
async def get_user_me(authorization: str = Header(default="")):
    """Returns user_id if token is valid — used by frontend to check auth state."""
    user_id = _verify_clerk_token(authorization)
    if not user_id:
        return JSONResponse({"error": "unauthorized"}, status_code=401)
    return {"user_id": user_id}


@app.get("/user/plan")
async def get_user_plan_endpoint(authorization: str = Header(default="")):
    """Returns current tier, usage, and limits for today."""
    user_id = _verify_clerk_token(authorization)
    if not user_id:
        return {
            "tier":   "anonymous",
            "usage":  {"orlog": 0, "loki": 0},
            "limits": {"orlog": _FREE_ORLOG_LIMIT, "loki": _FREE_LOKI_LIMIT},
        }
    plan  = _get_user_plan(user_id)
    usage = _get_daily_usage(user_id)
    tier  = plan.get("tier", "free")
    limits = {"orlog": _PAID_ORLOG_LIMIT, "loki": _PAID_LOKI_LIMIT} if tier == "paid" \
             else {"orlog": _FREE_ORLOG_LIMIT, "loki": _FREE_LOKI_LIMIT}
    return {
        "tier":   tier,
        "usage":  {"orlog": usage.get("orlog", 0), "loki": usage.get("loki", 0)},
        "limits": limits,
    }


@app.post("/subscribe")
async def subscribe(request: Request, authorization: str = Header(default="")):
    """Create a Stripe Checkout session for the monthly plan."""
    user_id = _verify_clerk_token(authorization)
    if not user_id:
        return JSONResponse({"error": "unauthorized"}, status_code=401)
    if not STRIPE_SECRET_KEY or not STRIPE_PRICE_ID:
        return JSONResponse({"error": "Stripe not configured"}, status_code=503)
    try:
        import stripe as _stripe
        _stripe.api_key = STRIPE_SECRET_KEY
        session = _stripe.checkout.Session.create(
            mode="subscription",
            line_items=[{"price": STRIPE_PRICE_ID, "quantity": 1}],
            success_url="https://orlog.fyi/app?upgraded=1",
            cancel_url="https://orlog.fyi/app",
            client_reference_id=user_id,
            metadata={"clerk_user_id": user_id},
        )
        return {"url": session.url}
    except Exception as e:
        import traceback; traceback.print_exc()
        return JSONResponse({"error": str(e), "type": type(e).__name__}, status_code=500)


@app.post("/stripe-webhook")
async def stripe_webhook(request: Request):
    """Handle Stripe subscription lifecycle events."""
    if not STRIPE_WEBHOOK_SECRET:
        return JSONResponse({"error": "not configured"}, status_code=503)
    payload = await request.body()
    sig     = request.headers.get("stripe-signature", "")
    try:
        import stripe as _stripe
        _stripe.api_key = STRIPE_SECRET_KEY
        event = _stripe.Webhook.construct_event(payload, sig, STRIPE_WEBHOOK_SECRET)
    except Exception as e:
        return JSONResponse({"error": str(e)}, status_code=400)

    etype = event["type"]
    if etype == "checkout.session.completed":
        s = event["data"]["object"]
        user_id     = s.get("client_reference_id")
        sub_id      = s.get("subscription")
        customer_id = s.get("customer")
        if user_id and sub_id:
            plan = _get_user_plan(user_id)
            plan.update({"tier": "paid", "stripe_subscription_id": sub_id, "stripe_customer_id": customer_id})
            _set_user_plan(user_id, plan)
            if customer_id:
                _save_stripe_customer(customer_id, user_id)

    elif etype in ("customer.subscription.updated", "customer.subscription.deleted"):
        sub         = event["data"]["object"]
        customer_id = sub.get("customer")
        status      = sub.get("status", "")
        user_id     = _load_stripe_customers().get(customer_id)
        if user_id:
            plan = _get_user_plan(user_id)
            if etype == "customer.subscription.deleted" or status in ("canceled", "unpaid"):
                plan["tier"] = "free"
                plan["stripe_subscription_id"] = None
            else:
                plan["tier"] = "paid"
                plan["stripe_subscription_id"] = sub.get("id")
            _set_user_plan(user_id, plan)

    return {"ok": True}


# ── Wyrd endpoints ──────────────────────────────────────────────────────────

@app.get("/wyrd")
async def get_wyrd(authorization: str = Header(default="")):
    """Return the full accumulated Wyrd profile for the authenticated user."""
    user_id = _verify_clerk_token(authorization)
    if not user_id:
        return JSONResponse({"error": "unauthorized"}, status_code=401)
    profile = _wyrd_profile(user_id, USER_DATA_DIR)
    entries = _load_entries_global(user_id, USER_DATA_DIR)
    profile["entries"] = list(reversed(entries[-20:]))
    return profile


# ── Team Wyrd ────────────────────────────────────────────────────────────────

_TEAMS_FILE = USER_DATA_DIR / "teams.json"

def _load_teams() -> list[dict]:
    if not _TEAMS_FILE.exists():
        return []
    try:
        return json.loads(_TEAMS_FILE.read_text())
    except Exception:
        return []

def _save_teams(teams: list[dict]) -> None:
    _TEAMS_FILE.write_text(json.dumps(teams, indent=2))

def _team_by_code(code: str, teams: list[dict]):
    return next((t for t in teams if t["code"] == code.upper()), None)

def _team_by_member(user_id: str, teams: list[dict]):
    return next((t for t in teams if user_id in t["members"]), None)

def _cluster_tensions(tensions: list[str], member_ids: list[str]) -> list[dict]:
    """Semantic clustering of tension phrases via Haiku. Returns ranked clusters."""
    if len(tensions) < 2:
        return []

    numbered = "\n".join(f"{i+1}. {t}" for i, t in enumerate(tensions))
    unique_members = list(dict.fromkeys(member_ids))

    prompt = f"""You have {len(tensions)} tension phrases from {len(unique_members)} people on the same team.
Each person used an AI reasoning tool independently — they never compared notes.

Tensions:
{numbered}

Cluster these by shared underlying conflict, even when phrasings are completely different.
Also detect fault lines: tensions that directly OPPOSE each other (one person wants more X, another less X).

Return ONLY a JSON array (no markdown):
[
  {{
    "theme": "<underlying conflict in 5-8 words>",
    "tension_indices": [<1-based>],
    "surprise": <0.0-1.0 — how different were the phrasings? 1.0 = same conflict, opposite words>,
    "fault_line": <true if the tensions oppose each other>,
    "insight": "<one sentence: what this reveals about the team dynamics>"
  }}
]

Only include clusters where 2+ tensions share a theme. Sort by surprise descending. Max 6 clusters."""

    raw = fast_call("", prompt, 1200).strip()
    if raw.startswith("```"):
        raw = raw.split("```", 2)[1]
        if raw.startswith("json"):
            raw = raw[4:]
    clusters = json.loads(raw)

    # Enrich with example phrases and real member counts
    for c in clusters:
        phrases, members_in = [], set()
        for idx in c.get("tension_indices", []):
            if 1 <= idx <= len(tensions):
                phrases.append(tensions[idx - 1])
                members_in.add(member_ids[idx - 1])
        c["phrases"]      = phrases[:3]
        c["member_count"] = len(members_in)

    return [c for c in clusters if c.get("member_count", 0) >= 2]

def _find_convergence(entries_by_member: dict[str, list]) -> list[dict]:
    """Find topics both members brought in within 7 days of each other (independently)."""
    from datetime import timedelta
    events = []
    for uid, entries in entries_by_member.items():
        for e in entries:
            events.append({"uid": uid, "topic": e.get("topic"), "ts": e.get("ts",""), "tension": e.get("tension","")})
    events.sort(key=lambda x: x["ts"])

    convergences = []
    for i, a in enumerate(events):
        for b in events[i+1:]:
            if a["uid"] == b["uid"] or a["topic"] != b["topic"]:
                continue
            try:
                from datetime import datetime, timezone
                ta = datetime.fromisoformat(a["ts"])
                tb = datetime.fromisoformat(b["ts"])
                if abs((tb - ta).days) <= 7:
                    convergences.append({
                        "topic": a["topic"],
                        "tension_a": a["tension"],
                        "tension_b": b["tension"],
                        "days_apart": abs((tb - ta).days),
                    })
            except Exception:
                pass
    seen = set()
    out = []
    for c in convergences:
        key = c["topic"]
        if key not in seen:
            seen.add(key)
            out.append(c)
    return out[:3]

def _find_shared_anchors(entries_by_member: dict[str, list]) -> list[str]:
    """Surface anchor phrases that appear across 2+ members."""
    from collections import Counter
    anchor_members: dict[str, set] = {}
    for uid, entries in entries_by_member.items():
        for e in entries:
            a = (e.get("anchor") or "").strip().lower()
            if a and len(a) > 8:
                anchor_members.setdefault(a, set()).add(uid)
    return [a for a, members in anchor_members.items() if len(members) >= 2]


@app.post("/team/create")
async def team_create(authorization: str = Header(default="")):
    import secrets, string
    user_id = _verify_clerk_token(authorization)
    if not user_id:
        return JSONResponse({"error": "auth required"}, status_code=401)

    teams = _load_teams()
    existing = _team_by_member(user_id, teams)
    if existing:
        return {"team_id": existing["id"], "code": existing["code"],
                "members": len(existing["members"]), "already_exists": True}

    alphabet = string.ascii_uppercase + string.digits
    code = ''.join(secrets.choice(alphabet) for _ in range(6))
    while _team_by_code(code, teams):
        code = ''.join(secrets.choice(alphabet) for _ in range(6))

    from datetime import datetime, timezone
    team = {
        "id":         "team_" + secrets.token_hex(8),
        "code":       code,
        "members":    [user_id],
        "created_by": user_id,
        "created_at": datetime.now(timezone.utc).isoformat(),
    }
    teams.append(team)
    _save_teams(teams)
    return {"team_id": team["id"], "code": code, "members": 1}


@app.post("/team/join")
async def team_join(request: Request, authorization: str = Header(default="")):
    user_id = _verify_clerk_token(authorization)
    if not user_id:
        return JSONResponse({"error": "auth required"}, status_code=401)

    body = await request.json()
    code = body.get("code", "").strip().upper()
    if not code:
        return JSONResponse({"error": "code required"}, status_code=400)

    teams = _load_teams()
    existing = _team_by_member(user_id, teams)
    if existing:
        if existing["code"] == code:
            return {"team_id": existing["id"], "code": code,
                    "members": len(existing["members"]), "joined": False}
        return JSONResponse({"error": "already_on_team",
                             "message": "You're already on a team. Leave first."}, status_code=400)

    team = _team_by_code(code, teams)
    if not team:
        return JSONResponse({"error": "not_found",
                             "message": "No team found with that code."}, status_code=404)

    if user_id not in team["members"]:
        team["members"].append(user_id)
        _save_teams(teams)

    return {"team_id": team["id"], "code": code,
            "members": len(team["members"]), "joined": True}


@app.post("/team/leave")
async def team_leave(authorization: str = Header(default="")):
    user_id = _verify_clerk_token(authorization)
    if not user_id:
        return JSONResponse({"error": "auth required"}, status_code=401)

    teams = _load_teams()
    team  = _team_by_member(user_id, teams)
    if not team:
        return JSONResponse({"error": "not_on_team"}, status_code=404)

    team["members"] = [m for m in team["members"] if m != user_id]
    if not team["members"]:
        teams = [t for t in teams if t["id"] != team["id"]]
    _save_teams(teams)
    return {"left": True}


@app.get("/team/wyrd")
async def get_team_wyrd(authorization: str = Header(default="")):
    """Aggregate Team Wyrd: semantic clusters, fault lines, convergence moments, shared anchors."""
    user_id = _verify_clerk_token(authorization)
    if not user_id:
        return JSONResponse({"error": "auth required"}, status_code=401)

    teams = _load_teams()
    team  = _team_by_member(user_id, teams)
    if not team:
        return JSONResponse({"error": "not_on_team",
                             "message": "You're not on a team yet."}, status_code=404)

    entries_by_member: dict[str, list] = {}
    member_summaries = []
    for mid in team["members"]:
        entries = _load_entries_global(mid, USER_DATA_DIR)
        entries_by_member[mid] = entries
        heat_avg = round(sum(e.get("heat",0) for e in entries) / len(entries), 2) if entries else 0.0
        member_summaries.append({
            "id":       mid[:8] + "…",   # truncated for privacy
            "sessions": len(entries),
            "heat_avg": heat_avg,
            "is_you":   mid == user_id,
        })

    # Collect all tensions with member attribution
    all_tensions, all_member_ids = [], []
    for mid, entries in entries_by_member.items():
        for e in entries:
            t = (e.get("tension") or "").strip()
            if t:
                all_tensions.append(t)
                all_member_ids.append(mid)

    thin = len(all_tensions) < 4
    clusters, convergence, shared_anchors = [], [], []

    if not thin:
        try:
            clusters = _cluster_tensions(all_tensions, all_member_ids)
        except Exception:
            clusters = []
        convergence    = _find_convergence(entries_by_member)
        shared_anchors = _find_shared_anchors(entries_by_member)

    return {
        "team_id":       team["id"],
        "code":          team["code"],
        "members":       len(team["members"]),
        "member_summaries": member_summaries,
        "clusters":      clusters,
        "fault_lines":   [c for c in clusters if c.get("fault_line")],
        "convergence":   convergence,
        "shared_anchors": shared_anchors,
        "thin":          thin,
    }


@app.get("/wyrd/lore")
async def get_lore_opening(authorization: str = Header(default="")):
    """
    Return Lore's session-opening line — the thread Orlog noticed from past sessions.
    Returns null if the user is too new (< 3 sessions).
    """
    user_id = _verify_clerk_token(authorization)
    if not user_id:
        return JSONResponse({"error": "unauthorized"}, status_code=401)
    opening = _lore_opening(user_id, USER_DATA_DIR)
    return {"lore": opening}


@app.get("/developers")
async def developers_page():
    return FileResponse(str(Path(__file__).parent / "static" / "developers.html"))


@app.post("/api/request-key")
async def request_api_key(request: Request):
    body = await request.json()
    email = (body.get("email") or "").strip().lower()
    if not email or "@" not in email:
        return JSONResponse({"error": "invalid email"}, status_code=400)
    log_path = Path(__file__).parent.parent.parent / "logs" / "api_key_requests.json"
    try:
        existing = json.loads(log_path.read_text()) if log_path.exists() else []
    except Exception:
        existing = []
    import datetime
    existing.append({"email": email, "ts": datetime.datetime.utcnow().isoformat(), "status": "pending"})
    log_path.write_text(json.dumps(existing, indent=2))
    return {"ok": True}


app.mount("/", StaticFiles(directory=str(Path(__file__).parent / "static"), html=True), name="static")
