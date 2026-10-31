"""
orlog/interface/api.py — REST API interface for Orlog.

Exposes the full Orlog pipeline over HTTP so any external caller
(web app, CLI tool, other services) can use it without importing Python.

Requires: pip install fastapi uvicorn
Start:    python3 -m orlog.interface.api
          uvicorn orlog.interface.api:app --host 0.0.0.0 --port 8000

Endpoints:
  POST /run          — full Orlog pipeline (goal + question → verdict)
  POST /build        — generate project scaffolding from a run result
  POST /score        — label a run with an outcome signal
  GET  /stats        — router stats + scheduler state
  GET  /pairs        — export current DPO pairs
  GET  /health       — liveness check
"""

import os
from pathlib import Path
from dotenv import load_dotenv

load_dotenv(Path(__file__).parent.parent.parent / ".env")

try:
    from fastapi import FastAPI, HTTPException
    from fastapi.middleware.cors import CORSMiddleware
    from pydantic import BaseModel
except ImportError:
    raise ImportError("pip install fastapi uvicorn to use the Orlog API")

from orlog.core import Orlog
from orlog.eval.outcome import SIGNAL_TYPES

app = FastAPI(
    title="Orlog",
    description="Domain-agnostic AI operating system — deliberate, build, learn.",
    version="0.1.0",
)

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_methods=["*"],
    allow_headers=["*"],
)


# ── Request / response models ─────────────────────────────────────────────────

class RunRequest(BaseModel):
    goal:      str
    question:  str
    domain:    str  = None
    task_type: str  = "general"
    context:   dict = {}
    auto_score: bool = True

class BuildRequest(BaseModel):
    goal:       str
    domain:     str
    run_id:     str
    output_dir: str = "/tmp/orlog_scaffold"
    heal:       bool = True

class ScoreRequest(BaseModel):
    goal:        str
    domain:      str
    run_id:      str
    signal_type: str
    value:       float
    baseline:    float = 0.0
    target:      float = 1.0
    window_days: int   = 7
    notes:       str   = ""

class RunResponse(BaseModel):
    id:          str
    domain:      str
    task_type:   str
    verdict:     str
    key_tension: str
    blind_spot:  str
    orlog_score: float = None
    lenses:      list[str]
    state:       str

class BuildResponse(BaseModel):
    output_dir:  str
    loop_status: str
    files:       list[str]

class ScoreResponse(BaseModel):
    run_id:      str
    signal_type: str
    orlog_score: float
    recorded_at: str


# ── Helpers ───────────────────────────────────────────────────────────────────

def _orlog(goal: str, domain: str = None) -> Orlog:
    return Orlog(goal=goal, domain=domain, api_key=os.getenv("ANTHROPIC_API_KEY"))


# ── Endpoints ─────────────────────────────────────────────────────────────────

@app.get("/health")
def health():
    return {"status": "ok", "version": "0.1.0"}


@app.post("/run", response_model=RunResponse)
def run(req: RunRequest):
    try:
        o      = _orlog(req.goal, req.domain)
        result = o.run(
            question=req.question,
            task_type=req.task_type,
            context=req.context or None,
            auto_score=req.auto_score,
        )
        s = result.get("synthesis", {})
        return RunResponse(
            id=result["id"],
            domain=result["domain"],
            task_type=result["task_type"],
            verdict=s.get("verdict", ""),
            key_tension=s.get("key_tension", ""),
            blind_spot=s.get("blind_spot", ""),
            orlog_score=result.get("orlog_score"),
            lenses=result.get("lenses", []),
            state=result.get("perspectives", {}).get("state", {}).get("state", "stable")
                  if isinstance(result.get("perspectives"), dict) else "stable",
        )
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


@app.post("/build", response_model=BuildResponse)
def build(req: BuildRequest):
    import json, os as _os
    try:
        # Reconstruct a minimal result dict from stored run log
        o    = _orlog(req.goal, req.domain)
        runs = o._load_runs()
        run  = next((r for r in runs if r.get("id") == req.run_id), None)
        if not run:
            raise HTTPException(status_code=404, detail=f"Run {req.run_id} not found")

        result = o.build(run, output_dir=req.output_dir, heal=req.heal)

        files = []
        for root, _, fnames in _os.walk(result["output_dir"]):
            for f in fnames:
                files.append(_os.path.join(root, f).replace(result["output_dir"] + "/", ""))

        return BuildResponse(
            output_dir=result["output_dir"],
            loop_status=result["loop_report"].get("final_status", "unknown"),
            files=sorted(files),
        )
    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


@app.post("/score", response_model=ScoreResponse)
def score(req: ScoreRequest):
    if req.signal_type not in SIGNAL_TYPES:
        raise HTTPException(status_code=400, detail=f"signal_type must be one of {SIGNAL_TYPES}")
    try:
        o     = _orlog(req.goal, req.domain)
        entry = o.record_outcome(
            run_id=req.run_id,
            signal_type=req.signal_type,
            value=req.value,
            baseline=req.baseline,
            target=req.target,
            window_days=req.window_days,
            notes=req.notes,
        )
        return ScoreResponse(
            run_id=entry["run_id"],
            signal_type=entry["signal_type"],
            orlog_score=entry["orlog_score"],
            recorded_at=entry["recorded_at"],
        )
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


@app.get("/stats")
def stats():
    try:
        from orlog.brain.router import Router
        from orlog.scheduler.scheduler import scheduler_stats
        from orlog.eval.dpo import synthesis_pairs

        r = Router()
        return {
            "router":    r.stats(),
            "scheduler": scheduler_stats(),
            "dpo_pairs": len(synthesis_pairs()),
        }
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


@app.get("/pairs")
def pairs(min_gap: float = 1.0):
    try:
        from orlog.eval.dpo import synthesis_pairs, router_pairs
        s = synthesis_pairs(min_gap=min_gap)
        r = router_pairs(min_gap=min_gap)
        return {
            "synthesis_pairs": len(s),
            "router_pairs":    len(r),
            "total":           len(s) + len(r),
            "min_gap":         min_gap,
        }
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


# ── Entry point ───────────────────────────────────────────────────────────────

if __name__ == "__main__":
    import uvicorn
    uvicorn.run("orlog.interface.api:app", host="0.0.0.0", port=8000, reload=False)
