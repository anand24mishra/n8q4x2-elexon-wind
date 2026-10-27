"""Pure historical as-of selection and forecast error calculations.

Forecast selection is strictly as-of cutoff; retrospective metrics use later actuals.
"""
from __future__ import annotations

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
            if p["error"] is not None:
                all_orig.append({"forecast": p["forecast"], "actual": p["actual"]})
            if p["adjustedError"] is not None:
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
