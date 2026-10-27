"""Pure historical as-of selection and forecast error calculations.

Forecast selection is strictly as-of cutoff; retrospective metrics use later actuals.
"""
from __future__ import annotations

import hashlib
from collections import defaultdict
from datetime import date, datetime, time, timedelta, timezone
from math import ceil, isfinite
from statistics import mean
from typing import Any
from zoneinfo import ZoneInfo

LONDON = ZoneInfo("Europe/London")
UTC = timezone.utc
JAN_START = date(2026, 1, 1)
JAN_END = date(2026, 1, 31)

# Minimum number of matched (forecast, actual-published-by-cutoff) pairs that
# must exist from *prior* decision days before a walk-forward bias adjustment
# is shown.  Fewer than this means the training window is too small to be
# reported with any credibility.
MIN_BACKTEST_PAIRS = 72
ROLLING_WINDOW_DECISION_DATES = 7
ALGORITHM_VERSION = "scenario-workbench-1"
WINDFOR_STREAM = "https://data.elexon.co.uk/bmrs/api/v1/datasets/WINDFOR/stream"
FUELHH_STREAM = "https://data.elexon.co.uk/bmrs/api/v1/datasets/FUELHH/stream"
WINDFOR_PARAMS = {
    "publishDateTimeFrom": "2026-01-01T00:00:00Z",
    "publishDateTimeTo": "2026-02-01T00:00:00Z",
}
FUELHH_PARAMS = {
    "settlementDateFrom": "2026-01-02",
    "settlementDateTo": "2026-02-02",
}
WINDFOR_URL = (
    f"{WINDFOR_STREAM}?publishDateTimeFrom=2026-01-01T00%3A00%3A00Z"
    "&publishDateTimeTo=2026-02-01T00%3A00%3A00Z"
)
FUELHH_URL = (
    f"{FUELHH_STREAM}?settlementDateFrom=2026-01-02&settlementDateTo=2026-02-02"
)
SOURCE_FIELD_DEFINITIONS = {
    "WINDFOR.startTime": "Delivery interval start. In this extract the series is hourly.",
    "WINDFOR.publishTime": "Forecast publication time. A revision is eligible only when publishTime <= the decision cutoff.",
    "WINDFOR.generation": "Forecast wind generation in MW.",
    "FUELHH.startTime": "Settlement-period start. FUELHH is half-hourly.",
    "FUELHH.publishTime": "Outturn publication time. A historical actual may enter training only when this is at or before the decision cutoff.",
    "FUELHH.generation": "Observed wind generation in MW after fuelType=WIND.",
    "FUELHH.fuelType": "Restricted to WIND. Other fuels are discarded.",
    "signedError": "Forecast MW minus actual MW. Positive means the forecast was above observed wind.",
}
SCENARIO_ASSUMPTIONS = [
    "Decision dates are 1–31 January 2026. Delivery is the following London calendar day, through 1 February 2026.",
    "The raw forecast at decision D is the latest WINDFOR revision with publishTime <= D's Europe/London cutoff.",
    "Historical signed error is that earlier forecast minus the actual revision whose publication time was <= D's cutoff.",
    "Expanding correction uses every causally eligible pair from decision dates before D.",
    "Rolling correction uses only pairs from the previous seven decision dates.",
    "Both corrections require at least 72 causally eligible pairs. Below that, the adjusted forecast is left unavailable.",
    "Adjusted forecast = max(0, raw forecast - mean signed error). Missing forecasts stay missing.",
    "Retrospective scores use the latest FUELHH revision and are not treated as known at decision time.",
    "Join key is exact startTime. Adjacent half-hours are not averaged.",
]
SCENARIO_LIMITATIONS = [
    "Historical energy-forecasting pilot for one calendar month (January 2026 decision dates).",
    "WINDFOR is hourly and FUELHH wind outturn is half-hourly. Exact timestamp matching leaves the unobserved half-hours missing. No synthetic records are inserted.",
    "An hourly sensitivity that would average adjacent FUELHH half-hours was not implemented. The source metadata used here does not define those settlement periods as an hourly integral, so averaging would invent a generation measure.",
    "Exploratory baselines only. The 7-day window and the 72-pair threshold were fixed before looking at results and were not tuned to improve January scores.",
    "Walk-forward scores include days where a correction makes the error worse.",
    "Demand, prices, and portfolio positions are not in these datasets. This is not a trading order, a procurement quantity, or a financial saving.",
    "Realized generation, realized error, and whether an adjustment helped are known only after delivery.",
    "No confidence interval is reported. One month is not a statistical validation of a forward strategy.",
]


def parse_utc(value: Any) -> datetime | None:
    if not value or not isinstance(value, str):
        return None
    try:
        parsed = datetime.fromisoformat(value.strip().replace("Z", "+00:00"))
        if parsed.tzinfo is None:
            # Elexon's stream times are UTC. Treat any missing offset as UTC.
            parsed = parsed.replace(tzinfo=UTC)
        return parsed.astimezone(UTC)
    except ValueError:
        return None


def number(value: Any) -> float | None:
    if isinstance(value, bool):
        return None
    try:
        result = float(value)
        return result if isfinite(result) and result >= 0 else None
    except (TypeError, ValueError, OverflowError):
        return None


def normalize(records: list[dict], *, actual: bool) -> list[dict]:
    """Normalize Elexon records; reject malformed, negative and non-wind actual rows."""
    out = []
    for record in records:
        if not isinstance(record, dict):
            continue
        if actual and record.get("fuelType") != "WIND":
            continue
        start = parse_utc(record.get("startTime"))
        publish = parse_utc(record.get("publishTime"))
        generation = number(record.get("generation"))
        if start is None or generation is None or (not actual and publish is None):
            continue
        normalized = {
            "startTime": start.isoformat(),
            "publishTime": publish.isoformat() if publish else None,
            "generation": generation,
        }
        if actual:
            normalized["fuelType"] = "WIND"
        out.append(normalized)
    return out


def decision_cutoff(day: date, cutoff: str) -> datetime:
    hour, minute = map(int, cutoff.split(":"))
    if not (0 <= hour <= 23 and 0 <= minute <= 59):
        raise ValueError("Invalid cutoff")
    return datetime.combine(day, time(hour, minute), tzinfo=LONDON).astimezone(UTC)


def quantile(values: list[float], fraction: float) -> float | None:
    """Nearest-rank P90. Easy to explain to a trading desk."""
    if not values:
        return None
    return sorted(values)[max(0, ceil(fraction * len(values)) - 1)]


def metrics(rows: list[dict]) -> dict:
    matched = [r for r in rows if r.get("forecast") is not None and r.get("actual") is not None]
    if not matched:
        return {"count": 0, "maeMw": None, "rmseMw": None, "biasMw": None,
                "p90AbsoluteErrorMw": None, "overforecastRatePct": None, "meanActualMw": None}
    errors = [r["forecast"] - r["actual"] for r in matched]
    return {
        "count": len(matched),
        "maeMw": round(mean(abs(e) for e in errors), 2),
        "rmseMw": round(mean(e * e for e in errors) ** 0.5, 2),
        "biasMw": round(mean(errors), 2),
        "p90AbsoluteErrorMw": round(quantile([abs(e) for e in errors], .90), 2),
        "overforecastRatePct": round(100 * sum(e > 0 for e in errors) / len(errors), 1),
        "meanActualMw": round(mean(r["actual"] for r in matched), 2),
    }


