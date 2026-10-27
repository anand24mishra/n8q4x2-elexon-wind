# Windward · GB Wind Forecast Intelligence

A decision-support tool for UK electricity procurement teams. Windward answers a precise question: **given the wind generation forecast visible at a specific market cutoff, how accurately did it predict actual generation?**

The platform pulls live data from the Elexon BMRS public API, reconstructs point-in-time forecast snapshots for each half-hour of January 2026, and delivers interactive accuracy analytics without relying on pre-computed or simulated values.

**Live app:** _deploy to Vercel or Heroku and add the URL here_  
**Stack:** React 19 · Vite 7 · FastAPI · Python 3.12 · HTTPX · Nginx · Docker Compose  
**Data:** Elexon BMRS — WINDFOR (forecast) and FUELHH (actuals), January 2026

---

## Why it exists

UK day-ahead electricity buyers commit to wholesale positions the morning before delivery — typically by the N2EX gate closure at 09:50 UK time. Their wind generation assumption is based on whatever forecast existed at that moment. If that forecast proves wrong, they face unplanned balancing costs.

Windward makes that exposure visible and quantifiable:

- Shows exactly which WINDFOR revision was available at any configurable cutoff
- Computes MAE, RMSE, signed bias, P90 absolute error, and overforecast rate against FUELHH actuals
- Breaks results down to individual settlement periods so traders can trace every number back to source
- Lets analysts compare how accuracy would differ at an earlier or later cutoff

---

## Screenshot

> _Record and embed a screenshot here after first deployment_

---

## Architecture

```
Browser  (React 19 SPA)
   │
   │   GET /api/status  ·  GET /api/dashboard?cutoff=HH:MM  ·  POST /api/refresh
   ▼
Nginx  :80 → FastAPI (uvicorn)  :8000
               │
          elexon.py          ──  async HTTPX · exponential back-off · no fallback
          │   WINDFOR    publishDateTimeFrom / publishDateTimeTo
          │   FUELHH     settlementDateFrom / settlementDateTo  → fuelType=WIND
               │
          domain.py          ──  ZoneInfo(Europe/London) cutoffs · as-of revision
                                 selection · MAE / RMSE / bias / P90 / coverage
               │
          main.py            ──  FastAPI routes · atomic JSON cache (.tmp rename)
                                 · Vercel /tmp adapter · background task (Docker)
```

**Deployment targets:**

| Target | Frontend | Backend | Cache |
|--------|----------|---------|-------|
| Docker Compose | Nginx container | FastAPI container | Named volume (`windward_data`) |
| Vercel | Static CDN (`frontend/dist`) | Python serverless function (`api/index.py`) | `/tmp` (per warm instance) |
| Heroku | FastAPI static file mount | Single dyno | Ephemeral dyno filesystem |

---

## Repository layout

```
n8q4x2/
├── vercel.json                Vercel single-project config (frontend + serverless API)
├── docker-compose.yml         Nginx + FastAPI + named cache volume
├── Dockerfile                 Single-image build for Heroku
├── heroku.yml                 Heroku container process file
├── .gitignore
├── .dockerignore
│
├── api/
│   ├── index.py               Vercel ASGI entry point (re-exports FastAPI app)
│   └── requirements.txt       Python deps for the Vercel serverless function
│
├── backend/
│   ├── Dockerfile
│   ├── requirements.txt       Runtime: fastapi, uvicorn, httpx
│   ├── requirements-dev.txt   Dev: pytest, anyio, httpx
│   ├── api/
│   │   └── index.py           Vercel entry point (re-exports FastAPI app)
│   └── app/
│       ├── main.py            API routes, atomic cache, Vercel/Docker refresh strategy
│       ├── elexon.py          Elexon BMRS HTTP adapter with retry and validation
│       └── domain.py          Pure analysis: cutoffs, as-of selection, metrics
│   └── tests/
│       ├── conftest.py
│       ├── test_api.py        API endpoints, cache preservation, Vercel env detection
│       ├── test_elexon.py     Exact request parameters, WIND filter, upstream errors
│       ├── test_domain.py     Cutoff leakage, revision ordering, timezone, metrics
│       └── test_regression.py Coverage gaps, boundary conditions, empty inputs
│
└── frontend/
    ├── Dockerfile
    ├── nginx/default.conf     Reverse proxy + 185 s timeout + SPA fallback
    ├── package.json
    ├── package-lock.json
    ├── vite.config.js
    ├── index.html
    └── src/
        ├── main.jsx
        ├── App.jsx            Dashboard, explorer, accuracy, methodology, CSV export
        └── style.css
```

---

## Running locally

### Docker Compose (recommended)

```bash
docker compose up --build
```

Open **http://localhost:8080**. Click **Sync Elexon** or wait for the automatic import trigger. The import fetches ~18 k WINDFOR records and ~1.5 k FUELHH WIND records from the live Elexon API (typically 10–20 s). The dashboard becomes interactive once the import completes.

```bash
docker compose down          # stop containers (cache volume is preserved)
docker compose down -v       # stop and delete cache volume
```

### Local dev (no Docker)

**Backend** — Python 3.12+:
```bash
cd backend
python -m venv .venv && source .venv/bin/activate
pip install -r requirements.txt
CACHE_FILE=./data/elexon-january-2026.json uvicorn app.main:app --reload --port 8000
```

**Frontend** — Node 22+:
```bash
cd frontend
npm install
npm run dev         # Vite dev server at http://localhost:5173
```

The Vite config proxies `/api` to `localhost:8000`.

