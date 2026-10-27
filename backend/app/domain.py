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
