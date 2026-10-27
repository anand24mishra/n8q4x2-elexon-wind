"""Production API: Elexon-only historical forecast analysis with atomic file cache.

Vercel serverless compatibility notes
--------------------------------------
- VERCEL env var is injected automatically by Vercel's runtime.
- Cache is written to /tmp (only writable path in Lambda).  Docker uses
  /app/data via CACHE_FILE env var or the default path.
- asyncio.create_task() tasks are silently dropped when a Vercel function
  returns its HTTP response, so POST /api/refresh awaits refresh_data()
  synchronously on Vercel instead of backgrounding it.
"""
from __future__ import annotations

import asyncio
import json
import os
from datetime import datetime, timezone
from pathlib import Path

from fastapi import FastAPI, HTTPException, Query
from fastapi.middleware.cors import CORSMiddleware
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel

from .domain import analyze, decision_risk_brief, normalize, walk_forward_backtest
from .elexon import SOURCES, load_january

app = FastAPI(title="Windward | Elexon Wind Intelligence", version="2.0.0", description="January 2026 decision-time GB wind forecast evaluation")
app.add_middleware(CORSMiddleware, allow_origins=[v.strip() for v in os.getenv("CORS_ORIGINS", "http://localhost:5173,http://localhost:8080").split(",")], allow_methods=["GET", "POST"], allow_headers=["*"])
# On Vercel the only writable filesystem path is /tmp.  Docker uses the
# volume-mounted path configured via CACHE_FILE env var.
_IS_VERCEL = bool(os.getenv("VERCEL"))
_DEFAULT_CACHE = (
    "/tmp/elexon-january-2026.json" if _IS_VERCEL
    else "/app/data/elexon-january-2026.json"
)
CACHE_FILE = Path(os.getenv("CACHE_FILE", _DEFAULT_CACHE))
state = {"status": "idle", "done": 0, "total": 2, "error": None, "lastSynced": None}
_task: asyncio.Task | None = None


class RefreshResponse(BaseModel):
    status: str
    done: int
    total: int
    error: str | None = None
    lastSynced: str | None = None


def cached_data() -> dict | None:
    if not CACHE_FILE.exists():
        return None
    try:
        data = json.loads(CACHE_FILE.read_text(encoding="utf-8"))
        if (isinstance(data, dict) and data.get("schemaVersion") == 2
                and data.get("sourceParams") == SOURCES
                and isinstance(data.get("actual"), list) and isinstance(data.get("forecast"), list)):
            return data
    except (ValueError, OSError, AttributeError):
        pass
    return None


async def refresh_data():
    state.update(status="loading", done=0, total=2, error=None)
    try:
        def progress(done, total):
            state.update(done=done, total=total)
        actual, forecast = await load_january(progress)
        actual_norm = [r for r in normalize(actual, actual=True)
                       if "2026-01-02" <= r["startTime"][:10] <= "2026-02-01"]
        forecast_norm = [r for r in normalize(forecast, actual=False)
                         if "2026-01-01" <= r["publishTime"][:10] <= "2026-01-31"]
        if not actual_norm or not forecast_norm:
            raise ValueError("Elexon supplied no valid WIND data in the requested January windows")
        quality = analyze(actual_norm, forecast_norm)
        if not quality["metrics"]["actualPeriods"] or not quality["metrics"]["forecastPeriods"]:
            raise ValueError("Elexon imported records, but no January decision-day forecast/outturn time points were usable")
        # Only a fully validated new-format cache can replace the prior snapshot.
        payload = {"schemaVersion": 2, "actual": actual_norm, "forecast": forecast_norm,
                   "fetchedAt": datetime.now(timezone.utc).isoformat(), "sourceParams": SOURCES}
        CACHE_FILE.parent.mkdir(parents=True, exist_ok=True)
        temporary = CACHE_FILE.with_suffix(".tmp")
        temporary.write_text(json.dumps(payload, separators=(",", ":")), encoding="utf-8")
        temporary.replace(CACHE_FILE)
        state.update(status="ready", lastSynced=payload["fetchedAt"])
    except Exception as exc:
        state.update(status="failed", error=str(exc)[:300])