---

## API reference

| Method | Route | Description |
|--------|-------|-------------|
| `GET` | `/api/health` | Liveness check + cache presence |
| `GET` | `/api/status` | Import progress, last-synced timestamp, error string |
| `POST` | `/api/refresh` | Trigger Elexon import (background on Docker, blocking on Vercel) |
| `GET` | `/api/dashboard?cutoff=09:50` | Full January analysis; `cutoff` accepts any `HH:MM` in `[00:00, 23:59]` |

Interactive docs: `http://localhost:8000/docs`

---

## Tests

```bash
cd backend
pip install -r requirements-dev.txt
PYTHONPATH=. python -m pytest -v
```

**31 tests, 4 modules** — all deterministic, no network calls, no test data files:

| Module | What it covers |
|--------|---------------|
| `test_api.py` | Health endpoint, 503 when no cache, invalid cutoff rejection, no simulation mode, cache survives failed refresh, Vercel `/tmp` path |
| `test_elexon.py` | Exact SOURCES parameters sent in HTTP requests, empty/malformed response → `UpstreamError`, WIND-only filter |
| `test_domain.py` | Cutoff prevents look-ahead, revision ordering, BST/GMT timezone, prior-known MAE excludes future actuals, null not zero for unmatched periods |
| `test_regression.py` | Hourly WINDFOR → 50% coverage, cutoff boundary (≤ inclusive), multiple revisions, empty inputs, negative generation rejection, late FUELHH publishTime |

---

## Deployment

### Vercel (Services — single project)

`vercel.json` uses Vercel's Services format: each directory is a standalone deployable unit, routed by top-level `rewrites`.

| Route | Service | Notes |
|-------|---------|-------|
| `/api/*` | `backend` — FastAPI serverless | `maxDuration 60 s`, `memory 1024 MB` |
| `/*` | `frontend` — Vite static CDN | SPA fallback to `index.html` |

**No service bindings** — the React frontend calls `/api/*` from the browser (public rewrites handle routing); the FastAPI backend only calls Elexon externally. No server-to-server traffic between the two services.

Vercel's `framework: "fastapi"` discovers the ASGI app at `backend/api/index.py` (within the service root). That shim adds `backend/` to `sys.path` and re-exports the FastAPI `app` object from `backend/app/main.py`.

Two runtime adaptations in `backend/app/main.py` (guarded by `os.getenv("VERCEL")`, injected automatically):

1. **Cache path** — writes to `/tmp` (only writable Lambda path) instead of `/app/data/`
2. **Synchronous refresh** — awaits `refresh_data()` directly instead of `asyncio.create_task()`, which Vercel drops when the function returns its response

> `/tmp` is shared only across warm Lambda instances. Cold starts trigger a fresh Elexon import on the first request.

```bash
npm install -g vercel
vercel login
vercel --prod          # run from repo root; follow prompts to link project
```

Or import via the Vercel dashboard — choose **"Import multi-service project"**, no environment variables required.

### Heroku

```bash
heroku container:push web -a <app-name>
heroku container:release web -a <app-name>
```

Uses the root `Dockerfile` which runs `vite build` and serves the output as FastAPI static files. Heroku dyno storage is ephemeral — the Elexon cache is lost on each restart. For production, move the cache to an external store (S3, Postgres).

---

## Data sources

Both datasets are from the [Elexon BMRS public API](https://bmrs.elexon.co.uk/api-documentation). No API key is required.

| Dataset | Parameters | Records (Jan 2026) |
|---------|-----------|-------------------|
| [WINDFOR](https://bmrs.elexon.co.uk/api-documentation/endpoint/datasets/WINDFOR/stream) | `publishDateTimeFrom=2026-01-01T00:00:00Z` `publishDateTimeTo=2026-02-01T00:00:00Z` | 18,104 |
| [FUELHH](https://bmrs.elexon.co.uk/api-documentation/endpoint/datasets/FUELHH/stream) | `settlementDateFrom=2026-01-02` `settlementDateTo=2026-02-02` → `fuelType=WIND` | 1,536 |

**Why different parameter types?** WINDFOR is filtered by when forecasts were *published* (relevant for reconstructing the buyer's information set at cutoff). FUELHH is filtered by *settlement date* (the delivery day), as that is how Elexon indexes measured generation.

**50% forecast coverage:** WINDFOR publishes one forecast per hour, not per half-hour. For each 48-slot delivery day, 24 slots have a matched WINDFOR record; the other 24 remain `null`. This is a dataset characteristic — Windward leaves unmatched slots as `null` rather than interpolating.

---

## Methodology

| Concept | Implementation |
|---------|---------------|
| Decision cutoff | `ZoneInfo("Europe/London")` local time → UTC; BST-aware for future months |
| As-of forecast selection | Latest `WINDFOR` revision with `publishTime ≤ cutoff` per delivery `startTime` |
| Actual generation | Latest `FUELHH` WIND revision per `startTime` (retrospective evaluation) |
| Prior-known MAE | Trailing MAE using only FUELHH revisions published `≤ cutoff` at time of decision |
| Error sign | `forecast − actual`; positive = overforecast = more wind assumed than delivered |
| Metrics | MAE, RMSE, bias, P90 absolute error, overforecast rate — matched pairs only |
| Match | Exact UTC `startTime` equality; no interpolation or zero-fill |

---

*AI tools used during development: Google Gemini (Antigravity IDE) and OpenAI ChatGPT — for domain research, code generation assistance, test design, and documentation.*
