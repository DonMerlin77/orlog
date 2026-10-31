"""
orlog/app/mcp_server.py — Orlog MCP Server

Exposes Orlog's decision reasoning as MCP tools Claude can call natively.

Deploy:
    modal deploy orlog/app/mcp_server.py

Generate an API key:
    modal run orlog/app/mcp_server.py::generate_key           # free tier
    modal run orlog/app/mcp_server.py::generate_key --tier paid

Claude config (~/.claude.json or Claude Desktop settings):
    {
      "mcpServers": {
        "orlog": {
          "type": "sse",
          "url": "https://tomsem77--mimir-mcp-serve.modal.run/sse",
          "headers": { "X-API-Key": "ok_your-key-here" }
        }
      }
    }
"""

import contextvars
import json
import secrets
import uuid
from datetime import date

import httpx
import modal

# ── Modal app ─────────────────────────────────────────────────────────────────

app   = modal.App("mimir-mcp")
image = (
    modal.Image.debian_slim(python_version="3.11")
    .pip_install(
        "fastapi>=0.110.0",
        "uvicorn>=0.29.0",
        "mcp>=1.3.0",
        "httpx>=0.27.0",
        "starlette>=0.36.0",
        "anyio>=4.0.0",
    )
)

# Persistent key store: api_key → {tier, calls_today, date}
key_store = modal.Dict.from_name("mcp-api-keys", create_if_missing=True)

ORLOG_BASE = "https://tomsem77--orlog-web-serve.modal.run"
FREE_LIMIT  = 10
PAID_LIMIT  = 500

# Per-request context so tools can read the validated key without it being a param
_api_key_ctx: contextvars.ContextVar[str] = contextvars.ContextVar("api_key", default="")


# ── Auth helpers ──────────────────────────────────────────────────────────────

def _validate_key(api_key: str) -> dict | None:
    try:
        return key_store.get(api_key)
    except Exception:
        return None


def _check_and_increment(api_key: str) -> tuple[bool, str]:
    info = _validate_key(api_key)
    if not info:
        return False, "Invalid API key. Get one at orlog.fyi/developers"
    today = str(date.today())
    if info.get("date") != today:
        info["calls_today"] = 0
        info["date"] = today
    limit = PAID_LIMIT if info.get("tier") == "paid" else FREE_LIMIT
    if info["calls_today"] >= limit:
        tier = info.get("tier", "free")
        upgrade = "" if tier == "paid" else " Upgrade at orlog.fyi."
        return False, f"Daily limit reached ({limit} calls/day).{upgrade}"
    info["calls_today"] += 1
    key_store[api_key] = info
    return True, ""


# ── MCP server ────────────────────────────────────────────────────────────────

