# Windward: UK day-ahead wind forecast intelligence

A client-facing January 2026 pilot for a UK electricity utility's **day-ahead procurement team**. The product reconstructs which national wind forecast the buyer could see at a specified purchase cutoff, joins it to later measured wind generation, and quantifies the forecast error. It does **not** use generated example values, external demand series, or invented procurement volumes.

**Technology:** React 19, Vite 7, Python 3.12, FastAPI, HTTPX, Nginx, Docker and Docker Compose. **Period:** purchases dated 1-31 January 2026, corresponding to delivery dates 2 January to 1 February 2026.

> **Verification status (27 October 2026):** All 20 automated tests pass locally. Both Elexon API endpoints were live-verified: WINDFOR returned 18,104 records and FUELHH returned 30,720 records including 1,536 WIND records. Docker Compose build succeeded (both `api` and `web` containers). The running application at `http://localhost:8080` returned real Elexon data: 1,488 expected periods, 744 matched pairs (50% coverage — WINDFOR publishes at hourly resolution, leaving every alternate half-hour without a direct forecast), 31 daily decision-day objects, and quantified accuracy metrics. The React production bundle builds without warnings. Public repository is published at https://github.com/anand24mishra/n8q4x2-elexon-wind. Public deployment and unlisted YouTube video are not yet claimed complete.

## Business questions answered

1. **What did we know at purchase time?** Pick a 09:50 UK time N2EX example cutoff, or compare alternative cutoffs. For every half-hour of tomorrow's delivery day, select only the latest `WINDFOR` forecast published on or before today's cutoff. Never use a later revision to make an old decision appear more accurate.
2. **How accurate was that forecast?** Compare matched `startTime` values against `FUELHH` with `fuelType = WIND`. See forecast-vs-actual charts, MAE, RMSE, signed bias, P90 absolute error, overforecast frequency, and observed/matched coverage.
3. **What error was historically known on that purchase day?** A rolling prior-known MAE uses only earlier delivery periods for which an FUELHH actual *revision* was already published by the selected cutoff. This avoids using end-of-month knowledge for an early-January purchase decision. It is an indicative historical risk signal, not a statistical prediction interval.
4. **Can we audit the numbers?** Every selected time point includes the forecast publication timestamp, FUELHH latest outturn publication timestamp (if supplied), forecast, outturn and signed error; export the full January time series to CSV.

The client has **not** provided consumer demand, forward prices, contracts or internal generation positions. Therefore, this pilot illustrates the wind component of an eventual procurement decision but **cannot** calculate how many MW/MWh to buy from the wholesale market or from thermal plants. Any claim otherwise would violate the scope of the supplied datasets.

## Only two source datasets

The backend calls the **exact query-window parameters supplied in the task**. No API key is required for the public Elexon Insights API.

**Forecast, WINDFOR**, historical publication revisions:

```text
https://data.elexon.co.uk/bmrs/api/v1/datasets/WINDFOR/stream?publishDateTimeFrom=2026-01-01T00%3A00%3A00Z&publishDateTimeTo=2026-02-01T00%3A00%3A00Z
```

**Actual, FUELHH**, by settlement date, followed by `fuelType == WIND` filtering:

```text
https://data.elexon.co.uk/bmrs/api/v1/datasets/FUELHH/stream?settlementDateFrom=2026-01-02&settlementDateTo=2026-02-02
```

**Important:** FUELHH must be queried with `settlementDateFrom` / `settlementDateTo` as above, **not** `publishDateTimeFrom` / `publishDateTimeTo`. The stream adapter accepts Elexon's documented JSON-array form and the optional `data` envelope, rejects malformed/empty responses, retries transport errors/429/5xx, and does not fall back to a simulation. Historical as-of selection only uses WINDFOR revisions in the supplied January publication window. Earlier publications such as 31 December are *outside the supplied query*, so earliest-day decisions can have lower coverage if they relied on older forecasts. This constraint is explicit rather than inventing their values.