def analyze(actual_records: list[dict], forecast_records: list[dict], cutoff: str = "09:50") -> dict:
    """For each Jan-2026 decision day, select newest forecast visible as of D-1 cutoff.

    Decision days Jan 1-31 correspond to delivery Jan 2 - Feb 1 inclusive.
    Only exact target timestamps are joined, with no interpolation or look-ahead.
    """
    # Ensure cutoff is valid even if the cache is empty.
    decision_cutoff(JAN_START, cutoff)
    actual_revisions: dict[datetime, list[tuple[datetime, float]]] = defaultdict(list)
    for r in normalize(actual_records, actual=True):
        start = parse_utc(r["startTime"])
        publication = parse_utc(r["publishTime"]) or datetime.min.replace(tzinfo=UTC)
        actual_revisions[start].append((publication, r["generation"]))
    for revisions in actual_revisions.values():
        revisions.sort(key=lambda pair: pair[0])
    # Most recently published outturn is the retrospective reference.
    actuals = {target: revisions[-1] for target, revisions in actual_revisions.items()}

    forecasts: dict[datetime, list[tuple[datetime, float]]] = defaultdict(list)
    for r in normalize(forecast_records, actual=False):
        start, publication = parse_utc(r["startTime"]), parse_utc(r["publishTime"])
        forecasts[start].append((publication, r["generation"]))
    for candidates in forecasts.values():
        candidates.sort(key=lambda p: p[0])

    periods = []
    daily = []
    total_expected = 0
    total_available = 0
    total_actual_available = 0
    for i in range(31):
        day = JAN_START + timedelta(days=i)
        delivery = day + timedelta(days=1)
        cutoff_utc = decision_cutoff(day, cutoff)
        # January 2026 is GMT; local-day construction also handles future DST changes.
        local_start = datetime.combine(delivery, time.min, tzinfo=LONDON)
        local_end = datetime.combine(delivery + timedelta(days=1), time.min, tzinfo=LONDON)
        count = int((local_end.astimezone(UTC) - local_start.astimezone(UTC)).total_seconds() // 1800)
        total_expected += count
        daily_rows = []
        for n in range(count):
            target = local_start.astimezone(UTC) + timedelta(minutes=30 * n)
            actual_entry = actuals.get(target)
            actual = actual_entry[1] if actual_entry else None
            # Binary search would be possible; these lists are small in normal WINDFOR.
            eligible = [p for p in forecasts.get(target, []) if p[0] <= cutoff_utc]
            selected = eligible[-1] if eligible else None
            forecast = selected[1] if selected else None
            row = {
                "decisionDate": day.isoformat(),
                "deliveryDate": delivery.isoformat(),
                "startTime": target.isoformat(),
                "forecast": forecast,
                "actual": actual,
                "publishTime": selected[0].isoformat() if selected else None,
                "actualPublishTime": actual_entry[0].isoformat() if actual_entry and actual_entry[0] != datetime.min.replace(tzinfo=UTC) else None,
                "error": round(forecast - actual, 2) if forecast is not None and actual is not None else None,
            }
            daily_rows.append(row)
            periods.append(row)
        paired = sum(r["error"] is not None for r in daily_rows)
        predicted = sum(r["forecast"] is not None for r in daily_rows)
        observed = sum(r["actual"] is not None for r in daily_rows)
        total_available += predicted
        total_actual_available += observed
        # The trailing error estimate is computed using historical forecasts
        # AND FUELHH *revisions available by this particular cutoff*. A later
        # correction is not retroactively visible to an earlier buyer.
        prior_errors = []
        for prev in periods[:-len(daily_rows)]:
            if prev["forecast"] is None:
                continue
            revisions = actual_revisions.get(parse_utc(prev["startTime"]), [])
            eligible_actuals = [p for p in revisions
                                if p[0] != datetime.min.replace(tzinfo=UTC) and p[0] <= cutoff_utc]
            if eligible_actuals:
                prior_errors.append(prev["forecast"] - eligible_actuals[-1][1])
        prior_mae = round(mean(abs(e) for e in prior_errors), 2) if prior_errors else None
        day_metrics = metrics(daily_rows)
        paired_rows = [r for r in daily_rows if r["error"] is not None]
        daily.append({
            "priorKnownMaeMw": prior_mae,
            "priorKnownMatchedPeriods": len(prior_errors),
            "matchedForecastMWh": round(sum(r["forecast"] * 0.5 for r in paired_rows), 2),
            "matchedActualMWh": round(sum(r["actual"] * 0.5 for r in paired_rows), 2),
            "netEnergyErrorMWh": round(sum(r["error"] * 0.5 for r in paired_rows), 2),
            "decisionDate": day.isoformat(),
            "deliveryDate": delivery.isoformat(),
            "coveragePct": round(100 * predicted / count, 1),
            "matched": paired,
            "predicted": predicted,
            "actualAvailable": observed,
            "expected": count,
            **day_metrics,
        })
    total_metrics = metrics(periods)
    paired_rows = [r for r in periods if r["error"] is not None]
    total_metrics.update({
        "matchedForecastMWh": round(sum(r["forecast"] * 0.5 for r in paired_rows), 2),
        "matchedActualMWh": round(sum(r["actual"] * 0.5 for r in paired_rows), 2),
        "netEnergyErrorMWh": round(sum(r["error"] * 0.5 for r in paired_rows), 2),
        "expectedPeriods": total_expected,
        "forecastPeriods": total_available,
        "actualPeriods": total_actual_available,
        "coveragePct": round(100 * total_available / total_expected, 1),
        "matchedCoveragePct": round(100 * total_metrics["count"] / total_expected, 1),
    })
    return {
        "metrics": total_metrics,
        "daily": daily,
        "periods": periods,
        "cutoff": cutoff,
        "decisionWindow": {"from": JAN_START.isoformat(), "to": JAN_END.isoformat()},
        "deliveryWindow": {"from": "2026-01-02", "to": "2026-02-01"},
    }


# ---------------------------------------------------------------------------
# Walk-forward bias-correction backtest
# ---------------------------------------------------------------------------

def _build_actual_revisions(actual_records: list[dict]) -> dict[datetime, list[tuple[datetime, float]]]:
    """Build sorted per-timestamp list of (publish_time, generation) from FUELHH records."""
    actual_revisions: dict[datetime, list[tuple[datetime, float]]] = defaultdict(list)
    for r in normalize(actual_records, actual=True):
        start = parse_utc(r["startTime"])
        publication = parse_utc(r["publishTime"]) or datetime.min.replace(tzinfo=UTC)
        actual_revisions[start].append((publication, r["generation"]))
    for revisions in actual_revisions.values():
        revisions.sort(key=lambda p: p[0])
    return actual_revisions


def _build_forecast_index(forecast_records: list[dict]) -> dict[datetime, list[tuple[datetime, float]]]:
    """Build sorted per-timestamp list of (publish_time, generation) from WINDFOR records."""
    forecast_index: dict[datetime, list[tuple[datetime, float]]] = defaultdict(list)
    for r in normalize(forecast_records, actual=False):
        start, pub = parse_utc(r["startTime"]), parse_utc(r["publishTime"])
        if start is not None and pub is not None:
            forecast_index[start].append((pub, r["generation"]))
    for candidates in forecast_index.values():
        candidates.sort(key=lambda p: p[0])
    return forecast_index


def _day_slot_count(delivery: date) -> tuple[datetime, int]:
    """Return (UTC start of delivery day, number of 30-min slots)."""
    local_start = datetime.combine(delivery, time.min, tzinfo=LONDON)
    local_end = datetime.combine(delivery + timedelta(days=1), time.min, tzinfo=LONDON)
    n = int((local_end.astimezone(UTC) - local_start.astimezone(UTC)).total_seconds() // 1800)
    return local_start.astimezone(UTC), n


def walk_forward_backtest(
    actual_records: list[dict],
    forecast_records: list[dict],
    cutoff: str = "09:50",
) -> dict:
    """Strictly causal walk-forward bias-correction backtest.

    For each January 2026 decision day D (in chronological order):

    Training window (strictly causal, no leakage):
      All prior decision days D' < D where:
        - A WINDFOR forecast was visible at D' decision cutoff
        - The corresponding FUELHH actual revision was published at or before
          D's cutoff (i.e. the buyer on day D could have already seen it)

    Adjustment:
      mean_signed_error = mean(forecast - actual) over training window
      adjusted_forecast(t) = max(0, raw_forecast(t) - mean_signed_error)

    Minimum training window: MIN_BACKTEST_PAIRS matched records.
    Before that threshold, adjustedForecast and adjustedError are null.

    Evaluation:
      Both original and adjusted forecasts are evaluated on the *same*
      matched pairs using retrospective FUELHH actuals (latest revision).

    LABEL: Exploratory retrospective analysis only. Not a proven trading
    strategy, prediction interval, or financial saving estimate.

    WINDFOR/FUELHH alignment note:
      WINDFOR is published hourly; FUELHH is half-hourly. Pairs are matched
      on exact startTime only. Approximately 50% of half-hourly slots have
      no WINDFOR entry and are excluded from all counts.
    """
    decision_cutoff(JAN_START, cutoff)

    actual_revisions = _build_actual_revisions(actual_records)
    # Retrospective outturn: latest revision regardless of cutoff (for evaluation)
    retro_actuals: dict[datetime, float] = {
        t: revisions[-1][1] for t, revisions in actual_revisions.items()
    }
    forecast_index = _build_forecast_index(forecast_records)

    # Precompute per-decision-day: list of (target_utc, forecast_mw) pairs that
    # had an eligible forecast at that day's cutoff.  We use these when building
    # the training window for subsequent days.
    day_forecast_pairs: list[list[tuple[datetime, float]]] = []

    daily = []

    for i in range(31):
        day = JAN_START + timedelta(days=i)
        delivery = day + timedelta(days=1)
        cutoff_utc = decision_cutoff(day, cutoff)
        day_start_utc, count = _day_slot_count(delivery)

        # --- Build causal training window for day D ---
        # Scan all prior days D' < D and collect matched pairs where the
        # FUELHH actual for D''s delivery was published at or before D's cutoff.
        causal_pairs: list[tuple[float, float]] = []
        for prior_i, prior_fc_list in enumerate(day_forecast_pairs):
            for target_ts, fc_mw in prior_fc_list:
                revisions = actual_revisions.get(target_ts, [])
                causal_actual: float | None = None
                for pub_ts, gen in revisions:
                    if pub_ts != datetime.min.replace(tzinfo=UTC) and pub_ts <= cutoff_utc:
                        causal_actual = gen  # take latest eligible revision
                if causal_actual is not None:
                    causal_pairs.append((fc_mw, causal_actual))

        n_training = len(causal_pairs)
        has_enough = n_training >= MIN_BACKTEST_PAIRS
        mean_bias = round(mean(f - a for f, a in causal_pairs), 2) if has_enough else None

        # --- Collect today's forecast slots (for future training windows) ---
        today_fc_list: list[tuple[datetime, float]] = []
        for n in range(count):
            target = day_start_utc + timedelta(minutes=30 * n)
            eligible = [p for p in forecast_index.get(target, []) if p[0] <= cutoff_utc]
            if eligible:
                today_fc_list.append((target, eligible[-1][1]))
        day_forecast_pairs.append(today_fc_list)

        # --- Evaluate on today's delivery periods ---
        orig_rows: list[dict] = []
        adj_rows: list[dict] = []
        period_details: list[dict] = []

        for n in range(count):
            target = day_start_utc + timedelta(minutes=30 * n)
            eligible = [p for p in forecast_index.get(target, []) if p[0] <= cutoff_utc]
            selected = eligible[-1] if eligible else None
            raw_fc = selected[1] if selected else None
            retro_actual = retro_actuals.get(target)

            adj_fc = (
                max(0.0, raw_fc - mean_bias)
                if raw_fc is not None and mean_bias is not None else None
            )
            orig_error = (
                round(raw_fc - retro_actual, 2)
                if raw_fc is not None and retro_actual is not None else None
            )
            adj_error = (
                round(adj_fc - retro_actual, 2)
                if adj_fc is not None and retro_actual is not None else None
            )

            period_details.append({
                "startTime": target.isoformat(),
                "forecast": raw_fc,
                "adjustedForecast": adj_fc,
                "actual": retro_actual,
                "error": orig_error,
                "adjustedError": adj_error,
                "publishTime": selected[0].isoformat() if selected else None,
            })

            if raw_fc is not None and retro_actual is not None:
                orig_rows.append({"forecast": raw_fc, "actual": retro_actual})
                if adj_fc is not None:
                    adj_rows.append({"forecast": adj_fc, "actual": retro_actual})

        orig_m = metrics(orig_rows)
        adj_m = metrics(adj_rows) if (has_enough and adj_rows) else None

        same_eval = [r for r in period_details
                     if r["error"] is not None and r["adjustedError"] is not None]
        improved = sum(1 for r in same_eval if abs(r["adjustedError"]) < abs(r["error"]))
        worsened = sum(1 for r in same_eval if abs(r["adjustedError"]) > abs(r["error"]))

        daily.append({
            "decisionDate": day.isoformat(),
            "deliveryDate": delivery.isoformat(),
            "trainingPairs": n_training,
            "hasEnoughHistory": has_enough,
            "meanBiasMw": mean_bias,
            "originalMetrics": orig_m,
            "adjustedMetrics": adj_m,
            "improvedPeriods": improved if has_enough else None,
            "worsenedPeriods": worsened if has_enough else None,
            "periods": period_details,
        })

    eligible_days = [d for d in daily if d["hasEnoughHistory"]]
    all_orig = []
    all_adj = []
    for d in eligible_days:
        for p in d["periods"]:
            if p["error"] is not None and p["adjustedError"] is not None:
                all_orig.append({"forecast": p["forecast"], "actual": p["actual"]})
                all_adj.append({"forecast": p["adjustedForecast"], "actual": p["actual"]})

    return {
        "cutoff": cutoff,
        "minTrainingPairs": MIN_BACKTEST_PAIRS,
        "daysWithEnoughHistory": len(eligible_days),
        "summaryOriginal": metrics(all_orig),
        "summaryAdjusted": metrics(all_adj) if all_adj else None,
        "disclaimer": (
            "Exploratory retrospective analysis only. The mean bias is estimated from one "
            "calendar month of data and has high sampling uncertainty. This does not represent "
            "a proven forward strategy, a prediction interval, or a financial saving estimate."
        ),
        "alignmentNote": (
            "WINDFOR publishes at hourly granularity; FUELHH reports half-hourly. "
            "Matched pairs use only exact startTime equality — no interpolation. "
            "Roughly 50% of half-hourly delivery slots lack a direct WINDFOR entry. "
            "Bias correction is trained and evaluated only on matched pairs."
        ),
        "daily": daily,
    }


# ---------------------------------------------------------------------------
# Decision risk brief
# ---------------------------------------------------------------------------

def decision_risk_brief(
    actual_records: list[dict],
    forecast_records: list[dict],
    cutoff: str = "09:50",
    target_date: str | None = None,
) -> dict:
    """Per-decision-day risk brief using only data available at cutoff.

    For the requested decision date:
      - Latest eligible WINDFOR publication visible at cutoff
      - Data freshness (hours between latest forecast publish and cutoff)
      - Overforecast tendency from prior days (causal, cutoff-filtered)
      - Recent large misses (last 7 prior days with matched pairs)
      - All supporting sample counts shown explicitly

    Deliberately avoids:
      - Any look-ahead beyond the specified cutoff
      - Demand, price, thermal-generation, or procurement volume calculations
      - Presenting historical tendencies as guaranteed future performance
    """
    decision_cutoff(JAN_START, cutoff)

    actual_revisions = _build_actual_revisions(actual_records)
    forecast_index = _build_forecast_index(forecast_records)

    # Resolve and clamp target date
    target_day = JAN_END
    if target_date:
        try:
            target_day = date.fromisoformat(target_date)
        except ValueError:
            pass
    target_day = max(JAN_START, min(JAN_END, target_day))

    target_cutoff_utc = decision_cutoff(target_day, cutoff)
    target_delivery = target_day + timedelta(days=1)
    day_start_utc, count = _day_slot_count(target_delivery)

    # Forecast summary for target day
    latest_pub: datetime | None = None
    forecast_sum = 0.0
    n_forecast = 0
    for n in range(count):
        t = day_start_utc + timedelta(minutes=30 * n)
        eligible = [p for p in forecast_index.get(t, []) if p[0] <= target_cutoff_utc]
        if eligible:
            pub_ts, gen = eligible[-1]
            forecast_sum += gen
            n_forecast += 1
            if latest_pub is None or pub_ts > latest_pub:
                latest_pub = pub_ts

    mean_forecast_mw = round(forecast_sum / n_forecast, 1) if n_forecast else None
    freshness_hours = (
        round((target_cutoff_utc - latest_pub).total_seconds() / 3600, 1)
        if latest_pub else None
    )

    # Build raw prior errors: all periods from days before target where both
    # forecast existed (at that day's cutoff) and FUELHH actual was published
    # by the TARGET day's cutoff.
    raw_prior_errors: list[float] = []
    prior_day_results: list[dict] = []

    for i in range(31):
        day = JAN_START + timedelta(days=i)
        if day >= target_day:
            break
        delivery = day + timedelta(days=1)
        d_cutoff_utc = decision_cutoff(day, cutoff)
        d_start_utc, d_count = _day_slot_count(delivery)
        day_errors: list[float] = []
        for n in range(d_count):
            t = d_start_utc + timedelta(minutes=30 * n)
            elig = [p for p in forecast_index.get(t, []) if p[0] <= d_cutoff_utc]
            if not elig:
                continue
            fc = elig[-1][1]
            revisions = actual_revisions.get(t, [])
            causal = [p for p in revisions
                      if p[0] != datetime.min.replace(tzinfo=UTC) and p[0] <= target_cutoff_utc]
            if causal:
                day_errors.append(fc - causal[-1][1])
        if day_errors:
            raw_prior_errors.extend(day_errors)
            prior_day_results.append({
                "decisionDate": day.isoformat(),
                "n": len(day_errors),
                "maeMw": round(mean(abs(e) for e in day_errors), 1),
                "biasMw": round(mean(day_errors), 1),
                "overforecastRatePct": round(100 * sum(e > 0 for e in day_errors) / len(day_errors), 1),
            })

    n_prior = len(raw_prior_errors)
    prior_bias = round(mean(raw_prior_errors), 1) if raw_prior_errors else None
    prior_mae = round(mean(abs(e) for e in raw_prior_errors), 1) if raw_prior_errors else None
    prior_overforecast_pct = (
        round(100 * sum(e > 0 for e in raw_prior_errors) / n_prior, 1)
        if raw_prior_errors else None
    )

    # Tendency label
    if prior_bias is None or n_prior < 10:
        tendency_label = "insufficient_history"
    elif prior_bias > 50:
        tendency_label = "systematic_overforecast"
    elif prior_bias < -50:
        tendency_label = "systematic_underforecast"
    else:
        tendency_label = "near_neutral"

    # Recent large misses (last 7 prior days)
    recent_days = prior_day_results[-7:]
    large_miss_threshold = round((prior_mae or 0) * 1.5, 1)
    large_miss_days = [
        d for d in recent_days
        if d["maeMw"] > large_miss_threshold and large_miss_threshold > 0
    ]

    return {
        "decisionDate": target_day.isoformat(),
        "deliveryDate": target_delivery.isoformat(),
        "cutoff": cutoff,
        "forecastSummary": {
            "meanForecastMw": mean_forecast_mw,
            "periodsWithForecast": n_forecast,
            "totalPeriods": count,
            "coveragePct": round(100 * n_forecast / count, 1) if count else None,
            "latestPublishTime": latest_pub.isoformat() if latest_pub else None,
            "dataFreshnessHours": freshness_hours,
        },
        "priorHistory": {
            "matchedPairs": n_prior,
            "biasMw": prior_bias,
            "maeMw": prior_mae,
            "overforecastRatePct": prior_overforecast_pct,
            "tendencyLabel": tendency_label,
            "note": (
                f"Based on {n_prior} matched pairs from prior decision days. "
                f"Actuals restricted to those published at or before {cutoff} UK time on {target_day}."
            ),
        },
        "recentDailyHistory": recent_days,
        "largeMissDays": large_miss_days,
        "largeMissThresholdMw": large_miss_threshold if large_miss_threshold > 0 else None,
        "alignmentNote": (
            "WINDFOR hourly vs FUELHH half-hourly: matched on exact startTime only. "
            "~50% of half-hourly periods lack a WINDFOR entry and are excluded. "
            "Risk indicators reflect matched-pair observations only."
        ),
        "disclaimer": (
            "Historical pattern awareness only. Does not forecast demand, compute thermal "
            "generation requirements, or recommend purchase volumes. Prior overforecast "
            "tendency does not guarantee the same pattern on the target delivery day."
        ),
    }


# ---------------------------------------------------------------------------
# Decision Scenario Workbench & Reproducibility Engine
# ---------------------------------------------------------------------------

def _history_summary(pairs: list[tuple[float, float]]) -> dict:
    """Descriptive error stats for a causal sample. Not an applied adjustment."""
    if not pairs:
        return {"count": 0, "meanSignedErrorMw": None, "maeMw": None}
    errors = [forecast - actual for forecast, actual in pairs]
    return {
        "count": len(errors),
        "meanSignedErrorMw": round(mean(errors), 2),
        "maeMw": round(mean(abs(error) for error in errors), 2),
    }


def _mean_present(rows: list[dict], key: str) -> float | None:
    values = [row[key] for row in rows if row.get(key) is not None]
    return round(mean(values), 1) if values else None


def _population_block(
    original_rows: list[dict],
    adjusted_rows: list[dict],
    *,
    eligible_days: int,
    improved: int,
    worsened: int,
    unchanged: int,
    label: str,
    note: str,
) -> dict:
    """Score one eligibility rule on its own matched rows. Do not mix it with another rule's rows."""
    return {
        "label": label,
        "note": note,
        "eligibleDays": eligible_days,
        "matchedObservations": len(original_rows),
        "original": metrics(original_rows),
        "adjusted": metrics(adjusted_rows) if adjusted_rows else None,
        "daysImproved": improved,
        "daysWorsened": worsened,
        "daysUnchanged": unchanged,
    }


def compute_dataset_hash(actual_records: list[dict], forecast_records: list[dict]) -> str:
    """Compute a deterministic SHA-256 fingerprint over normalized Elexon records.

    Ensures analysis reports are strictly reproducible and traceable to a specific
    immutable snapshot of source records without exposing server paths or secrets.
    """
    hasher = hashlib.sha256()
    actual_norm = sorted(
        (r["startTime"], r["generation"], r.get("publishTime") or "")
        for r in normalize(actual_records, actual=True)
    )
    forecast_norm = sorted(
        (r["startTime"], r["generation"], r["publishTime"])
        for r in normalize(forecast_records, actual=False)
    )
    meta = f"act_count:{len(actual_norm)}|fc_count:{len(forecast_norm)};"
    hasher.update(meta.encode("utf-8"))
    for item in actual_norm:
        hasher.update(f"a:{item[0]}:{item[1]}:{item[2]};".encode("utf-8"))
    for item in forecast_norm:
        hasher.update(f"f:{item[0]}:{item[1]}:{item[2]};".encode("utf-8"))
    return hasher.hexdigest()


def _scenario_parameters() -> dict:
    return {
        "minTrainingPairs": MIN_BACKTEST_PAIRS,
        "rollingWindowDecisionDates": ROLLING_WINDOW_DECISION_DATES,
        "nonnegativeRule": "max(0, raw_forecast - bias_estimate)",
        "errorDefinition": "signed error = forecast MW - actual MW",
        "matchRule": "exact startTime equality; missing values stay missing",
        "timezone": "Europe/London",
        "biasEstimate": "mean of causally eligible signed errors",
    }


def _provenance(content_hash: str, retrieved_at: str | None) -> dict:
    return {
        "windforDatasetUrl": WINDFOR_STREAM,
        "fuelhhDatasetUrl": FUELHH_STREAM,
        "windforUrl": WINDFOR_URL,
        "fuelhhUrl": FUELHH_URL,
        "windforParams": dict(WINDFOR_PARAMS),
        "fuelhhParams": dict(FUELHH_PARAMS),
        "fuelTypeFilter": "WIND",
        "contentHash": content_hash,
        "snapshotId": content_hash,
        "schemaVersion": 3,
        "retrievedAt": retrieved_at,
    }


def _reproducibility(content_hash: str) -> dict:
    return {
        "algorithmVersion": ALGORITHM_VERSION,
        "snapshotId": content_hash,
        "parameters": _scenario_parameters(),
        "sourceFieldDefinitions": dict(SOURCE_FIELD_DEFINITIONS),
        "assumptions": list(SCENARIO_ASSUMPTIONS),
        "hourlyAlignmentSensitivity": (
            "not_implemented: adjacent FUELHH half-hours are not averaged. "
            "Hourly versus half-hourly populations were not shown to be the same physical interval."
        ),
    }


def render_report_markdown(document: dict) -> str:
    """Readable companion for the JSON analysis report. No server paths or secrets."""
    identity = document["identity"]
    provenance = document["provenance"]
    known = document["knownAtDecisionTime"]
    learned = document["learnedAfterDelivery"]
    retro = learned["retrospective"]
    month = learned["monthSummary"]
    lines = [
        "# Windward day-ahead forecast decision report",
        "",
        "KNOWN AT DECISION TIME and LEARNED AFTER DELIVERY are separate sections. "
        "Retrospective errors were not available when the cutoff passed.",
        "",
        "## Identity",
        f"- Project: {identity['project']}",
        f"- Decision date: {identity['decisionDate']}",
        f"- Delivery date: {identity['deliveryDate']}",
        f"- UK cutoff: {identity['cutoff']}",
        f"- Report creation timestamp: {identity['generatedAt']}",
        f"- Selected strategy: {document['selectedStrategy']}",
        f"- Algorithm version: {document['algorithmVersion']}",
        "",
        "## Provenance",
        f"- WINDFOR: {provenance['windforUrl']}",
        f"- FUELHH: {provenance['fuelhhUrl']}",
        f"- Fuel filter: {provenance['fuelTypeFilter']}",
        f"- Dataset retrieval timestamp: {provenance.get('retrievedAt') or 'not recorded on this snapshot'}",
        f"- Source snapshot content hash: {provenance['contentHash']}",
        "",
        "## Scenario definition",
        f"- Minimum training pairs: {document['scenarioDefinition']['minTrainingPairs']}",
        f"- Rolling window: {document['scenarioDefinition']['rollingWindowDecisionDates']} previous decision dates",
        f"- Nonnegative rule: {document['scenarioDefinition']['nonnegativeRule']}",
        f"- Error definition: {document['scenarioDefinition']['errorDefinition']}",
    ]
    for key, spec in document["scenarioDefinition"]["strategies"].items():
        lines.append(
            f"- {spec['name']} ({key}): status {spec['status']}; "
            f"eligible training pairs {spec['trainingPairs']}; "
            f"bias estimate MW {spec.get('meanBiasMw')}"
        )
    ex = known["exAnte"]
    lines.extend([
        "",
        "## KNOWN AT DECISION TIME",
        f"- Latest forecast publication: {ex.get('latestForecastPublishTime')}",
        f"- Data freshness hours before cutoff: {ex.get('dataFreshnessHours')}",
        f"- Forecast coverage percent: {ex['rawForecastSummary'].get('coveragePct')}",
        f"- Periods with a forecast: {ex['rawForecastSummary'].get('periodsWithForecast')} of {ex['rawForecastSummary'].get('totalPeriods')}",
        "",
        "## LEARNED AFTER DELIVERY",
        "Actual generation, realized error, MAE, RMSE, signed bias, and P90 below use the retrospective outturn. "
        "They were not known at the decision cutoff.",
        f"- Matched observations on this delivery day: {retro['matchedPeriodCount']}",
    ])
    for name, block in retro["metrics"].items():
        if block is None:
            lines.append(f"- {name}: unavailable (insufficient causal history; not scored on a different sample)")
        else:
            lines.append(
                f"- {name}: MAE {block.get('maeMw')} MW, RMSE {block.get('rmseMw')} MW, "
                f"signed bias {block.get('biasMw')} MW, P90 absolute error {block.get('p90AbsoluteErrorMw')} MW, "
                f"n={block.get('count')}"
            )
    comparison = retro["dayComparison"]
    lines.append(f"- Retrospective reading for this delivery day: {comparison['summary']}")
    lines.extend([
        "",
        "## Common-set month scores",
        "Only decision days where both corrections had at least 72 causally eligible pairs are included. "
        "This set is not the full-period matched count from the dashboard.",
        f"- Eligible days in the common set: {month['eligibleDaysBoth']}",
        f"- Common evaluation observations: {month['commonEvaluationObservations']}",
        f"- Expanding days improved / worsened: {month['expandingDaysImproved']} / {month['expandingDaysWorsened']}",
        f"- Rolling days improved / worsened: {month['rollingDaysImproved']} / {month['rollingDaysWorsened']}",
    ])
    expanding_pop = (learned.get("evaluationPopulations") or {}).get("expandingEligible")
    if expanding_pop:
        adjusted = expanding_pop.get("adjusted") or {}
        lines.extend([
            "",
            "## Expanding-eligible walk-forward",
            expanding_pop["note"],
            f"- Eligible days: {expanding_pop['eligibleDays']}",
            f"- Matched observations: {expanding_pop['matchedObservations']}",
            f"- Original MAE MW: {expanding_pop['original'].get('maeMw')}",
            f"- Adjusted MAE MW: {adjusted.get('maeMw')}",
            f"- Days improved / worsened: {expanding_pop['daysImproved']} / {expanding_pop['daysWorsened']}",
        ])
    lines.extend([
        "",
        "## Missing data",
        f"- Delivery periods: {document['results']['missingData']['deliveryPeriods']}",
        f"- Missing forecasts: {document['results']['missingData']['periodsMissingForecast']}",
        f"- Missing actuals: {document['results']['missingData']['periodsMissingActual']}",
        f"- Matched periods: {document['results']['missingData']['matchedPeriods']}",
        "",
        "## Limitations",
    ])
    lines.extend(f"- {item}" for item in document["limitations"])
    lines.append("")
    return "\n".join(lines)


def build_analysis_report(workbench: dict, selected_strategy: str = "all") -> dict:
    """Canonical export document. selected_strategy must be one the workbench computed."""
    allowed = {"all", "original", "expanding", "rolling7Day"}
    if selected_strategy not in allowed:
        raise ValueError(
            f"Unsupported strategy '{selected_strategy}'. Supported: all, original, expanding, rolling7Day."
        )
    strategies = workbench["exAnte"]["strategies"]
    document = {
        "reportType": "Windward Day-Ahead Forecast Decision Scenario Report",
        "algorithmVersion": ALGORITHM_VERSION,
        "selectedStrategy": selected_strategy,
        "identity": workbench["identity"],
        "provenance": workbench["provenance"],
        "scenarioDefinition": {
            "minTrainingPairs": MIN_BACKTEST_PAIRS,
            "rollingWindowDecisionDates": ROLLING_WINDOW_DECISION_DATES,
            "nonnegativeRule": "max(0, raw_forecast - bias_estimate)",
            "errorDefinition": "signed error = forecast MW - actual MW",
            "historyWindows": {
                "original": "none; raw WINDFOR as of the cutoff",
                "expanding": "all causally eligible observations from earlier decision dates",
                "rolling7Day": "causally eligible observations from the previous seven decision dates",
            },
            "strategies": strategies,
            "selectedStrategyDetail": None if selected_strategy == "all" else strategies[selected_strategy],
        },
        "knownAtDecisionTime": {
            "label": "KNOWN AT DECISION TIME",
            "exAnte": workbench["exAnte"],
            "note": "Original forecast, publication time, training counts, bias estimates, and adjusted forecasts in this section use only information available at the cutoff.",
        },
        "learnedAfterDelivery": {
            "label": "LEARNED AFTER DELIVERY. Not available at the decision cutoff.",
            "retrospective": workbench["retrospective"],
            "dailyHistory": workbench["dailyHistory"],
            "monthSummary": workbench["monthSummary"],
            "evaluationPopulations": workbench.get("evaluationPopulations"),
            "note": "Actual wind generation, realized forecast error, MAE, and whether an adjustment improved that delivery day are retrospective.",
        },
        "results": {
            "periods": workbench["periods"],
            "missingData": workbench["missingData"],
            "monthSummary": workbench["monthSummary"],
            "improvedOrWorsened": workbench["retrospective"]["dayComparison"],
        },
        "reproducibility": workbench["reproducibility"],
        "limitations": workbench["limitations"],
    }
    document["markdown"] = render_report_markdown(document)
    return document


def decision_scenario_workbench(
    actual_records: list[dict],
    forecast_records: list[dict],
    cutoff: str = "09:50",
    target_date: str = "2026-01-15",
    retrieved_at: str | None = None,
) -> dict:
    """Comprehensive Day-Ahead Decision Scenario Workbench engine.

    Implements three predefined, deterministic day-ahead forecast strategies:
      1. Original: Raw WINDFOR forecast as-of decision cutoff.
      2. Expanding Mean: Causal subtraction of mean signed error from ALL prior
         decision days whose outturn was published at or before cutoff.
      3. Rolling 7-Day Mean: Causal subtraction of mean signed error from
         the previous seven decision dates [D-7, D-1] whose outturn was
         published at or before cutoff.

    Key invariants:
      - Causal training only (publishTime <= cutoff).
      - Minimum training threshold: MIN_BACKTEST_PAIRS (72 pairs) for both
        correction strategies. Below 72, adjustment is marked unavailable.
      - Non-negative adjusted forecast: max(0.0, raw - bias).
      - Identical matched observation set for all comparisons on any day.
      - Clear separation of Ex-Ante (known at decision time) vs Retrospective
        (learned after delivery).
    """
    try:
        t_day = date.fromisoformat(target_date)
    except (ValueError, TypeError):
        raise ValueError(f"Invalid date format: {target_date}. Expected YYYY-MM-DD.")

    if t_day < JAN_START or t_day > JAN_END:
        raise ValueError(f"Decision date {target_date} out of range. Must be between {JAN_START} and {JAN_END}.")

    t_cutoff_utc = decision_cutoff(t_day, cutoff)
    t_delivery = t_day + timedelta(days=1)

    actual_revisions = _build_actual_revisions(actual_records)
    retro_actuals = {t: revisions[-1][1] for t, revisions in actual_revisions.items()}
    forecast_index = _build_forecast_index(forecast_records)

    # 1. Precompute per-decision-day forecast slots at cutoff for all 31 days
    day_forecast_pairs: list[tuple[date, list[tuple[datetime, float]]]] = []
    for i in range(31):
        d_day = JAN_START + timedelta(days=i)
        d_delivery = d_day + timedelta(days=1)
        d_cutoff_utc = decision_cutoff(d_day, cutoff)
        d_start_utc, d_count = _day_slot_count(d_delivery)
        today_fc: list[tuple[datetime, float]] = []
        for n in range(d_count):
            t = d_start_utc + timedelta(minutes=30 * n)
            elig = [p for p in forecast_index.get(t, []) if p[0] <= d_cutoff_utc]
            if elig:
                today_fc.append((t, elig[-1][1]))
        day_forecast_pairs.append((d_day, today_fc))

    # 2. Build prior causal matched pairs available at t_cutoff_utc for target_date
    prior_pairs_by_day: dict[date, list[tuple[float, float]]] = {}
    for prior_day, prior_fc_list in day_forecast_pairs:
        if prior_day >= t_day:
            break
        day_pairs: list[tuple[float, float]] = []
        for target_ts, fc_mw in prior_fc_list:
            revisions = actual_revisions.get(target_ts, [])
            causal_actual: float | None = None
            for pub_ts, gen in revisions:
                if pub_ts != datetime.min.replace(tzinfo=UTC) and pub_ts <= t_cutoff_utc:
                    causal_actual = gen
            if causal_actual is not None:
                day_pairs.append((fc_mw, causal_actual))
        prior_pairs_by_day[prior_day] = day_pairs

    # Strategy 2: Expanding mean (all prior days < t_day)
    expanding_training_pairs: list[tuple[float, float]] = []
    for d_p, pairs in prior_pairs_by_day.items():
        expanding_training_pairs.extend(pairs)
    n_expanding = len(expanding_training_pairs)
    has_enough_exp = n_expanding >= MIN_BACKTEST_PAIRS
    expanding_history = _history_summary(expanding_training_pairs)
    bias_expanding = expanding_history["meanSignedErrorMw"] if has_enough_exp else None

    # Strategy 3: Rolling 7-day mean (days in [t_day - 7 days, t_day - 1 day])
    rolling_7d_start = t_day - timedelta(days=ROLLING_WINDOW_DECISION_DATES)
    rolling_training_pairs: list[tuple[float, float]] = []
    for d_p, pairs in prior_pairs_by_day.items():
        if rolling_7d_start <= d_p < t_day:
            rolling_training_pairs.extend(pairs)
    n_rolling = len(rolling_training_pairs)
    has_enough_roll = n_rolling >= MIN_BACKTEST_PAIRS
    rolling_history = _history_summary(rolling_training_pairs)
    bias_rolling = rolling_history["meanSignedErrorMw"] if has_enough_roll else None

    # 3. Target delivery day period-by-period forecast construction
    day_start_utc, count = _day_slot_count(t_delivery)
    periods: list[dict] = []
    latest_pub: datetime | None = None
    forecast_sum = 0.0
    n_forecast = 0

    for n in range(count):
        t = day_start_utc + timedelta(minutes=30 * n)
        elig = [p for p in forecast_index.get(t, []) if p[0] <= t_cutoff_utc]
        raw_fc = elig[-1][1] if elig else None
        pub_time = elig[-1][0].isoformat() if elig else None
        if elig:
            forecast_sum += elig[-1][1]
            n_forecast += 1
            if latest_pub is None or elig[-1][0] > latest_pub:
                latest_pub = elig[-1][0]

        retro_act = retro_actuals.get(t)

        exp_fc = (
            max(0.0, raw_fc - bias_expanding)
            if (has_enough_exp and raw_fc is not None and bias_expanding is not None)
            else None
        )
        roll_fc = (
            max(0.0, raw_fc - bias_rolling)
            if (has_enough_roll and raw_fc is not None and bias_rolling is not None)
            else None
        )

        raw_err = round(raw_fc - retro_act, 2) if (raw_fc is not None and retro_act is not None) else None
        exp_err = round(exp_fc - retro_act, 2) if (exp_fc is not None and retro_act is not None) else None
        roll_err = round(roll_fc - retro_act, 2) if (roll_fc is not None and retro_act is not None) else None

        periods.append({
            "period": n + 1,
            "startTime": t.isoformat(),
            "rawForecast": raw_fc,
            "expandingForecast": exp_fc,
            "rollingForecast": roll_fc,
            "actual": retro_act,
            "rawError": raw_err,
            "expandingError": exp_err,
            "rollingError": roll_err,
            "publishTime": pub_time,
        })

    freshness_hours = round((t_cutoff_utc - latest_pub).total_seconds() / 3600, 1) if latest_pub else None

    # 4. Retrospective evaluation on the EXACT same matched periods for target day
    matched_slots = [p for p in periods if p["rawForecast"] is not None and p["actual"] is not None]
    n_matched_day = len(matched_slots)

    orig_eval = metrics([{"forecast": p["rawForecast"], "actual": p["actual"]} for p in matched_slots])
    exp_eval = (
        metrics([{"forecast": p["expandingForecast"], "actual": p["actual"]} for p in matched_slots])
        if has_enough_exp else None
    )
    roll_eval = (
        metrics([{"forecast": p["rollingForecast"], "actual": p["actual"]} for p in matched_slots])
        if has_enough_roll else None
    )

    improved_exp = (
        sum(1 for p in matched_slots if abs(p["expandingError"]) < abs(p["rawError"]))
        if has_enough_exp else None
    )
    worsened_exp = (
        sum(1 for p in matched_slots if abs(p["expandingError"]) > abs(p["rawError"]))
        if has_enough_exp else None
    )

    improved_roll = (
        sum(1 for p in matched_slots if abs(p["rollingError"]) < abs(p["rawError"]))
        if has_enough_roll else None
    )
    worsened_roll = (
        sum(1 for p in matched_slots if abs(p["rollingError"]) > abs(p["rawError"]))
        if has_enough_roll else None
    )

    # 5. Month-wide walk-forward comparison across all 31 decision days
    daily_history = []
    month_common_orig = []
    month_common_exp = []
    month_common_roll = []
    exp_improved_days = 0
    exp_worsened_days = 0
    exp_unchanged_days = 0
    roll_improved_days = 0
    roll_worsened_days = 0
    roll_unchanged_days = 0
    exp_orig_rows: list[dict] = []
    exp_adj_rows: list[dict] = []
    exp_eligible_days = 0
    exp_only_improved = 0
    exp_only_worsened = 0
    exp_only_unchanged = 0
    roll_orig_rows: list[dict] = []
    roll_adj_rows: list[dict] = []
    roll_eligible_days = 0
    roll_only_improved = 0
    roll_only_worsened = 0
    roll_only_unchanged = 0

    for i in range(31):
        cur_day = JAN_START + timedelta(days=i)
        cur_delivery = cur_day + timedelta(days=1)
        cur_cutoff_utc = decision_cutoff(cur_day, cutoff)
        cur_start_utc, cur_count = _day_slot_count(cur_delivery)

        cur_prior_exp = []
        cur_prior_roll = []
        roll_window_start = cur_day - timedelta(days=ROLLING_WINDOW_DECISION_DATES)
        for p_day, p_fc_list in day_forecast_pairs[:i]:
            for target_ts, fc_mw in p_fc_list:
                revisions = actual_revisions.get(target_ts, [])
                causal_act = None
                for pub_ts, gen in revisions:
                    if pub_ts != datetime.min.replace(tzinfo=UTC) and pub_ts <= cur_cutoff_utc:
                        causal_act = gen
                if causal_act is not None:
                    cur_prior_exp.append((fc_mw, causal_act))
                    if roll_window_start <= p_day < cur_day:
                        cur_prior_roll.append((fc_mw, causal_act))

        cur_has_exp = len(cur_prior_exp) >= MIN_BACKTEST_PAIRS
        cur_bias_exp = round(mean(f - a for f, a in cur_prior_exp), 2) if cur_has_exp else None

        cur_has_roll = len(cur_prior_roll) >= MIN_BACKTEST_PAIRS
        cur_bias_roll = round(mean(f - a for f, a in cur_prior_roll), 2) if cur_has_roll else None

        cur_matched = []
        for n in range(cur_count):
            target = cur_start_utc + timedelta(minutes=30 * n)
            elig = [p for p in forecast_index.get(target, []) if p[0] <= cur_cutoff_utc]
            if elig and target in retro_actuals:
                cur_matched.append((target, elig[-1][1], retro_actuals[target]))

        d_orig_mae = round(mean(abs(f - a) for _, f, a in cur_matched), 2) if cur_matched else None
        d_exp_mae = (
            round(mean(abs(max(0.0, f - cur_bias_exp) - a) for _, f, a in cur_matched), 2)
            if (cur_has_exp and cur_matched and cur_bias_exp is not None) else None
        )
        d_roll_mae = (
            round(mean(abs(max(0.0, f - cur_bias_roll) - a) for _, f, a in cur_matched), 2)
            if (cur_has_roll and cur_matched and cur_bias_roll is not None) else None
        )

        if cur_has_exp:
            exp_eligible_days += 1
            if cur_matched and cur_bias_exp is not None:
                for _, forecast_mw, actual_mw in cur_matched:
                    exp_orig_rows.append({"forecast": forecast_mw, "actual": actual_mw})
                    exp_adj_rows.append({"forecast": max(0.0, forecast_mw - cur_bias_exp), "actual": actual_mw})
                if d_exp_mae is not None and d_orig_mae is not None:
                    if d_exp_mae < d_orig_mae:
                        exp_only_improved += 1
                    elif d_exp_mae > d_orig_mae:
                        exp_only_worsened += 1
                    else:
                        exp_only_unchanged += 1
        if cur_has_roll:
            roll_eligible_days += 1
            if cur_matched and cur_bias_roll is not None:
                for _, forecast_mw, actual_mw in cur_matched:
                    roll_orig_rows.append({"forecast": forecast_mw, "actual": actual_mw})
                    roll_adj_rows.append({"forecast": max(0.0, forecast_mw - cur_bias_roll), "actual": actual_mw})
                if d_roll_mae is not None and d_orig_mae is not None:
                    if d_roll_mae < d_orig_mae:
                        roll_only_improved += 1
                    elif d_roll_mae > d_orig_mae:
                        roll_only_worsened += 1
                    else:
                        roll_only_unchanged += 1

        if cur_has_exp and cur_has_roll and cur_matched:
            for _, f, a in cur_matched:
                month_common_orig.append({"forecast": f, "actual": a})
                month_common_exp.append({"forecast": max(0.0, f - cur_bias_exp), "actual": a})
                month_common_roll.append({"forecast": max(0.0, f - cur_bias_roll), "actual": a})
            if d_exp_mae is not None and d_orig_mae is not None:
                if d_exp_mae < d_orig_mae:
                    exp_improved_days += 1
                elif d_exp_mae > d_orig_mae:
                    exp_worsened_days += 1
                else:
                    exp_unchanged_days += 1
            if d_roll_mae is not None and d_orig_mae is not None:
                if d_roll_mae < d_orig_mae:
                    roll_improved_days += 1
                elif d_roll_mae > d_orig_mae:
                    roll_worsened_days += 1
                else:
                    roll_unchanged_days += 1

        daily_history.append({
            "decisionDate": cur_day.isoformat(),
            "deliveryDate": cur_delivery.isoformat(),
            "trainingPairsExpanding": len(cur_prior_exp),
            "trainingPairsRolling": len(cur_prior_roll),
            "hasEnoughExpanding": cur_has_exp,
            "hasEnoughRolling": cur_has_roll,
            "biasExpandingMw": cur_bias_exp,
            "biasRollingMw": cur_bias_roll,
            "origMaeMw": d_orig_mae,
            "expandingMaeMw": d_exp_mae,
            "rollingMaeMw": d_roll_mae,
            "matchedCount": len(cur_matched),
        })

    best_strategy = "original"
    best_mae = orig_eval.get("maeMw")
    if best_mae is None:
        reason_text = (
            "No matched forecast and actual on this delivery day, so no retrospective ranking is available."
        )
    else:
        reason_text = (
            f"Retrospective only: the raw forecast had the lowest MAE on this delivery day "
            f"({best_mae} MW). This was not known at the decision cutoff."
        )
    if exp_eval and exp_eval.get("maeMw") is not None and (best_mae is None or exp_eval["maeMw"] < best_mae):
        best_strategy = "expanding"
        best_mae = exp_eval["maeMw"]
        reason_text = (
            f"Retrospective only: expanding mean correction had a lower MAE "
            f"({best_mae} MW) than the raw forecast ({orig_eval.get('maeMw')} MW) on this delivery day. "
            "This was not known at the decision cutoff."
        )
    if roll_eval and roll_eval.get("maeMw") is not None and (best_mae is None or roll_eval["maeMw"] < best_mae):
        best_strategy = "rolling7Day"
        best_mae = roll_eval["maeMw"]
        reason_text = (
            f"Retrospective only: rolling 7-day correction had a lower MAE "
            f"({best_mae} MW) than the other scored strategies on this delivery day "
            f"(raw MAE {orig_eval.get('maeMw')} MW). This was not known at the decision cutoff."
        )

    periods_with_actual = sum(p["actual"] is not None for p in periods)
    missing_data = {
        "deliveryPeriods": count,
        "periodsWithForecast": n_forecast,
        "periodsWithActual": periods_with_actual,
        "periodsMissingForecast": count - n_forecast,
        "periodsMissingActual": count - periods_with_actual,
        "matchedPeriods": n_matched_day,
    }
    content_hash = compute_dataset_hash(actual_records, forecast_records)
    provenance = _provenance(content_hash, retrieved_at)
    reproducibility = _reproducibility(content_hash)
    evaluation_populations = {
        "expandingEligible": _population_block(
            exp_orig_rows,
            exp_adj_rows,
            eligible_days=exp_eligible_days,
            improved=exp_only_improved,
            worsened=exp_only_worsened,
            unchanged=exp_only_unchanged,
            label="Expanding-history eligible days",
            note=(
                "Same causal rule as the existing walk-forward backtest: a day is included only after "
                "72 prior matched pairs. This is not the full-period matched count on the dashboard."
            ),
        ),
        "rollingEligible": _population_block(
            roll_orig_rows,
            roll_adj_rows,
            eligible_days=roll_eligible_days,
            improved=roll_only_improved,
            worsened=roll_only_worsened,
            unchanged=roll_only_unchanged,
            label="Rolling 7-day eligible days",
            note=(
                "Only the previous seven decision dates contribute, and only after 72 causal pairs. "
                "Scored separately from the expanding-history set."
            ),
        ),
        "commonThreeWay": {
            "label": "Days where both corrections meet the 72-pair threshold",
            "note": (
                "Original, expanding, and rolling forecasts are scored on this identical timestamp set. "
                "Days below the threshold for either correction are excluded rather than borrowed from the full matched period."
            ),
            "eligibleDays": sum(1 for day in daily_history if day["hasEnoughExpanding"] and day["hasEnoughRolling"]),
            "matchedObservations": len(month_common_orig),
            "original": metrics(month_common_orig),
            "expanding": metrics(month_common_exp),
            "rolling": metrics(month_common_roll),
            "expandingDaysImproved": exp_improved_days,
            "expandingDaysWorsened": exp_worsened_days,
            "expandingDaysUnchanged": exp_unchanged_days,
            "rollingDaysImproved": roll_improved_days,
            "rollingDaysWorsened": roll_worsened_days,
            "rollingDaysUnchanged": roll_unchanged_days,
        },
    }

    result = {
        "identity": {
            "project": "Windward Day-Ahead Forecast Decision Workbench",
            "decisionDate": t_day.isoformat(),
            "deliveryDate": t_delivery.isoformat(),
            "cutoff": cutoff,
            "generatedAt": datetime.now(timezone.utc).isoformat(),
            "contentHash": content_hash,
        },
        "exAnte": {
            "decisionDate": t_day.isoformat(),
            "deliveryDate": t_delivery.isoformat(),
            "cutoffUK": cutoff,
            "asOfCutoffUTC": t_cutoff_utc.isoformat(),
            "latestForecastPublishTime": latest_pub.isoformat() if latest_pub else None,
            "dataFreshnessHours": freshness_hours,
            "rawForecastSummary": {
                "meanMw": round(forecast_sum / n_forecast, 1) if n_forecast else None,
                "periodsWithForecast": n_forecast,
                "totalPeriods": count,
                "coveragePct": round(100 * n_forecast / count, 1) if count else None,
            },
            "strategies": {
                "original": {
                    "name": "Original WINDFOR",
                    "type": "raw_baseline",
                    "status": "ready",
                    "hasEnoughHistory": True,
                    "description": "Latest available national wind forecast published at or before cutoff. No bias adjustment applied.",
                    "trainingPairs": 0,
                    "meanBiasMw": 0.0,
                    "adjustmentAppliedMw": 0.0,
                    "deliveryDayMeanMw": _mean_present(periods, "rawForecast"),
                    "trainingError": {"count": 0, "meanSignedErrorMw": None, "maeMw": None},
                },
                "expanding": {
                    "name": "Expanding Mean Bias Correction",
                    "type": "expanding_causal_mean",
                    "status": "ready" if has_enough_exp else "insufficient_history",
                    "hasEnoughHistory": has_enough_exp,
                    "minTrainingPairs": MIN_BACKTEST_PAIRS,
                    "trainingPairs": n_expanding,
                    "meanBiasMw": bias_expanding,
                    "adjustmentAppliedMw": bias_expanding if has_enough_exp else None,
                    "deliveryDayMeanMw": _mean_present(periods, "expandingForecast") if has_enough_exp else None,
                    "formula": "max(0, raw_forecast - expanding_mean_bias)",
                    "description": f"Subtracts causal mean signed error ({bias_expanding if bias_expanding is not None else 'N/A'} MW) calculated from all {n_expanding} prior eligible observations.",
                    "trainingError": expanding_history,
                },
                "rolling7Day": {
                    "name": "Rolling 7-Day Mean Bias Correction",
                    "type": "rolling_7day_causal_mean",
                    "status": "ready" if has_enough_roll else "insufficient_history",
                    "hasEnoughHistory": has_enough_roll,
                    "minTrainingPairs": MIN_BACKTEST_PAIRS,
                    "trainingPairs": n_rolling,
                    "meanBiasMw": bias_rolling,
                    "adjustmentAppliedMw": bias_rolling if has_enough_roll else None,
                    "deliveryDayMeanMw": _mean_present(periods, "rollingForecast") if has_enough_roll else None,
                    "formula": "max(0, raw_forecast - rolling_7day_bias)",
                    "description": f"Subtracts causal mean signed error ({bias_rolling if bias_rolling is not None else 'N/A'} MW) calculated from {n_rolling} observations across previous 7 decision dates [{rolling_7d_start.isoformat()} to {(t_day - timedelta(days=1)).isoformat()}].",
                    "trainingError": rolling_history,
                },
            },
        },
        "periods": periods,
        "retrospective": {
            "deliveryDate": t_delivery.isoformat(),
            "matchedPeriodCount": n_matched_day,
            "metrics": {
                "original": orig_eval,
                "expanding": exp_eval,
                "rolling7Day": roll_eval,
            },
            "dayComparison": {
                "bestStrategy": best_strategy,
                "bestMaeMw": best_mae,
                "summary": reason_text,
                "expandingImprovement": {
                    "improvedPeriods": improved_exp,
                    "worsenedPeriods": worsened_exp,
                } if has_enough_exp else None,
                "rollingImprovement": {
                    "improvedPeriods": improved_roll,
                    "worsenedPeriods": worsened_roll,
                } if has_enough_roll else None,
            },
        },
        "dailyHistory": daily_history,
        "monthSummary": {
            "eligibleDaysBoth": sum(1 for d in daily_history if d["hasEnoughExpanding"] and d["hasEnoughRolling"]),
            "commonEvaluationObservations": len(month_common_orig),
            "commonOriginalMetrics": metrics(month_common_orig),
            "commonExpandingMetrics": metrics(month_common_exp),
            "commonRollingMetrics": metrics(month_common_roll),
            "expandingDaysImproved": exp_improved_days,
            "expandingDaysWorsened": exp_worsened_days,
            "rollingDaysImproved": roll_improved_days,
            "rollingDaysWorsened": roll_worsened_days,
        },
        "evaluationPopulations": evaluation_populations,
        "missingData": missing_data,
        "provenance": provenance,
        "reproducibility": reproducibility,
        "limitations": list(SCENARIO_LIMITATIONS),
        "requestedStrategy": None,
    }
    result["report"] = build_analysis_report(result, selected_strategy="all")
    return result