@app.function(
    image=image,
    min_containers=0,
    max_containers=20,
    timeout=120,
)
@modal.concurrent(max_inputs=100)
@modal.asgi_app()
def serve():
    from fastapi import FastAPI, Request
    from fastapi.middleware.cors import CORSMiddleware
    from fastapi.responses import JSONResponse
    from mcp.server.fastmcp import FastMCP

    mcp = FastMCP(
        "Orlog",
        instructions=(
            "Orlog is a multi-perspective decision reasoning engine. "
            "Use orlog_decide when the user has a real choice to make. "
            "Use orlog_explore when they seem stuck or unclear about what they're actually deciding. "
            "Always pass the api_key you received from the request context."
        ),
    )

    @mcp.tool()
    async def orlog_decide(question: str) -> str:
        """
        Get multi-perspective reasoning on a decision.

        Use when the user has a clear choice with real tension — career moves,
        financial decisions, relationship trade-offs, strategic pivots.
        Orlog examines competing angles and takes a clear position.

        Args:
            question: The decision or situation to reason about. Be specific —
                      include stakes, what pulls each way, and what makes it hard.
        """
        api_key = _api_key_ctx.get()
        allowed, err = _check_and_increment(api_key)
        if not allowed:
            return f"[Orlog] {err}"
        async with httpx.AsyncClient() as client:
            try:
                r = await client.post(
                    f"{ORLOG_BASE}/analyze",
                    json={"question": question, "skip_extract": False},
                    timeout=90,
                )
                data = r.json()
                if "error" in data:
                    return f"[Orlog] {data['error']}"
                return data.get("trace", "[Orlog] No verdict returned.")
            except Exception as e:
                return f"[Orlog] Reasoning failed: {e}"

    @mcp.tool()
    async def orlog_explore(message: str, session_id: str = "") -> str:
        """
        Have a conversation to surface the real question behind a situation.

        Use when the user seems stuck, unclear, or hasn't named what they're
        actually deciding. Loki will help them find the real tension. Pass the
        returned session_id on follow-up turns to continue the same thread.
        When the response contains [DECISION READY], call orlog_decide with
        the stated question.

        Args:
            message: What the user wants to think through
            session_id: Continue an existing thread (from prior orlog_explore call)
        """
        api_key = _api_key_ctx.get()
        allowed, err = _check_and_increment(api_key)
        if not allowed:
            return f"[Orlog] {err}"
        sid = session_id or f"mcp_{uuid.uuid4().hex[:8]}"
        async with httpx.AsyncClient() as client:
            try:
                r = await client.post(
                    f"{ORLOG_BASE}/conversation",
                    json={"session_id": sid, "message": message},
                    timeout=60,
                )
                data = r.json()
                if "error" in data:
                    return f"[Orlog] {data['error']}"
                response = data.get("response", "")
                out = f"[session:{sid}]\n\n{response}"
                if data.get("ready") and data.get("goal"):
                    out += f'\n\n[DECISION READY — call orlog_decide("{data["goal"]}")  ]'
                return out
            except Exception as e:
                return f"[Orlog] Explore failed: {e}"

    # ── ASGI wiring with auth middleware ─────────────────────────────────────

    mcp_app = mcp.sse_app()

    api = FastAPI(title="Orlog MCP", version="1.0")
    api.add_middleware(
        CORSMiddleware,
        allow_origins=["*"],
        allow_headers=["*"],
        allow_methods=["*"],
    )

    @api.middleware("http")
    async def auth_middleware(request: Request, call_next):
        # Health check — no auth needed
        if request.url.path in ("/health", "/ping"):
            return await call_next(request)
        api_key = request.headers.get("x-api-key", "").strip()
        if not api_key:
            return JSONResponse(
                {"error": "X-API-Key header required. Get a key at orlog.fyi/developers"},
                status_code=401,
            )
        info = _validate_key(api_key)
        if info is None:
            return JSONResponse({"error": "Invalid API key."}, status_code=401)
        _api_key_ctx.set(api_key)
        return await call_next(request)

    @api.get("/health")
    def health():
        return {"status": "ok", "product": "orlog-mcp", "version": "1.0"}

    @api.get("/ping")
    def ping():
        return "pong"

    api.mount("/", mcp_app)
    return api


# ── Key management — run locally ──────────────────────────────────────────────

@app.local_entrypoint()
def generate_key(tier: str = "free"):
    """
    Generate an API key.
        modal run orlog/app/mcp_server.py::generate_key
        modal run orlog/app/mcp_server.py::generate_key --tier paid
    """
    key = "ok_" + secrets.token_urlsafe(24)
    key_store[key] = {"tier": tier, "calls_today": 0, "date": str(date.today())}
    limit = PAID_LIMIT if tier == "paid" else FREE_LIMIT
    print(f"\nOrlog API Key ({tier}, {limit} calls/day):")
    print(f"  {key}\n")
    print("Add to Claude config:")
    print(json.dumps({
        "mcpServers": {
            "orlog": {
                "type": "sse",
                "url": "https://tomsem77--mimir-mcp-serve.modal.run/sse",
                "headers": {"X-API-Key": key},
            }
        }
    }, indent=2))


@app.local_entrypoint()
def list_keys():
    """List all API keys: modal run orlog/app/mcp_server.py::list_keys"""
    for key, info in key_store.items():
        print(f"  {key[:12]}...  tier={info.get('tier')}  today={info.get('calls_today')}/{PAID_LIMIT if info.get('tier')=='paid' else FREE_LIMIT}")


@app.local_entrypoint()
def revoke_key(key: str):
    """Revoke a key: modal run orlog/app/mcp_server.py::revoke_key --key ok_..."""
    try:
        del key_store[key]
        print(f"Revoked: {key[:12]}...")
    except Exception:
        print("Key not found.")