Source documentation: [WINDFOR](https://bmrs.elexon.co.uk/api-documentation/endpoint/datasets/WINDFOR/stream), [FUELHH](https://bmrs.elexon.co.uk/api-documentation/endpoint/datasets/FUELHH/stream). N2EX 09:50 closing time reference: [Nord Pool support](https://support.nordpoolgroup.com/support/solutions/articles/8000088463-about-the-n2ex-day-ahead-auction). The cutoff is configurable because other auctions can have different deadlines.

## Architecture

```text
Browser / React 19 (Vite)
    |
    | GET /api/status, /api/dashboard, POST /api/refresh
    v
Nginx on port 8080  ->  FastAPI on port 8000
                              |
                              +-- elexon.py   HTTPX + retries
                              |      | WINDFOR by publish time
                              |      | FUELHH by settlement date, WIND only
                              |
                              +-- main.py     validate + normalize + atomic JSON cache
                              |
                              +-- domain.py   UK cutoff, as-of selection, comparisons,
                                             revision-aware prior MAE, metrics
```

One-container `Dockerfile` uses React's Vite production build served by FastAPI static files for Heroku. Compose uses separate frontend Nginx and FastAPI services, plus a named persistent cache volume. **Heroku dyno storage is ephemeral**. For a production system, move snapshots to S3/Blob/Postgres and introduce a scheduled import or persistent queue. This short-lived pilot loads January on first browser visit and caches in the running instance.

### Folder layout

```text
.
├── README.md                     Problem, design, setup, verification
├── .gitignore                    Excludes secrets, dependency folders, caches
├── .dockerignore                 Docker build exclusions
├── docker-compose.yml            Nginx + FastAPI + named cache volume
├── Dockerfile                    Single-image Heroku build
├── heroku.yml                    Heroku Docker app process
├── backend/
│   ├── Dockerfile                FastAPI Docker image
│   ├── requirements.txt          Runtime requirements
│   ├── requirements-dev.txt      Pytest development requirements
│   ├── app/
│   │   ├── main.py               API, background refresh, validated atomic cache
│   │   ├── elexon.py             Exact Elexon requests, retry and failure handling
│   │   └── domain.py             Pure time selection and quantitative evaluation
│   └── tests/
│       ├── test_api.py           API behaviour, cache preservation
│       ├── test_elexon.py        Exact URL parameters and WIND filtering
│       └── test_domain.py        Cutoffs, revisions, leakage, error measures
└── frontend/
    ├── Dockerfile                Vite + Nginx image
    ├── nginx/default.conf        Reverse proxy for API and SPA fallback
    ├── package.json             React and Vite dependencies
    ├── vite.config.js           Local dev proxy
    ├── index.html               Application shell
    └── src/
        ├── main.jsx             React mount
        ├── App.jsx              Dashboard, date explorer, accuracy, CSV, audit
        └── style.css            Responsive UI design
```

## Run locally

Prerequisites: Docker Desktop with Compose v2.

```bash
docker compose up --build
```

Visit **http://localhost:8080**. On first visit the app requests an Elexon refresh automatically, or click **Sync Elexon**. The app remains in an honest empty/loading state until the real-source import completes. No demo/sample data mode exists. Re-import does not overwrite a previous successful cache until both datasets have validated. On import failure, a previously cached snapshot remains readable and the error is shown.

To stop: `docker compose down`. Named volume `windward_data` persists between ordinary Compose restarts.

### Development without Docker

Python 3.12+ and Node.js 22+ recommended. Terminal 1:

```bash
cd backend
python -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
CACHE_FILE=./data/elexon-january-2026.json uvicorn app.main:app --reload --port 8000
```

Terminal 2:

```bash
cd frontend
npm install
npm run dev
```

Open **http://localhost:5173**. On Windows, activate the Python virtual environment using the equivalent shell command. The Vite dev proxy forwards `/api` to FastAPI.

### API

| Route | Purpose |
|---|---|
| `GET /api/health` | Liveness and cache presence |
| `GET /api/status` | Import status, progress and errors |
| `POST /api/refresh` | Asynchronous real Elexon import, no fake fallback |
| `GET /api/dashboard?cutoff=09:50` | Point-in-time January report with 31 days and 1,488 settlement periods |

The API docs are available locally at `http://localhost:8000/docs` when running the development backend directly.

### Automated verification

```bash
cd backend
pip install -r requirements-dev.txt
PYTHONPATH=. python -m pytest -q
```

20 tests passed against deterministic **test-only fixtures**, including precise source request parameters, rejection of empty source results, 31 January decisions, 1 February delivery coverage, forecast versions published after cutoff, missing observations, actual revisions, and a cache that survives a failed refresh. Test fixtures are not displayed as live customer results.

**Full acceptance test to run with internet and Docker before the client demo:**

1. `docker compose up --build`, then confirm `/api/health` and a successful `POST /api/refresh` and `/api/status` result.
2. Verify source request parameters on Network/server logs or an upstream recording. They must match the two URLs above, with no other datasets.
3. Check `GET /api/dashboard?cutoff=09:50`: `rawRecords.actual > 0`, `rawRecords.forecast > 0`, `metrics.count > 0`, 31 daily objects and 1,488 period objects. Inspect coverage: if low, explain why before claiming forecast quality.
4. Inspect a date's forecast publication timestamps and prove each is no later than its purchase cutoff. Spot-check a few source records and manually recompute `(forecast - actual)`.
5. Switch cutoff from 09:20 to 15:30 and confirm the resulting selected publication timestamps never exceed the selected cutoff.
6. Export CSV and compare its row count (1,488 plus header), selected timestamps and errors with dashboard values.
7. Refresh with Elexon unavailable: the previous snapshot must stay available; the UI must show an error and never substitute simulated numbers.
8. Verify desktop/mobile responsive layout and inspect browser console. Check that `/api/dashboard` never exposes fabricated records after a fresh install.

The first run and a full Docker/React build could not be exercised in this restricted authoring environment. **Do not record or submit until these checks pass in a networked environment.**

## Methodology and caveats

- **Clock and delivery:** decision dates 1-31 January 2026 use London local-time cutoffs; associated delivery day is D+1. All January/February target days here are GMT, with 48 half-hour intervals each, giving **1,488** expected timestamps.
- **Revision-aware as-of forecast:** for a target `startTime`, choose the maximum forecast `publishTime` that is `<=` its decision cutoff. A later forecast is excluded from historic purchase-day forecasts.
- **Actual generation:** `FUELHH` records are restricted to `fuelType=WIND`. For retrospective outturn evaluation, use the latest valid published revision per timestamp. The prior-known benchmark uses instead the latest actual revision published *by that earlier cutoff*.
- **Match:** exact UTC `startTime` equality. Missing rows remain `null`; no forward-fill, interpolation or treating missing as zero. Hourly forecasts can consequently leave unmatched half-hourly periods.
- **Error (MW):** `forecast - actual`. Positive error means wind was **overforecast**, potentially causing an unexpected shortfall relative to the assumed wind contribution.
- **MAE:** mean absolute error in MW. **RMSE:** square root of average squared error in MW. **Bias:** mean signed error in MW. **P90:** nearest-rank 90th percentile of absolute errors in MW. All error metrics are calculated on *matched pairs only*.
- **Net matched energy error:** sum of `0.5 * (forecast MW - actual MW)` over **matched** half-hour periods in MWh. It is not the physical total for any uncovered periods and does **not** equal the client's required wholesale purchase.
- **Coverage:** expected forecast, expected actual, and matched periods are counted separately. A precise estimate on 2% matched coverage is not an adequate month-wide assessment.
- **Hindsight vs. usable uncertainty:** month-end metrics are for retrospective evaluation. The per-decision `priorKnownMaeMw` and its sample size are the only metrics intentionally constrained to data already published at that decision point. Neither P90 nor MAE should be described as a validated prospective prediction interval without calibration and further history.
- **Source limits:** this assignment explicitly excludes demand, prices and thermal generation; no trading optimization, purchase volume or cost reduction is claimed. Earlier-than-January WINDFOR publications are excluded because the requested forecast URL begins on 1 January 2026.

## Deploy and submission

### Heroku (single container)

1. Create a new Heroku app in a Heroku account with container deployment support.
2. From this repository root, authenticate with the Heroku CLI and set the app's stack to `container`.
3. Deploy using `git push heroku main` with the checked-in `heroku.yml` and root `Dockerfile`, or use the Heroku Container Registry (`heroku container:push web -a <app>` and `heroku container:release web -a <app>`).
4. Open the app's URL, allow the real Elexon import to finish, and validate the analysis, selected dates, coverage and CSV export as above.
5. Put the public URL in the submission form and check the app without a logged-in session. **Deploying does not make the real Elexon dataset magically validated.**

**Deployment URL:** Not yet deployed. Replace this line with the verified public URL after deployment.

### Vercel (single project — frontend + serverless backend)

`vercel.json` at the repository root configures one Vercel project that serves both tiers:

| Route | Handler | Notes |
|-------|---------|-------|
| `/api/*` | `api/index.py` (Python serverless function) | `maxDuration 60 s`, `memory 1024 MB` |
| `/*` | `frontend/dist` (static) | Vite build output; SPA fallback to `index.html` |

**Serverless adaptations** (see `backend/app/main.py` module docstring):

1. **Cache path** — Vercel's Lambda filesystem is read-only except `/tmp`. When the `VERCEL` env var is present (injected automatically by Vercel's runtime), the cache is written to `/tmp/elexon-january-2026.json` instead of `/app/data/`. Docker Compose is unaffected (no `VERCEL` var).
2. **Synchronous refresh** — `asyncio.create_task()` tasks are silently dropped when a Vercel function returns its HTTP response. `POST /api/refresh` therefore awaits `refresh_data()` directly on Vercel, blocking until the Elexon import completes before returning 202. The Docker path keeps the original background-task behaviour.

