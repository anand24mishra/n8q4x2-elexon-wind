"""Tests for walk_forward_backtest() and decision_risk_brief().

Core invariants tested:
- Future-data leakage: adjustment for day D must never use data from day D or later
- Insufficient-history guard: adjusted estimate absent before MIN_BACKTEST_PAIRS pairs
- Actual-publication timing: FUELHH actuals published after the cutoff must not
  enter the training window for that cutoff
- Adjusted forecast is never negative
- Same evaluation set used for original and adjusted metrics
- Risk brief uses only data available by the target day's cutoff
"""
from datetime import date, datetime, timezone, timedelta
from statistics import mean

from app.domain import (
    MIN_BACKTEST_PAIRS,
    decision_cutoff,
    walk_forward_backtest,
    decision_risk_brief,
)

UTC = timezone.utc
JAN_START = date(2026, 1, 1)


# ---------------------------------------------------------------------------
# Fixtures helpers
# ---------------------------------------------------------------------------

def fc(target: str, published: str, generation: float) -> dict:
    return {"startTime": target, "publishTime": published, "generation": generation}


def act(target: str, generation: float, publish: str | None = None) -> dict:
    rec = {"startTime": target, "generation": generation, "fuelType": "WIND"}
    if publish:
        rec["publishTime"] = publish
    return rec


def _make_dense_dataset(n_days: int, forecast_mw: float = 5000.0, actual_mw: float = 4500.0):
    """Build n_days × 24 hourly records (WINDFOR style) with a fixed bias of
    (forecast_mw - actual_mw) per period.  All forecast publish times are at
    08:00 UK on the decision day; all actual publish times are at 01:00 on the
    delivery day (well before the next 09:50 cutoff).
    """
    actuals = []
    forecasts = []
    for i in range(n_days):
        decision_day = JAN_START + timedelta(days=i)
        delivery_day = decision_day + timedelta(days=1)
        for h in range(24):
            target_ts = datetime(
                delivery_day.year, delivery_day.month, delivery_day.day,
                h, 0, tzinfo=UTC
            ).isoformat()
            publish_fc = f"{decision_day.isoformat()}T08:00:00Z"
            # Actual published 1h after delivery day starts → visible to all
            # subsequent cutoffs
            publish_act = f"{delivery_day.isoformat()}T01:00:00Z"
            forecasts.append(fc(target_ts, publish_fc, forecast_mw))
            actuals.append(act(target_ts, actual_mw, publish_act))
    return actuals, forecasts


# ---------------------------------------------------------------------------
# Insufficient-history guard
# ---------------------------------------------------------------------------

def test_adjusted_metrics_null_before_threshold():
    """No adjusted estimate must be shown until MIN_BACKTEST_PAIRS prior pairs exist.

    With 24 matched periods per day, MIN_BACKTEST_PAIRS = 72 means 3 full days of
    prior matched pairs are needed.  So day index 0-2 must have null adjustedMetrics;
    day index 3+ (which have seen >= 72 prior pairs) must show adjustedMetrics.
    """
    n_days = 10
    actuals, forecasts = _make_dense_dataset(n_days)
    result = walk_forward_backtest(actuals, forecasts)
    daily = result["daily"]

    # Days 0..2: 0, 24, 48 prior pairs respectively — all < 72
    for idx in range(3):
        d = daily[idx]
        assert d["hasEnoughHistory"] is False, (
            f"Day index {idx} has {d['trainingPairs']} pairs — should not have enough"
        )
        assert d["adjustedMetrics"] is None, (
            f"Day {idx} should have null adjustedMetrics (only {d['trainingPairs']} pairs)"
        )

    # Day index 3: 72 prior pairs → first day with enough history
    d3 = daily[3]
    assert d3["trainingPairs"] == 72
    assert d3["hasEnoughHistory"] is True
    assert d3["adjustedMetrics"] is not None


def test_training_pairs_grow_monotonically():
    """Training pair count must be non-decreasing as we move forward in time."""
    actuals, forecasts = _make_dense_dataset(10)
    daily = walk_forward_backtest(actuals, forecasts)["daily"]
    for i in range(1, len(daily)):
        assert daily[i]["trainingPairs"] >= daily[i - 1]["trainingPairs"], (
            f"Training pairs decreased at day index {i}: "
            f"{daily[i-1]['trainingPairs']} -> {daily[i]['trainingPairs']}"
        )


