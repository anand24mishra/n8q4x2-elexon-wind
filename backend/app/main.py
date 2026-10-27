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
import logging
import os
import time
import uuid
from datetime import datetime, timezone
from pathlib import Path

from fastapi import FastAPI, HTTPException, Query, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.staticfiles import StaticFiles

from .domain import (
    analyze,
    build_analysis_report,
    decision_risk_brief,
    decision_scenario_workbench,
    normalize,
    walk_forward_backtest,
)

SUPPORTED_STRATEGIES = {"all", "original", "expanding", "rolling7Day"}
from .elexon import SOURCES, load_january
from .schemas import RefreshResponse, ScenarioWorkbenchResponse

logger = logging.getLogger("windward")
if not logger.handlers:
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
    )

app = FastAPI(
    title="Windward | Elexon Wind Intelligence",
    version="2.1.0",
    description="January 2026 decision-time GB wind forecast evaluation & scenario workbench",
)
app.add_middleware(
    CORSMiddleware,
    allow_origins=[v.strip() for v in os.getenv("CORS_ORIGINS", "http://localhost:5173,http://localhost:8080").split(",")],
    allow_methods=["GET", "POST"],
    allow_headers=["*"],
)

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


@app.middleware("http")
async def request_logging_middleware(request: Request, call_next):
    req_id = request.headers.get("x-request-id") or uuid.uuid4().hex[:8]
    start_time = time.perf_counter()
    response = await call_next(request)
    duration_ms = round((time.perf_counter() - start_time) * 1000, 2)
    response.headers["x-request-id"] = req_id
    if request.url.path.startswith("/api"):
        logger.info(
            f"request_id={req_id} method={request.method} path={request.url.path} "
            f"status={response.status_code} duration_ms={duration_ms}"
        )
    return response


def logged_cache(route: str) -> dict | None:
    """Read the local snapshot and record hit/miss without logging the payload."""
    data = cached_data()
    logger.info(f"cache_{'hit' if data is not None else 'miss'} route={route}")
    return data


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
        t_start = time.perf_counter()
        actual, forecast = await load_january(progress)
        fetch_duration = round(time.perf_counter() - t_start, 2)
        logger.info(
            f"elexon_fetch_success duration_sec={fetch_duration} "
            f"actual_count={len(actual)} forecast_count={len(forecast)}"
        )
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
        logger.error(f"elexon_fetch_failure error={str(exc)}")
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
    data = logged_cache("/api/dashboard")
    if data is None and _IS_VERCEL:
        # On Vercel each Lambda instance has its own /tmp.  A prior POST
        # /api/refresh may have populated it in a *different* container, so
        # this instance cannot rely on that cache.  Fetch Elexon directly here
        # so this invocation is completely self-contained.
        state.update(status="loading", done=0, total=2, error=None)
        await refresh_data()
        data = logged_cache("/api/dashboard")
    if data is None:
        raise HTTPException(503, detail="Elexon January 2026 source data not imported yet. Please sync Elexon.")
    result = analyze(data["actual"], data["forecast"], cutoff=cutoff)
    return {**result, "source": "elexon", "lastSynced": data.get("fetchedAt"),
            "rawRecords": {"actual": len(data["actual"]), "forecast": len(data["forecast"])},
            "sourceParams": data.get("sourceParams", SOURCES),
            "methodology": "Select latest WINDFOR per exact delivery startTime where publishTime <= decision cutoff, compare with FUELHH WIND actuals. No imputation."}



@app.get("/api/backtest")
async def backtest(cutoff: str = Query(default="09:50", pattern=r"^(?:[01]\d|2[0-3]):[0-5]\d$")):
    data = logged_cache("/api/backtest")
    if data is None and _IS_VERCEL:
        state.update(status="loading", done=0, total=2, error=None)
        await refresh_data()
        data = logged_cache("/api/backtest")
    if data is None:
        raise HTTPException(503, detail="Elexon data not imported yet. Please sync Elexon.")
    result = walk_forward_backtest(data["actual"], data["forecast"], cutoff=cutoff)
    return {**result, "source": "elexon", "lastSynced": data.get("fetchedAt")}


@app.get("/api/risk-brief")
async def risk_brief(
    cutoff: str = Query(default="09:50", pattern=r"^(?:[01]\d|2[0-3]):[0-5]\d$"),
    date: str = Query(default="2026-01-31"),
):
    data = logged_cache("/api/risk-brief")
    if data is None and _IS_VERCEL:
        state.update(status="loading", done=0, total=2, error=None)
        await refresh_data()
        data = logged_cache("/api/risk-brief")
    if data is None:
        raise HTTPException(503, detail="Elexon data not imported yet. Please sync Elexon.")
    result = decision_risk_brief(data["actual"], data["forecast"], cutoff=cutoff, target_date=date)
    return {**result, "source": "elexon", "lastSynced": data.get("fetchedAt")}


@app.get("/api/scenarios", response_model=ScenarioWorkbenchResponse)
async def scenarios(
    request: Request,
    date: str = Query(default="2026-01-15", pattern=r"^\d{4}-\d{2}-\d{2}$"),
    cutoff: str = Query(default="09:50", pattern=r"^(?:[01]\d|2[0-3]):[0-5]\d$"),
    strategy: str | None = Query(default=None),
):
    unknown = sorted(set(request.query_params) - {"date", "cutoff", "strategy"})
    if unknown:
        raise HTTPException(status_code=400, detail=f"Unsupported query parameter '{unknown[0]}'.")
    if not ("2026-01-01" <= date <= "2026-01-31"):
        raise HTTPException(
            status_code=400,
            detail=f"Decision date '{date}' is out of range. Must be between 2026-01-01 and 2026-01-31.",
        )
    if strategy is not None and strategy not in SUPPORTED_STRATEGIES:
        raise HTTPException(
            status_code=400,
            detail=(
                f"Unsupported strategy '{strategy}'. "
                "Supported values: all, original, expanding, rolling7Day."
            ),
        )
    data = logged_cache("/api/scenarios")
    if data is None and _IS_VERCEL:
        state.update(status="loading", done=0, total=2, error=None)
        await refresh_data()
        data = logged_cache("/api/scenarios")
    if data is None:
        raise HTTPException(503, detail="Elexon data not imported yet. Please sync Elexon.")
    t0 = time.perf_counter()
    try:
        result = decision_scenario_workbench(
            data["actual"],
            data["forecast"],
            cutoff=cutoff,
            target_date=date,
            retrieved_at=data.get("fetchedAt"),
        )
        if strategy is not None:
            result["requestedStrategy"] = strategy
            result["report"] = build_analysis_report(result, selected_strategy=strategy)
    except ValueError as exc:
        logger.info(f"scenario_rejected date={date} cutoff={cutoff} error={exc}")
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    except Exception:
        logger.exception("scenario_calc_failure")
        raise HTTPException(
            status_code=500,
            detail="Scenario calculation failed. No adjusted forecast was fabricated.",
        )
    duration_ms = round((time.perf_counter() - t0) * 1000, 2)
    logger.info(
        f"scenario_calc_completed duration_ms={duration_ms} date={date} cutoff={cutoff} "
        f"strategy={strategy or 'all'}"
    )
    return result


DIST = Path(os.getenv("FRONTEND_DIST", "/app/frontend/dist"))
if DIST.exists():
    app.mount("/", StaticFiles(directory=str(DIST), html=True), name="frontend")