**Limitation:** `/tmp` is ephemeral and shared only across warm invocations of the same Lambda instance. Cold starts (new instance) have no cache and will trigger an automatic re-import on the first page visit.

**Deploy steps:**

```bash
npm install -g vercel          # Install Vercel CLI
vercel login                   # Authenticate
cd n8q4x2                      # Repository root
vercel --prod                  # Deploy; follow prompts to link to project
```

Or connect the GitHub repository in the Vercel dashboard (Settings → Git → Import Project). Set no extra environment variables — `VERCEL` is injected automatically.

After deployment: open the Vercel URL, click **Sync Elexon** (the request blocks until Elexon data is imported, typically 10–30 s), then verify the dashboard shows real data (`source: "elexon"`, `rawRecords.actual: 1488`).


**Public GitHub repo:** https://github.com/anand24mishra/n8q4x2-elexon-wind — published and publicly accessible.

**Unlisted YouTube video:** Not yet recorded/uploaded. Record a walkthrough under 10 minutes; verify the unlisted video URL in a signed-out browser.

**Submission form:** https://forms.cloud.microsoft/r/FTX6K2bbnU. Submit only after the app, repo and video work in incognito mode.

### AI tool disclosure

**AI tools used: Google Gemini / Antigravity IDE (Claude Sonnet) and OpenAI ChatGPT.** Used to assist with domain research, software design, backend and frontend implementation, test authoring, documentation, and review planning. The implementation and deployment claims above distinguish completed checks from unverified live integrations.
