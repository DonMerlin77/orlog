# Orlog Architecture
> Living doc. Update when a model is trained, endpoint added, or major feature ships.
> This file exists so Claude can build without re-reading code every session.

---

## Product Vision

Orlog is a multi-perspective decision reasoning engine. Users bring a real decision → Loki surfaces the real question → Orlog council deliberates from competing angles → verdict takes a clear position.

Long-term target: **AIOOS** — Mimir as the reasoning layer for NPCs, agents, and external tools via API/MCP.

**IMPORTANT — two separate systems live in this repo (added 2026-07-10, Fable audit):**
Production (`app/main.py`, the thing users actually hit) does NOT run the multi-agent
council live per request. `/analyze` makes a single call to the trained Mimir-Orlog
model (`_MIMIR_ORLOG_URL`), falling back to Claude on failure — the "council-style"
ANCHOR/TENSION/BLIND SPOT/VERDICT output format is a behavior the model LEARNED from
training data, not something re-executed live. The actual multi-agent debate (`core.py`,
`council/`, `brain/`, `goal/`) is a separate, currently-dormant research harness (last
active log: 2026-05-24) whose real job is running offline on Modal cron
(`eval/modal_cron.py`) to generate the DPO training pairs that produced Mimir-Orlog in
the first place. Don't assume "Live: Orlog council" below means a live debate happens
per request -- it means the model trained on debate-generated data is live.
Known issue in that offline pipeline: `eval/auto_score.py` scores council output using
the same model family that generated it, which then feeds `dpo_usable` for training --
a self-grading loop with no external ground truth in the loop. Fix is prioritized but
not yet done; see project memory for details before trusting the next DPO batch blind.

---

## Model Stack (Mimir)

| Model | Base | Size | Trained On | Checkpoint | Endpoint | Status |
|---|---|---|---|---|---|---|
| Mimir-Brief v1 | Qwen2.5-1.5B | 1.5B | 240 SFT pairs | `/checkpoints/brief_v1/adapter` | `mimir-brief-serve` | live |
| Mimir-Loki v1 small | Qwen2.5-1.5B | 1.5B | 400 DPO pairs | `/checkpoints/loki_v1_small` | `mimir-classifier` | live (shadow) |
| Mimir-Gate v1 small | Qwen2.5-1.5B | 1.5B | 200 DPO pairs | `/checkpoints/gate_v1_small` | `mimir-classifier` | live |
| Mimir-Loki v3 | Qwen2.5-7B | 7B | 800 DPO pairs | `/checkpoints/loki_v3` | `mimir-loki-serve` | live (shadow) |
| Mimir-Gate v1 | Qwen2.5-7B | 7B | 200 DPO pairs | `/checkpoints/gate_v1` | `mimir-gate-serve` | live |
| Mimir-Orlog v2 | Qwen2.5-7B | 7B | 535 DPO pairs | `/checkpoints/orlog_v2` | `mimir-orlog-serve` | live |
| Mimir-Wyrd v1 | Qwen2.5-1.5B | 1.5B | 199 DPO pairs | `/checkpoints/wyrd_v1_small` | `mimir-classifier` | live |
| **Mimir-Loki v2** | Qwen2.5-1.5B | 1.5B | 509 real pairs | TBD | TBD | **TO TRAIN** |

**Primary inference path:**
```
/conversation (Loki)
  → Mimir-Classifier (Gate + Loki extract, 1.5B CPU, always warm)
    → is_decision=false: Loki responds via Claude (fallback) or Mimir-Loki v2 (goal)
    → is_decision=true: pass clean question to Orlog council

/analyze (Orlog council)
  → Answerability gate (Haiku or Mimir-Answerability)
  → Council deliberation (Claude Sonnet — target: replace with Mimir-Orlog)
  → Synthesis → verdict
```

**Goal state (no Claude in production):**
```
User → Loki (Mimir-Loki v2, CPU) → Orlog (Mimir-Orlog, GPU) → verdict
```

---

## Modal Deployments

| App Name | URL | File | Purpose |
|---|---|---|---|
| `orlog-web` | `https://tomsem77--orlog-web-serve.modal.run` | `orlog/app/modal_web.py` | Main FastAPI app |
| `mimir-mcp` | `https://tomsem77--mimir-mcp-serve.modal.run` | `orlog/app/mcp_server.py` | MCP server for Claude integration |
| `mimir-brief` | `https://tomsem77--mimir-brief-serve.modal.run` | `orlog/app/mimir_brief_serve.py` | Brief card generation |
| `mimir-classifier` | internal | `mimir/modal_train.py` | Gate + Loki + Wyrd classifiers (CPU) |
| `mimir-loki-serve` | internal | `mimir/modal_train.py` | Mimir-Loki 7B (A10G) |
| `mimir-orlog-serve` | internal | `mimir/modal_train.py` | Mimir-Orlog 7B (A10G) |

**Deploy commands:**
```bash
modal deploy orlog/app/modal_web.py          # main app
modal deploy orlog/app/mcp_server.py         # MCP server
modal deploy orlog/app/mimir_brief_serve.py  # brief endpoint
```

**Volumes:**
- `orlog-user-data` → `/app/user_data` — user Wyrd profiles, session history
- `mimir-checkpoints` → `/checkpoints` — all trained model adapters