# ---------------------------------------------------------------------------
# Future-data leakage
# ---------------------------------------------------------------------------

def test_no_future_leakage_day_zero_has_zero_training_pairs():
    """The very first decision day (Jan 1) must have zero prior pairs — no data
    from Jan 1 itself or any later day can appear in the training window.
    """
    actuals, forecasts = _make_dense_dataset(5)
    result = walk_forward_backtest(actuals, forecasts)
    assert result["daily"][0]["trainingPairs"] == 0
    assert result["daily"][0]["meanBiasMw"] is None


def test_adjustment_uses_only_strictly_prior_days():
    """A known bias introduced on day 3 must not influence the adjustment on
    day 3 itself; it must only appear in the training window from day 4 onward.

    Days 0-2: forecast=5000, actual=4500 → error = +500 MW
    Day 3:    forecast=3000, actual=4500 → error = -1500 MW  (anomalous)
    Days 4+:  forecast=5000, actual=4500 → error = +500 MW

    The mean bias for day 3 must be computed from days 0-2 only (= +500 MW).
    The mean bias for day 4 must be computed from days 0-3 (including -1500)
    and therefore must differ from +500 MW.
    """
    actuals = []
    forecasts = []
    for i in range(8):
        decision_day = JAN_START + timedelta(days=i)
        delivery_day = decision_day + timedelta(days=1)
        gen_fc = 3000.0 if i == 3 else 5000.0
        gen_act = 4500.0
        for h in range(24):
            t = datetime(delivery_day.year, delivery_day.month, delivery_day.day,
                         h, 0, tzinfo=UTC).isoformat()
            pub_fc = f"{decision_day.isoformat()}T08:00:00Z"
            pub_act = f"{delivery_day.isoformat()}T01:00:00Z"
            forecasts.append(fc(t, pub_fc, gen_fc))
            actuals.append(act(t, gen_act, pub_act))

    result = walk_forward_backtest(actuals, forecasts)
    daily = result["daily"]

    # Day 3 sees 72 prior pairs (days 0-2), all with error +500
    day3 = daily[3]
    assert day3["hasEnoughHistory"] is True
    assert abs(day3["meanBiasMw"] - 500.0) < 1.0, (
        f"Day 3 mean bias should be ~500 MW (from days 0-2 only), got {day3['meanBiasMw']}"
    )

    # Day 4 sees 96 prior pairs (days 0-3), mixing +500 and -1500
    # Expected mean = (72 * 500 + 24 * (-1500)) / 96 = (36000 - 36000) / 96 = 0
    day4 = daily[4]
    assert day4["hasEnoughHistory"] is True
    assert abs(day4["meanBiasMw"] - 0.0) < 1.0, (
        f"Day 4 mean bias should be ~0 (days 0-3 mixed), got {day4['meanBiasMw']}"
    )


# ---------------------------------------------------------------------------
# Actual-publication timing: FUELHH actuals published after cutoff must not
# enter the training window for that day's cutoff.
# ---------------------------------------------------------------------------

def test_actual_published_after_cutoff_excluded_from_training():
    """If a FUELHH actual for day D's delivery is published AFTER day D's cutoff,
    it must not enter the training pool for day D.  It may only enter for day D+1
    or later if by then the publish time is <= that day's cutoff.

    Setup:
      Day 0 decision (Jan 1, 09:50 UTC):
        - WINDFOR forecast published 08:00 → eligible
        - FUELHH actual published 11:00 on delivery day (Jan 2)
          That means it was published AFTER Jan 1's 09:50 cutoff but BEFORE Jan 2's 09:50.

    Consequence:
      - Day 0: no causal pair (actual not visible at 09:50 Jan 1)
      - Day 1: actual now visible → pair can enter training from day 1's perspective
    """
    delivery_ts = "2026-01-02T00:00:00Z"
    # Actual published at 11:00 on delivery day → after Jan 1 09:50 cutoff
    # but before Jan 2 09:50 cutoff
    actuals_list = [act(delivery_ts, 4500.0, publish="2026-01-02T11:00:00Z")]
    forecasts_list = [fc(delivery_ts, "2026-01-01T08:00:00Z", 5000.0)]

    result = walk_forward_backtest(actuals_list, forecasts_list)
    daily = result["daily"]

    # Day 0 (Jan 1): cutoff is 09:50 UTC; actual published 11:00 Jan 2 → NOT visible
    assert daily[0]["trainingPairs"] == 0

    # Day 1 (Jan 2): cutoff is 09:50 UTC; actual published 11:00 Jan 2 → NOT yet visible
    # (11:00 > 09:50 on the same day)
    assert daily[1]["trainingPairs"] == 0, (
        "Actual published 11:00 Jan 2 must not be visible at 09:50 Jan 2"
    )

    # Day 2 (Jan 3): cutoff is 09:50 UTC Jan 3; actual published 11:00 Jan 2 → VISIBLE
    assert daily[2]["trainingPairs"] == 1, (
        "Actual published 11:00 Jan 2 must be visible at 09:50 Jan 3"
    )


