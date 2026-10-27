"""Regression tests for edge cases discovered during the October 2026 code review.

Covers:
- Hourly WINDFOR resolution leaving alternate half-hours without a direct forecast
- Forecast published exactly at the cutoff boundary (≤ cutoff is inclusive)
- Duplicate forecast revisions for same startTime kept in chronological order
- analyze() is safe when no forecast records exist at all
- analyze() is safe when no actual records exist at all
- FUELHH records with publishTime far after delivery do not affect as-of selection
"""
from app.domain import analyze, metrics, normalize, quantile


TARGET = "2026-01-02T00:00:00Z"
TARGET_PLUS_30 = "2026-01-02T00:30:00Z"


def actual(target, generation, publish=None):
    rec = {"startTime": target, "generation": generation, "fuelType": "WIND"}
    if publish:
        rec["publishTime"] = publish
    return rec


def forecast(target, published, generation):
    return {"startTime": target, "publishTime": published, "generation": generation}


def test_hourly_windfor_leaves_half_hour_gaps():
    """WINDFOR publishes at hourly granularity; the intervening half-hour has no forecast.

    This is the expected behaviour for the January 2026 dataset (50% coverage).
    The second half-hour slot must be None, not zero-filled.
    """
    result = analyze(
        [actual(TARGET, 5000), actual(TARGET_PLUS_30, 5100)],
        [forecast(TARGET, "2026-01-01T09:00:00Z", 4800)],
    )
    periods = result["periods"]
    slot_0 = next(p for p in periods if p["startTime"].startswith("2026-01-02T00:00"))
    slot_30 = next(p for p in periods if p["startTime"].startswith("2026-01-02T00:30"))
    assert slot_0["forecast"] == 4800
    assert slot_0["actual"] == 5000
    assert slot_30["forecast"] is None, "Unmatched half-hour must remain None, not zero-filled"
    assert slot_30["actual"] == 5100


def test_forecast_published_exactly_at_cutoff_is_included():
    """A forecast published at exactly the cutoff time is eligible (≤ cutoff)."""
    # 09:50 UK time on 2026-01-01 is 09:50 UTC (GMT in January)
    result = analyze(
        [actual(TARGET, 100)],
        [forecast(TARGET, "2026-01-01T09:50:00Z", 110)],
        cutoff="09:50",
    )
    assert result["periods"][0]["forecast"] == 110


def test_forecast_published_one_second_after_cutoff_is_excluded():
    """A forecast published one second after the cutoff must not be selected."""
    result = analyze(
        [actual(TARGET, 100)],
        [forecast(TARGET, "2026-01-01T09:50:01Z", 110)],
        cutoff="09:50",
    )
    assert result["periods"][0]["forecast"] is None
    assert result["periods"][0]["error"] is None


def test_multiple_revisions_uses_latest_eligible():
    """When multiple WINDFOR revisions exist before the cutoff, the newest wins."""
    result = analyze(
        [actual(TARGET, 100)],
        [
            forecast(TARGET, "2026-01-01T06:00:00Z", 80),
            forecast(TARGET, "2026-01-01T08:00:00Z", 95),
            forecast(TARGET, "2026-01-01T09:49:00Z", 105),
            forecast(TARGET, "2026-01-01T10:00:00Z", 999),  # after cutoff — excluded
        ],
        cutoff="09:50",
    )
    row = result["periods"][0]
    assert row["forecast"] == 105
    assert row["error"] == 5


def test_no_actual_records_does_not_crash():
    """analyze() must handle an empty actuals list gracefully."""
    result = analyze(
        [],
        [forecast(TARGET, "2026-01-01T09:00:00Z", 4000)],
    )
    assert result["metrics"]["count"] == 0
    assert result["metrics"]["maeMw"] is None
    assert result["periods"][0]["actual"] is None
    assert result["periods"][0]["error"] is None


def test_no_forecast_records_does_not_crash():
    """analyze() must handle an empty forecasts list gracefully."""
    result = analyze(
        [actual(TARGET, 4000)],
        [],
    )
    assert result["metrics"]["count"] == 0
    assert result["metrics"]["maeMw"] is None
    assert result["periods"][0]["forecast"] is None


def test_quantile_single_value():
    """Quantile of a one-element list must return that element regardless of fraction."""
    assert quantile([42.0], 0.90) == 42.0
    assert quantile([42.0], 0.10) == 42.0


def test_metrics_empty_rows_returns_nulls():
    """metrics() must return null fields rather than crashing when given no pairs."""
    result = metrics([{"forecast": None, "actual": None}])
    assert result["count"] == 0
    assert result["maeMw"] is None


def test_normalize_rejects_negative_generation():
    """Negative generation values are physically impossible and must be dropped."""
    records = [
        {"startTime": TARGET, "publishTime": "2026-01-01T08:00:00Z", "generation": -500},
        {"startTime": TARGET, "publishTime": "2026-01-01T08:00:00Z", "generation": 500},
    ]
    out = normalize(records, actual=False)
    assert len(out) == 1
    assert out[0]["generation"] == 500


def test_fuelhh_late_publish_time_does_not_influence_forecast_selection():
    """FUELHH records have publishTime far after delivery (e.g., next month).

    This must not affect which WINDFOR forecast is selected; the selection
    is based solely on WINDFOR publishTime vs decision cutoff.
    """
    # FUELHH publish is 2026-02-03, well after all decision cutoffs
    result = analyze(
        [actual(TARGET, 5000, publish="2026-02-03T00:00:00Z")],
        [
            forecast(TARGET, "2026-01-01T08:00:00Z", 4800),
            forecast(TARGET, "2026-01-01T10:30:00Z", 5200),  # after 09:50 cutoff
        ],
        cutoff="09:50",
    )
    row = result["periods"][0]
    assert row["forecast"] == 4800  # only pre-cutoff WINDFOR revision selected
    assert row["actual"] == 5000
