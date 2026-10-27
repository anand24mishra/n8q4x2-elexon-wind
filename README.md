<img width="1464" height="834" alt="image" src="https://github.com/user-attachments/assets/262f72cb-d401-4144-b987-3c500dbc84f4" /># Windward · Day-ahead forecast decision workbench

A January 2026 pilot for one question: **what GB wind forecast was visible before a day-ahead purchase, how much history supported trusting it, and would two predefined bias corrections have changed the estimate?**

Live app: https://n8q4x2-elexon-wind.vercel.app/

Repository: https://github.com/anand24mishra/n8q4x2-elexon-wind

Stack: React 19, Vite, FastAPI, HTTPX, Docker Compose, Vercel. Data: Elexon BMRS WINDFOR and FUELHH only.

## 1. Business problem

A UK day-ahead buyer commits on the morning before delivery, often around the 09:50 UK N2EX example cutoff. The wind assumption at that moment is whatever WINDFOR revision had already been published. This app reconstructs that information set for 1–31 January 2026 (delivery 2 January–1 February) and scores it against measured wind. It does not place a trade or recommend a purchase volume.

## 2. Why publication time matters

A forecast is eligible only when `publishTime <= cutoff` in Europe/London. An actual may enter the bias calculation only when **that outturn’s publication timestamp** is at or before the same cutoff. An earlier decision date is not enough: a late FUELHH revision must not leak into a morning decision. One second after the cutoff is excluded. The latest eligible revision is the one that is used.

## 3. Architecture

```
Browser (React 19, Vite, no Redux)
  GET /api/dashboard | /api/backtest | /api/risk-brief | /api/scenarios
  POST /api/refresh
        │
        ▼
FastAPI
  elexon.py    HTTPX, retries, no mock fallback
  domain.py    as-of selection, metrics, walk-forward, scenarios
  main.py      routes, atomic JSON snapshot, request logs
```

| Target | Frontend | API | Snapshot |
| --- | --- | --- | --- |
| Docker Compose | Nginx | uvicorn | named volume |
| Vercel | static Vite build | Python serverless (`backend/api/index.py`) | `/tmp` on that instance only |
| Heroku | FastAPI static files | one dyno | ephemeral disk |

Shared analytical state in the UI is the decision date, the UK cutoff, and the scenario strategy. Changing the date or cutoff refetches with `AbortController`, so an older response cannot overwrite a newer selection.

## 4. Data sources

Only these two requests. Records are real Elexon rows. Missing values stay missing.

- WINDFOR: https://data.elexon.co.uk/bmrs/api/v1/datasets/WINDFOR/stream?publishDateTimeFrom=2026-01-01T00%3A00%3A00Z&publishDateTimeTo=2026-02-01T00%3A00%3A00Z
- FUELHH: https://data.elexon.co.uk/bmrs/api/v1/datasets/FUELHH/stream?settlementDateFrom=2026-01-02&settlementDateTo=2026-02-02 then `fuelType=WIND`

WINDFOR is filtered by when the forecast was **published**. FUELHH is filtered by **settlement date**, because that is how measured generation is indexed.

A direct fetch on 27 October 2026 returned 18,104 WINDFOR records, 30,720 FUELHH records, and 1,536 FUELHH rows with `fuelType=WIND` before the delivery window was restricted.

## 5. Existing views

The sidebar still has Overview, Forecast explorer, Accuracy review, Bias backtest, Risk brief, and Methodology.

- Overview and accuracy: MAE, RMSE, signed bias, P90 absolute error, matched coverage. Signed error is forecast minus actual.
- Forecast explorer: half-hour table. Hourly WINDFOR markers are connected across the unobserved half-hour. No synthetic row is inserted. Hover shows delivery time, forecast, actual, publication time, MW, and missing-data status.
- Bias backtest: the original expanding-mean walk-forward.
- Risk brief: forecast position and prior tendency at the cutoff. Same decision date as the other views.
- CSV export of the matched period table remains on the page header.

Cutoff choices are the existing UK times: 08:30, 09:20, 09:50, 10:30, 12:30, 15:30.

## 6. Scenario workbench

Sidebar: **Scenario workbench**. One route does the calculation:

`GET /api/scenarios?date=2026-01-15&cutoff=09:50`

Optional `strategy` is `original`, `expanding`, `rolling7Day`, or `all`. Any other value is HTTP 400. An unknown query parameter is HTTP 400. A date outside 1–31 January 2026 is HTTP 400. No imported snapshot is HTTP 503.

The page shows, from that response:

- the raw forecast, the expanding-history adjustment, and the rolling-history adjustment
- the signed bias used, the eligible training count, and the 72-pair threshold
- latest forecast publication time, freshness at the cutoff, and forecast coverage
- a chart of the three forecasts against retrospective actuals
- historical scores on a shared timestamp set
- a short retrospective reading of which method had the lower error
- an explicit insufficient-history state
- **Export JSON report** and **Markdown**