---

## App Endpoints (main.py)

| Method | Path | What it does |
|---|---|---|
| POST | `/analyze` | Full Orlog council run → verdict |
| POST | `/analyze/stream` | Streaming version of /analyze |
| POST | `/conversation` | Loki conversation (multi-turn) |
| POST | `/loki` | Single Loki turn |
| POST | `/decide` | Gate check → route to Orlog or nudge |
| POST | `/ask` | Single question (legacy) |
| POST | `/subscribe` | Stripe checkout session |
| POST | `/stripe-webhook` | Stripe lifecycle events |
| POST | `/api/request-key` | MCP API key request (logs to file) |
| GET | `/wyrd` | User's Wyrd profile |
| GET | `/wyrd/lore` | Lore opening line for returning users |
| GET | `/user/me` | Current user info |
| GET | `/user/plan` | User tier (free/paid) |
| GET | `/developers` | /developers page |
| GET | `/health` | Health check |

---

## Data Pipeline

```
Training data sources:
  mimir/real_loki_pairs.jsonl     ← real conversations (Claude Code + Claude.ai + ChatGPT)
  mimir/loki_dpo_pairs.jsonl      ← synthetic DPO pairs
  mimir/gate_dpo_pairs.jsonl      ← gate classifier pairs
  mimir/brief_sft_pairs.jsonl     ← brief card SFT pairs (240 pairs)
  mimir/orlog_dpo_pairs.jsonl     ← council verdict pairs
  mimir/wyrd_pairs.py             ← Wyrd session labeling pairs

Extraction pipeline (real data):
  mimir/parse_claude_sessions.py  ← Claude Code JSONL → Loki DPO pairs
  mimir/parse_claude_ai.py        ← Claude.ai export → Loki DPO pairs
  [TODO] mimir/parse_chatgpt.py   ← ChatGPT export → Loki DPO pairs
  mimir/score_cache.json          ← scoring cache (avoids re-scoring = saves API cost)

Training:
  mimir/modal_train.py            ← SFT + DPO training on Modal GPU (A10G)
  mimir/synthetic_brief_pairs.py  ← generates Brief SFT pairs
  mimir/reddit_loki_pairs.py      ← generates Loki pairs from Reddit
```

---

## Feature Map

### Live
- Loki conversation (multi-turn, session-backed on Modal volume)
- Orlog verdict (Mimir-Orlog model, trained on offline council-generated data -- see note above, no live multi-agent debate per request)
- Gate classifier (is this a real decision?)
- Answerability gate (is this answerable?)
- Wyrd memory layer (cross-session profile per user)
- Lore thread (surfaces recurring tensions for returning users)
- Stripe subscriptions (Free: 1 Orlog/10 Loki, Pro $8/mo: 10 Orlog/unlimited)
- Clerk auth (JWT, per-user quotas)
- MCP server (orlog_decide + orlog_explore tools for Claude)
- /developers page (API key request form)
- Listed on mcp.so marketplace
- Brief card generation (Mimir-Brief v1, SFT)
- Rate limiting (10/60s anon, 60/60s auth)
- Prompt injection sanitization

### Mimir (model autonomy)
- Gate v1 small: live, production
- Loki v1 small: live, shadow-logging (not yet primary)
- Brief v1: live
- Orlog v2: live, GPU cold-start
- **Next: Loki v2 (509 real pairs) — train + flip to primary**

### To Build
- `parse_chatgpt.py` — ChatGPT export parser
- Flip Mimir-Loki to primary on `/conversation` (remove Claude fallback)
- Decision Service API — clean POST endpoint for AIOOS/NPC use
- Reddit post for first real users
- Session agent (auto-updates SESSION.md)
- Mimir-Orlog replacement for Claude council calls

### Deferred
- Wyrd panel UI (backend exists, no frontend)
- Mahoraga meta-adaptation
- Archetype routing (Wellspring/Crucible/Forge/Void)
- Team Wyrd (built, not exposed)
- Mobile
- Custom domain email

---

## Key Costs

| Thing | Cost | Notes |
|---|---|---|
| Modal mimir-classifier | ~$40/mo fixed | CPU, min_containers=1, always warm |
| Modal orlog-web | ~$0.002/verdict | A10G, cold-start amortized |
| Claude Sonnet (council) | ~$0.006/verdict | Target: $0 when Mimir-Orlog is primary |
| Claude Haiku (fallback) | ~$0.001/Loki turn | Target: $0 when Mimir-Loki v2 is primary |
| Stripe | 2.9% + $0.30/txn | Only on paid subscriptions |

---

## MCP Integration

**Server:** `https://tomsem77--mimir-mcp-serve.modal.run/sse`
**Tools:** `orlog_decide(question)`, `orlog_explore(message, session_id)`
**Auth:** `X-API-Key` header
**Key store:** Modal Dict `mcp-api-keys`
**Tiers:** Free (10/day), Paid (500/day)
**Generate key:** `modal run orlog/app/mcp_server.py::generate_key`
**Listed:** mcp.so marketplace

---

## AIOOS Path

1. Decision Service API — simple POST, <2s response, no SSE
2. Mimir-Orlog on CPU (quantized) — latency target <2s for NPC use
3. Coeus prototype — one NPC wired to Mimir in Unreal
4. SDK / plugin packaging