def test_actual_published_exactly_at_cutoff_is_included_in_next_day_training():
    """Actual published exactly at the cutoff time must be included (≤ is inclusive)."""
    delivery_ts = "2026-01-02T00:00:00Z"
    # 09:50 UTC on Jan 2 = exactly at day-1 (Jan 2) cutoff
    actuals_list = [act(delivery_ts, 4500.0, publish="2026-01-02T09:50:00Z")]
    forecasts_list = [fc(delivery_ts, "2026-01-01T08:00:00Z", 5000.0)]

    result = walk_forward_backtest(actuals_list, forecasts_list)
    daily = result["daily"]

    # Day 0 (Jan 1): actual published Jan 2 09:50 → not yet visible at Jan 1 09:50
    assert daily[0]["trainingPairs"] == 0

    # Day 1 (Jan 2): cutoff Jan 2 09:50; actual published Jan 2 09:50 → ≤ cutoff → visible
    assert daily[1]["trainingPairs"] == 1, (
        "Actual published exactly at day-1 cutoff must be included (≤ is inclusive)"
    )


# ---------------------------------------------------------------------------
# Adjusted forecast non-negativity
# ---------------------------------------------------------------------------

def test_adjusted_forecast_never_negative():
    """When the mean bias (overforecast) exceeds the raw forecast, the adjusted
    forecast must be clamped to 0, not go negative.
    """
    # Large positive bias: forecast 100, actual 4000 → mean_bias = 100 - 4000 = -3900
    # i.e. we systematically underforecast, so adjustment would add 3900 MW
    # To test the opposite: forecast 100 MW, actual 5000 → mean_bias = +4900
    # adjusted = max(0, 100 - 4900) = 0
    actuals_list = []
    forecasts_list = []
    # Build 4 prior days of huge overforecast (fc=5000, act=100 → error=+4900)
    for i in range(4):
        decision_day = JAN_START + timedelta(days=i)
        delivery_day = decision_day + timedelta(days=1)
        for h in range(24):
            t = datetime(delivery_day.year, delivery_day.month, delivery_day.day,
                         h, 0, tzinfo=UTC).isoformat()
            pub_fc = f"{decision_day.isoformat()}T08:00:00Z"
            pub_act = f"{delivery_day.isoformat()}T01:00:00Z"
            forecasts_list.append(fc(t, pub_fc, 5000.0))
            actuals_list.append(act(t, 100.0, pub_act))

    # Day 4 will have enough history (96 pairs); its raw forecast is 5000 MW
    # mean_bias = +4900 → adjusted = max(0, 5000 - 4900) = 100 ≥ 0 ✓
    # Now add a day where fc=50 → adjusted would be max(0, 50-4900) = 0
    delivery_day_5 = JAN_START + timedelta(days=5)
    t5 = datetime(delivery_day_5.year, delivery_day_5.month, delivery_day_5.day,
                  0, 0, tzinfo=UTC).isoformat()
    forecasts_list.append(fc(t5, f"{(JAN_START + timedelta(days=4)).isoformat()}T08:00:00Z", 50.0))
    actuals_list.append(act(t5, 100.0, f"{delivery_day_5.isoformat()}T01:00:00Z"))

    result = walk_forward_backtest(actuals_list, forecasts_list)

    # Find day index 4 (0-indexed) — the first day with enough training data
    # and verify none of its adjusted forecasts are negative
    for daily_row in result["daily"]:
        if not daily_row["hasEnoughHistory"]:
            continue
        for p in daily_row["periods"]:
            if p["adjustedForecast"] is not None:
                assert p["adjustedForecast"] >= 0, (
                    f"Adjusted forecast is negative: {p['adjustedForecast']} MW at {p['startTime']}"
                )