**Known at decision time** (badge on the page): original forecast, publication time, training count, historical signed bias, adjusted estimate, prior error stats, freshness.

**Learned after delivery**: actual generation, realized error, MAE / RMSE / bias / P90, and whether the adjustment helped that day. Those figures are labeled retrospective.

## 7. Strategy definitions

For decision date D:

| Strategy | Rule |
| --- | --- |
| Original | Latest WINDFOR value with `publishTime <= cutoff`. |
| Expanding mean correction | Subtract the mean signed error of every causally eligible prior pair. |
| Rolling 7-day mean correction | Same subtraction, using only the previous seven decision dates. |

`adjusted = max(0, raw forecast − bias)`. Both corrections require at least 72 causally eligible pairs. Below that the status is `insufficient_history`, the sample count is shown, the original forecast remains, and no adjustment is invented. The window and the threshold are fixed exploratory baselines.

## 8. Walk-forward evaluation

Each decision day is scored only with information available at that day’s cutoff. Successes and failures are both reported. There is no confidence interval and no money metric.

At cutoff 09:50, recomputed from the live extract on 27 October 2026:

| Population | What it is | Result |
| --- | --- | --- |
| Dashboard | All matched half-hours, 31 decision days | 1,488 expected half-hours, 744 matches, MAE 1,995.57 MW, signed bias +1,727.98 MW, 50% matched coverage |
| Expanding-eligible walk-forward | Days with at least 72 prior causal pairs | 27 days, 648 matches, original MAE 2,026.80 MW, adjusted MAE 1,458.04 MW |
| Rolling 7-day, same style of gate | Previous seven decision dates, same 72-pair gate | 27 days, 648 matches, adjusted MAE 1,559.50 MW |
| Three-way common set | Days where **both** corrections clear 72 pairs | On this extract, the same 27 days and 648 matches. Original MAE 2,026.80 MW, expanding 1,458.04 MW, rolling 1,559.50 MW. 19 days improved and 8 worsened under each correction |

The 744-row dashboard set is not the comparison set. On 15 January 2026 the expanding sample was 322 pairs (bias about +1,588 MW) and the rolling sample was 154 pairs (bias about +1,744 MW). On that delivery day the raw forecast had the lower realized MAE (about 897 MW versus about 1,366 and 1,469). A correction that helps the month can hurt a single day. 1 January has zero training pairs and both corrections stay unavailable.

These are retrospective January observations. They are not a guaranteed future error reduction.

## 9. Avoiding leakage

- Forecast eligibility: `publishTime <= cutoff`, including exactly at the cutoff.
- Training actuals: latest FUELHH revision whose publication time is `<= cutoff`.
- Evaluation actuals: latest revision, used only in the retrospective block.
- Adjusted values are not compared on a different timestamp set from the raw forecast.
- Tests cover the cutoff boundary, a late actual, the 71/72 threshold, and a correction that increases error.

## 10. Forecast and actual granularity

WINDFOR `startTime` in this extract is hourly. FUELHH wind is half-hourly settlement, in MW. The baseline join is exact `startTime` equality, which is why matched coverage is about 50 percent. Adjacent half-hours are not averaged. An hour-average actual was not implemented: the fields used here do not establish that the hourly WINDFOR value is that average, and averaging would create a generation measure the source did not publish. The report field `hourlyAlignmentSensitivity` is `not_implemented` for that reason.

## 11. Reproduce a scenario

```bash
# from the repo root, with the API already serving a real snapshot
curl "http://localhost:8000/api/scenarios?date=2026-01-15&cutoff=09:50"
```

Or open Scenario workbench, choose 15 Jan 2026 and 09:50, and read the same fields. The response `provenance.contentHash` is a SHA-256 of the normalised source rows. The same normalised snapshot produces the same hash. A changed generation or publication time changes the hash. `identity.generatedAt` is the analysis time and is not part of the hash.

## 12. API

| Method | Path | Notes |
| --- | --- | --- |
| GET | `/api/health` | Liveness and whether a snapshot exists |
| GET | `/api/status` | Import progress |
| POST | `/api/refresh` | Fetch Elexon. Background in Docker. Awaited on Vercel |
| GET | `/api/dashboard?cutoff=09:50` | 31-day as-of analysis |
| GET | `/api/backtest?cutoff=09:50` | Expanding-mean walk-forward |
| GET | `/api/risk-brief?cutoff=09:50&date=2026-01-31` | Position and prior tendency at the cutoff |
| GET | `/api/scenarios?date=2026-01-15&cutoff=09:50` | Scenario workbench |