@app.get("/api/health")
async def health():
    return {"ok": True, "cached": cached_data() is not None}


@app.get("/api/status", response_model=RefreshResponse)
async def get_status():
    cached = cached_data()
    return {**state, "lastSynced": cached.get("fetchedAt") if cached else None,
            "status": "ready" if cached and state["status"] == "idle" else state["status"]}


@app.post("/api/refresh", response_model=RefreshResponse, status_code=202)
async def start_refresh():
    global _task
    if _IS_VERCEL:
        # Vercel serverless: asyncio.create_task() tasks are silently dropped
        # when the function returns its response.  Await directly so the /tmp
        # cache is fully written before we return.
        if state.get("status") != "loading":
            state.update(status="loading", done=0, total=2, error=None)
            await refresh_data()
    else:
        # Docker / local dev: genuine background task so the endpoint returns
        # immediately and the client polls /api/status for progress.
        if not _task or _task.done():
            state.update(status="loading", done=0, total=2, error=None)
            _task = asyncio.create_task(refresh_data())
    cached = cached_data()
    return {**state, "lastSynced": cached.get("fetchedAt") if cached else None}


@app.get("/api/dashboard")
async def dashboard(cutoff: str = Query(default="09:50", pattern=r"^(?:[01]\d|2[0-3]):[0-5]\d$")):
    data = cached_data()
    if data is None and _IS_VERCEL:
        # On Vercel each Lambda instance has its own /tmp.  A prior POST
        # /api/refresh may have populated it in a *different* container, so
        # this instance cannot rely on that cache.  Fetch Elexon directly here
        # so this invocation is completely self-contained.
        state.update(status="loading", done=0, total=2, error=None)
        await refresh_data()
        data = cached_data()
    if data is None:
        raise HTTPException(503, detail="Elexon January 2026 source data not imported yet. Please sync Elexon.")
    result = analyze(data["actual"], data["forecast"], cutoff=cutoff)
    return {**result, "source": "elexon", "lastSynced": data.get("fetchedAt"),
            "rawRecords": {"actual": len(data["actual"]), "forecast": len(data["forecast"])},
            "sourceParams": data.get("sourceParams", SOURCES),
            "methodology": "Select latest WINDFOR per exact delivery startTime where publishTime <= decision cutoff, compare with FUELHH WIND actuals. No imputation."}



@app.get("/api/backtest")
async def backtest(cutoff: str = Query(default="09:50", pattern=r"^(?:[01]\d|2[0-3]):[0-5]\d$")):
    data = cached_data()
    if data is None and _IS_VERCEL:
        state.update(status="loading", done=0, total=2, error=None)
        await refresh_data()
        data = cached_data()
    if data is None:
        raise HTTPException(503, detail="Elexon data not imported yet. Please sync Elexon.")
    result = walk_forward_backtest(data["actual"], data["forecast"], cutoff=cutoff)
    return {**result, "source": "elexon", "lastSynced": data.get("fetchedAt")}


@app.get("/api/risk-brief")
async def risk_brief(
    cutoff: str = Query(default="09:50", pattern=r"^(?:[01]\d|2[0-3]):[0-5]\d$"),
    date: str = Query(default="2026-01-31"),
):
    data = cached_data()
    if data is None and _IS_VERCEL:
        state.update(status="loading", done=0, total=2, error=None)
        await refresh_data()
        data = cached_data()
    if data is None:
        raise HTTPException(503, detail="Elexon data not imported yet. Please sync Elexon.")
    result = decision_risk_brief(data["actual"], data["forecast"], cutoff=cutoff, target_date=date)
    return {**result, "source": "elexon", "lastSynced": data.get("fetchedAt")}


DIST = Path(os.getenv("FRONTEND_DIST", "/app/frontend/dist"))
if DIST.exists():
    app.mount("/", StaticFiles(directory=str(DIST), html=True), name="frontend")