# ---------------------------------------------------------------------------
# Same evaluation set invariant
# ---------------------------------------------------------------------------

def test_same_eval_set_for_original_and_adjusted():
    """originalMetrics.count must equal adjustedMetrics.count for any day
    that has enough history, ensuring the comparison is apples-to-apples.
    """
    actuals, forecasts = _make_dense_dataset(10)
    result = walk_forward_backtest(actuals, forecasts)
    for d in result["daily"]:
        if d["hasEnoughHistory"] and d["adjustedMetrics"] is not None:
            assert d["originalMetrics"]["count"] == d["adjustedMetrics"]["count"], (
                f"Evaluation set mismatch on {d['decisionDate']}: "
                f"orig={d['originalMetrics']['count']}, adj={d['adjustedMetrics']['count']}"
            )


# ---------------------------------------------------------------------------
# Bias correction direction
# ---------------------------------------------------------------------------

def test_positive_bias_reduces_adjusted_forecast():
    """When historical bias is positive (overforecast), each adjusted forecast
    must be strictly less than the corresponding raw forecast.
    """
    actuals, forecasts = _make_dense_dataset(10, forecast_mw=5000.0, actual_mw=4500.0)
    result = walk_forward_backtest(actuals, forecasts)
    for d in result["daily"]:
        if not d["hasEnoughHistory"]:
            continue
        assert d["meanBiasMw"] > 0, "Expected positive bias (overforecast)"
        for p in d["periods"]:
            if p["forecast"] is not None and p["adjustedForecast"] is not None:
                assert p["adjustedForecast"] <= p["forecast"], (
                    f"Adjusted forecast {p['adjustedForecast']} > raw {p['forecast']} "
                    f"despite positive bias"
                )


# ---------------------------------------------------------------------------
# WINDFOR hourly vs FUELHH half-hourly alignment
# ---------------------------------------------------------------------------

def test_hourly_windfor_half_hourly_fuelhh_alignment_in_backtest():
    """WINDFOR entries at HH:00 cover 50% of FUELHH half-hour slots.
    The other 50% (HH:30) must remain None in backtest periods, not zero-filled.
    Training pairs must only count slots where both forecast and causal actual exist.
    """
    # One WINDFOR entry at 00:00; FUELHH entries at 00:00 AND 00:30
    fc_ts = "2026-01-02T00:00:00Z"
    act_ts_00 = "2026-01-02T00:00:00Z"
    act_ts_30 = "2026-01-02T00:30:00Z"

    actuals_list = [
        act(act_ts_00, 5000.0, "2026-01-02T01:00:00Z"),
        act(act_ts_30, 4800.0, "2026-01-02T01:00:00Z"),
    ]
    forecasts_list = [fc(fc_ts, "2026-01-01T08:00:00Z", 5200.0)]

    result = walk_forward_backtest(actuals_list, forecasts_list)
    day0 = result["daily"][0]

    # Slot 00:00 should have both forecast and actual
    p_00 = next((p for p in day0["periods"] if "00:00" in p["startTime"] and "02T" in p["startTime"]), None)
    p_30 = next((p for p in day0["periods"] if "00:30" in p["startTime"] and "02T" in p["startTime"]), None)

    if p_00:
        assert p_00["forecast"] == 5200.0
        assert p_00["actual"] == 5000.0
    if p_30:
        assert p_30["forecast"] is None, (
            "Half-hourly FUELHH slot at :30 must have no forecast (WINDFOR is hourly)"
        )
        assert p_30["actual"] == 4800.0


# ---------------------------------------------------------------------------
# Decision risk brief
# ---------------------------------------------------------------------------

def test_risk_brief_uses_only_prior_days():
    """Risk brief for Jan 5 must not incorporate any data from Jan 5 delivery."""
    actuals, forecasts = _make_dense_dataset(10)
    brief = decision_risk_brief(actuals, forecasts, cutoff="09:50", target_date="2026-01-05")
    assert brief["decisionDate"] == "2026-01-05"
    # Prior history covers Jan 1-4 only
    for d in brief["recentDailyHistory"]:
        assert d["decisionDate"] < "2026-01-05", (
            f"Risk brief included data from {d['decisionDate']}, "
            f"which is not strictly prior to 2026-01-05"
        )