Interactive docs when the API process is running: `http://localhost:8000/docs`.

Scenario JSON includes `identity`, `exAnte`, `periods`, `retrospective`, `dailyHistory`, `monthSummary`, `evaluationPopulations`, `missingData`, `provenance`, `reproducibility`, `report`, and `limitations`. Logs include a request id, route, status, duration, Elexon fetch duration, cache hit or miss, and scenario calculation duration. They do not include payloads or secrets.

## 13. Report export

Scenario workbench downloads:

- `windward-decision-scenario-<date>-<cutoff>.json`
- the same stem as Markdown

The document carries the project name, decision date, delivery date, UK cutoff, creation time, both source URLs and parameters, retrieval time, content hash, strategy name, the 72-pair rule, the non-negative adjustment, training count, bias, raw and adjusted values, retrospective metrics, missing-data counts, improved and worsened counts, algorithm version `scenario-workbench-1`, and the limitations below. Retrospective outcomes are under **LEARNED AFTER DELIVERY**.

## 14. Tests and CI

Local commands that were run on 27 October 2026:

```bash
PYTHONPATH=backend backend/.venv/bin/python -m pytest backend/tests -q
# 71 passed

cd frontend && npm run build
# vite build completed, no build error
```

From a clean environment:

```bash
pip install -r backend/requirements.txt -r backend/requirements-dev.txt
PYTHONPATH=backend python -m pytest backend/tests -q

cd frontend && npm ci && npm run build
```

`.github/workflows/ci.yml` runs those two jobs on push and pull request (Python 3.12 and Node 22, with dependency caching). This README does not claim a green GitHub Actions run. Check the Actions tab after the workflow finishes.

Tests cover cutoff boundaries, late actuals, 0 / 71 / 72 training pairs, both bias signs, the non-negative floor, equal evaluation sets, a correction that worsens accuracy, invalid API input, the report hash, and the absence of secret fields. They do not call the network.

## 15. Docker

```bash
docker compose up --build
```

Open http://localhost:8080. The first import pulls the two Elexon windows. `docker compose down` keeps the cache volume. `docker compose down -v` deletes it.

A `docker compose up --build` was started in this session and did not finish, so container health was **not** reconfirmed here. The compose file is unchanged: API on the internal network, Nginx publishing port 8080.

## 16. Vercel

`vercel.json` routes `/api/*` to the FastAPI service and everything else to the Vite service. The public site is https://n8q4x2-elexon-wind.vercel.app/

On Vercel, `VERCEL` selects `/tmp` for the snapshot and makes `/api/refresh` synchronous, because a background task would be dropped when the function returns. Instances do not share `/tmp`. A cold start fetches Elexon on the analytical request. Pushing `main` deploys only if the GitHub project is already connected. This document does not claim a new deployment was observed.

## 17. Engineering tradeoffs

- The snapshot is a local JSON file, not a database. That matches a single-month public extract and avoids inventing shared infrastructure that Vercel instances would not share anyway.
- Scenario evaluation is recomputed per request. It is deterministic for a given snapshot hash. A cross-instance cache would need a store this pilot does not have.
- The UI keeps one date and one cutoff for Overview, explorer, workbench, and the risk brief, so those views cannot silently disagree.
- Hourly points are connected on the chart only as a visual. The metrics still ignore unmatched half-hours.

## 18. Known limitations

- One calendar month.
- Hourly forecasts versus half-hourly settlement. Exact matches only.
- Exploratory baselines. The January error reduction on the 648-row set is not a validated forward strategy.
- No demand, price, or portfolio series. No trading order, procurement quantity, or financial saving.
- Realized error is known only after delivery.
- No confidence interval.
- National GB wind only, not a single site.

## Screenshots

These images are the January dashboard from an earlier commit. They do not show the scenario workbench.
<img width="1464" height="834" alt="image" src="https://github.com/user-attachments/assets/81c4500c-7e0d-4ed6-b4b3-66cee547dcd7" />
<img width="1464" height="834" alt="image" src="https://github.com/user-attachments/assets/67e78370-b80c-4a28-9d2e-d28d0d0b74cd" />
<img width="1464" height="834" alt="image" src="https://github.com/user-attachments/assets/3785d47a-33db-444b-9a39-8a9e3985948a" />
<img width="1464" height="834" alt="image" src="https://github.com/user-attachments/assets/dd9790a7-ea4f-46a4-bf1a-a3bd7ccc431f" />
<img width="1464" height="834" alt="image" src="https://github.com/user-attachments/assets/1cf0e121-2d33-415c-a4a3-d9f8df93fc28" />
<img width="1464" height="834" alt="image" src="https://github.com/user-attachments/assets/996d7eea-2763-45c8-9bec-8a13caa1dd10" />