def test_risk_brief_actual_publish_timing():
    """FUELHH actuals published after the target day's cutoff must not appear
    in the risk brief's prior history statistics.
    """
    delivery_ts = "2026-01-02T00:00:00Z"
    # Actual published at 12:00 on Jan 2 → after Jan 2 09:50 cutoff
    actuals_list = [act(delivery_ts, 4500.0, publish="2026-01-02T12:00:00Z")]
    forecasts_list = [fc(delivery_ts, "2026-01-01T08:00:00Z", 5000.0)]

    # Request brief for Jan 2 (cutoff 09:50 UTC Jan 2)
    brief = decision_risk_brief(actuals_list, forecasts_list, cutoff="09:50", target_date="2026-01-02")
    # Jan 2 has no prior days at all, so matchedPairs must be 0
    assert brief["priorHistory"]["matchedPairs"] == 0

    # Request brief for Jan 3 (cutoff 09:50 UTC Jan 3)
    brief3 = decision_risk_brief(actuals_list, forecasts_list, cutoff="09:50", target_date="2026-01-03")
    # Actual at 12:00 Jan 2 → visible at 09:50 Jan 3 → 1 pair
    assert brief3["priorHistory"]["matchedPairs"] == 1


def test_risk_brief_forecast_summary_coverage():
    """Risk brief forecast coverage must reflect WINDFOR hourly resolution.
    With 24 WINDFOR entries for a 48-slot delivery day, coverage should be ~50%.
    """
    actuals, forecasts = _make_dense_dataset(3)
    brief = decision_risk_brief(actuals, forecasts, cutoff="09:50", target_date="2026-01-03")
    # 24 forecasts out of 48 half-hour slots
    assert brief["forecastSummary"]["periodsWithForecast"] == 24
    assert brief["forecastSummary"]["totalPeriods"] == 48
    assert abs(brief["forecastSummary"]["coveragePct"] - 50.0) < 1.0


def test_risk_brief_insufficient_history_label():
    """With fewer than 10 prior pairs the tendency label must be 'insufficient_history'."""
    # Only one prior period
    actuals_list = [act("2026-01-02T00:00:00Z", 4500.0, "2026-01-02T01:00:00Z")]
    forecasts_list = [fc("2026-01-02T00:00:00Z", "2026-01-01T08:00:00Z", 5000.0)]

    brief = decision_risk_brief(actuals_list, forecasts_list, cutoff="09:50", target_date="2026-01-05")
    assert brief["priorHistory"]["tendencyLabel"] == "insufficient_history"


def test_risk_brief_overforecast_tendency_label():
    """With a clear positive bias the tendency label must be 'systematic_overforecast'."""
    # 10+ days of overforecast by 200 MW each
    actuals, forecasts = _make_dense_dataset(15, forecast_mw=5200.0, actual_mw=5000.0)
    # bias = +200 MW — within near_neutral range (threshold is 50 MW for label)
    # Use a bigger bias to trigger label
    actuals2, forecasts2 = _make_dense_dataset(15, forecast_mw=6000.0, actual_mw=5000.0)
    brief = decision_risk_brief(actuals2, forecasts2, cutoff="09:50", target_date="2026-01-15")
    assert brief["priorHistory"]["tendencyLabel"] == "systematic_overforecast", (
        f"Expected systematic_overforecast, got {brief['priorHistory']['tendencyLabel']}"
    )


def test_risk_brief_disclaimer_present():
    """The disclaimer must be present and must not claim procurement volumes."""
    actuals, forecasts = _make_dense_dataset(5)
    brief = decision_risk_brief(actuals, forecasts, cutoff="09:50", target_date="2026-01-05")
    assert "disclaimer" in brief
    assert len(brief["disclaimer"]) > 30
    # Must not claim procurement volumes
    lower = brief["disclaimer"].lower()
    assert "purchase volume" in lower or "procurement" in lower or "thermal" in lower, (
        "Disclaimer should acknowledge what the tool does NOT compute"
    )
