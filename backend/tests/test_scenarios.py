"""Comprehensive tests for Decision Scenario Workbench, causality, and API contracts."""
from __future__ import annotations

import json
from datetime import datetime, timezone
import pytest
from fastapi.testclient import TestClient

from app import main
from app.domain import (
    compute_dataset_hash,
    decision_scenario_workbench,
    decision_cutoff,
    MIN_BACKTEST_PAIRS,
)

client = TestClient(main.app)


def test_content_hash_deterministic():
    actual = [
        {"fuelType": "WIND", "startTime": "2026-01-02T00:00:00Z", "publishTime": "2026-01-02T01:00:00Z", "generation": 100.0},
        {"fuelType": "WIND", "startTime": "2026-01-02T01:00:00Z", "publishTime": "2026-01-02T02:00:00Z", "generation": 120.0},
    ]
    forecast = [
        {"startTime": "2026-01-02T00:00:00Z", "publishTime": "2026-01-01T09:00:00Z", "generation": 110.0},
        {"startTime": "2026-01-02T01:00:00Z", "publishTime": "2026-01-01T09:00:00Z", "generation": 125.0},
    ]
    h1 = compute_dataset_hash(actual, forecast)
    h2 = compute_dataset_hash(actual, forecast)
    assert len(h1) == 64
    assert h1 == h2

    # Different actual generation changes hash
    actual_modified = [
        {"fuelType": "WIND", "startTime": "2026-01-02T00:00:00Z", "publishTime": "2026-01-02T01:00:00Z", "generation": 105.0},
        {"fuelType": "WIND", "startTime": "2026-01-02T01:00:00Z", "publishTime": "2026-01-02T02:00:00Z", "generation": 120.0},
    ]
    assert compute_dataset_hash(actual_modified, forecast) != h1


def test_forecast_eligibility_boundaries():
    """Verify that forecasts published <= cutoff are eligible, and cutoff + 1s is rejected."""
    # Decision date: 2026-01-15, cutoff 09:50
    # Delivery date: 2026-01-16
    delivery_time = "2026-01-16T12:00:00Z"
    cutoff_time = "2026-01-15T09:50:00Z"
    after_cutoff = "2026-01-15T09:50:01Z"

    # 1. Forecast published at exact cutoff
    actual = [{"fuelType": "WIND", "startTime": delivery_time, "publishTime": "2026-01-16T13:00:00Z", "generation": 500.0}]
    forecast_exact = [{"startTime": delivery_time, "publishTime": cutoff_time, "generation": 600.0}]

    res_exact = decision_scenario_workbench(actual, forecast_exact, cutoff="09:50", target_date="2026-01-15")
    p_exact = next(p for p in res_exact["periods"] if p["startTime"].startswith("2026-01-16T12:00:00"))
    assert p_exact["rawForecast"] == 600.0

    # Forecast published strictly before the cutoff is eligible.
    forecast_before = [{"startTime": delivery_time, "publishTime": "2026-01-15T09:49:59Z", "generation": 610.0}]
    res_before = decision_scenario_workbench(actual, forecast_before, cutoff="09:50", target_date="2026-01-15")
    p_before = next(p for p in res_before["periods"] if p["startTime"].startswith("2026-01-16T12:00:00"))
    assert p_before["rawForecast"] == 610.0

    # 2. Forecast published 1s after cutoff must NOT be selected
    forecast_late = [{"startTime": delivery_time, "publishTime": after_cutoff, "generation": 700.0}]
    res_late = decision_scenario_workbench(actual, forecast_late, cutoff="09:50", target_date="2026-01-15")
    p_late = next(p for p in res_late["periods"] if p["startTime"].startswith("2026-01-16T12:00:00"))
    assert p_late["rawForecast"] is None


def test_actual_publication_causality_exclusion():
    """Actual generation published AFTER decision cutoff must NOT be included in prior training."""
    # Decision date: 2026-01-05 at 09:50 UK
    # Prior delivery day: 2026-01-04.
    # If actual was published at 2026-01-05T09:51:00Z (after cutoff), it must NOT be used for bias estimation on Jan 5.
    actual = [
        # Eligible actual published before cutoff
        {"fuelType": "WIND", "startTime": "2026-01-03T00:00:00Z", "publishTime": "2026-01-03T01:00:00Z", "generation": 100.0},
        # Ineligible actual published after cutoff
        {"fuelType": "WIND", "startTime": "2026-01-04T12:00:00Z", "publishTime": "2026-01-05T09:55:00Z", "generation": 200.0},
    ]
    forecast = [
        {"startTime": "2026-01-03T00:00:00Z", "publishTime": "2026-01-02T09:00:00Z", "generation": 150.0},
        {"startTime": "2026-01-04T12:00:00Z", "publishTime": "2026-01-03T09:00:00Z", "generation": 250.0},
        # Target day delivery forecast
        {"startTime": "2026-01-06T00:00:00Z", "publishTime": "2026-01-05T09:00:00Z", "generation": 300.0},
    ]
    res = decision_scenario_workbench(actual, forecast, cutoff="09:50", target_date="2026-01-05")
    exp_strat = res["exAnte"]["strategies"]["expanding"]
    # Only the first observation was published before 2026-01-05T09:50:00Z
    assert exp_strat["trainingPairs"] == 1


def test_sample_size_threshold_boundary():
    """Verify that fewer than 72 pairs returns insufficient_history, and exactly 72 is ready."""
    # 0 pairs: Day 1 (Jan 1)
    res_zero = decision_scenario_workbench([], [], cutoff="09:50", target_date="2026-01-01")
    assert res_zero["exAnte"]["strategies"]["expanding"]["status"] == "insufficient_history"
    assert res_zero["exAnte"]["strategies"]["expanding"]["trainingPairs"] == 0

    # Create 71 prior matched pairs across prior days
    actual_71 = []
    forecast_71 = []
    for i in range(71):
        day = 2 + (i // 24)
        hour = i % 24
        st = f"2026-01-{day:02d}T{hour:02d}:00:00Z"
        actual_71.append({"fuelType": "WIND", "startTime": st, "publishTime": f"2026-01-{day:02d}T{hour:02d}:30:00Z", "generation": 100.0})
        forecast_71.append({"startTime": st, "publishTime": f"2026-01-{day-1:02d}T09:00:00Z", "generation": 120.0})

    # Add target delivery day forecast (Jan 10 decision for Jan 11 delivery)
    forecast_71.append({"startTime": "2026-01-11T12:00:00Z", "publishTime": "2026-01-10T09:00:00Z", "generation": 500.0})

    # 1. With 71 pairs: status must be insufficient_history
    res_71 = decision_scenario_workbench(actual_71, forecast_71, cutoff="09:50", target_date="2026-01-10")
    exp_71 = res_71["exAnte"]["strategies"]["expanding"]
    assert exp_71["trainingPairs"] == 71
    assert exp_71["status"] == "insufficient_history"
    assert exp_71["hasEnoughHistory"] is False
    assert exp_71["adjustmentAppliedMw"] is None

    # In period output, expandingForecast must be None when insufficient
    p_71 = next(p for p in res_71["periods"] if p["startTime"].startswith("2026-01-11T12:00:00"))
    assert p_71["rawForecast"] == 500.0
    assert p_71["expandingForecast"] is None

    # 2. Add the 72nd pair
    st_72 = "2026-01-05T00:00:00Z"
    actual_72 = list(actual_71) + [{"fuelType": "WIND", "startTime": st_72, "publishTime": "2026-01-05T00:30:00Z", "generation": 100.0}]
    forecast_72 = list(forecast_71) + [{"startTime": st_72, "publishTime": "2026-01-04T09:00:00Z", "generation": 120.0}]

    res_72 = decision_scenario_workbench(actual_72, forecast_72, cutoff="09:50", target_date="2026-01-10")
    exp_72 = res_72["exAnte"]["strategies"]["expanding"]
    assert exp_72["trainingPairs"] == 72
    assert exp_72["status"] == "ready"
    assert exp_72["hasEnoughHistory"] is True
    # Bias is forecast - actual = 120 - 100 = 20.0 MW
    assert exp_72["meanBiasMw"] == 20.0
    assert exp_72["adjustmentAppliedMw"] == 20.0

    # In period output, adjusted forecast is raw - bias = 500 - 20 = 480.0 MW
    p_72 = next(p for p in res_72["periods"] if p["startTime"].startswith("2026-01-11T12:00:00"))
    assert p_72["expandingForecast"] == 480.0


def test_nonnegative_clamping():
    """Adjusted forecast must never be negative even if historical overprediction bias exceeds raw forecast."""
    # Bias = +500 MW overprediction
    actual = [{"fuelType": "WIND", "startTime": f"2026-01-02T{h:02d}:00:00Z", "publishTime": "2026-01-02T23:59:00Z", "generation": 100.0} for h in range(24)]
    actual += [{"fuelType": "WIND", "startTime": f"2026-01-03T{h:02d}:00:00Z", "publishTime": "2026-01-03T23:59:00Z", "generation": 100.0} for h in range(24)]
    actual += [{"fuelType": "WIND", "startTime": f"2026-01-04T{h:02d}:00:00Z", "publishTime": "2026-01-04T23:59:00Z", "generation": 100.0} for h in range(24)]

    forecast = [{"startTime": f"2026-01-02T{h:02d}:00:00Z", "publishTime": "2026-01-01T09:00:00Z", "generation": 600.0} for h in range(24)]
    forecast += [{"startTime": f"2026-01-03T{h:02d}:00:00Z", "publishTime": "2026-01-02T09:00:00Z", "generation": 600.0} for h in range(24)]
    forecast += [{"startTime": f"2026-01-04T{h:02d}:00:00Z", "publishTime": "2026-01-03T09:00:00Z", "generation": 600.0} for h in range(24)]

    # Target decision date: 2026-01-05 for delivery 2026-01-06.
    # Raw forecast is small: 150 MW. Bias is 500 MW.
    # Raw - Bias = 150 - 500 = -350 -> clamped to 0.0 MW.
    forecast.append({"startTime": "2026-01-06T12:00:00Z", "publishTime": "2026-01-05T09:00:00Z", "generation": 150.0})

    res = decision_scenario_workbench(actual, forecast, cutoff="09:50", target_date="2026-01-05")
    p = next(p for p in res["periods"] if p["startTime"].startswith("2026-01-06T12:00:00"))
    assert p["rawForecast"] == 150.0
    assert p["expandingForecast"] == 0.0


def test_negative_historical_bias_adjustment():
    """Negative historical bias (underforecasting) adjusts forecast upwards."""
    # Bias = -50 MW underprediction (forecast 100, actual 150)
    actual = [{"fuelType": "WIND", "startTime": f"2026-01-02T{h:02d}:00:00Z", "publishTime": "2026-01-02T23:59:00Z", "generation": 150.0} for h in range(24)]
    actual += [{"fuelType": "WIND", "startTime": f"2026-01-03T{h:02d}:00:00Z", "publishTime": "2026-01-03T23:59:00Z", "generation": 150.0} for h in range(24)]
    actual += [{"fuelType": "WIND", "startTime": f"2026-01-04T{h:02d}:00:00Z", "publishTime": "2026-01-04T23:59:00Z", "generation": 150.0} for h in range(24)]

    forecast = [{"startTime": f"2026-01-02T{h:02d}:00:00Z", "publishTime": "2026-01-01T09:00:00Z", "generation": 100.0} for h in range(24)]
    forecast += [{"startTime": f"2026-01-03T{h:02d}:00:00Z", "publishTime": "2026-01-02T09:00:00Z", "generation": 100.0} for h in range(24)]
    forecast += [{"startTime": f"2026-01-04T{h:02d}:00:00Z", "publishTime": "2026-01-03T09:00:00Z", "generation": 100.0} for h in range(24)]

    # Target decision date: 2026-01-05 for delivery 2026-01-06
    forecast.append({"startTime": "2026-01-06T12:00:00Z", "publishTime": "2026-01-05T09:00:00Z", "generation": 200.0})

    res = decision_scenario_workbench(actual, forecast, cutoff="09:50", target_date="2026-01-05")
    p = next(p for p in res["periods"] if p["startTime"].startswith("2026-01-06T12:00:00"))
    assert p["rawForecast"] == 200.0
    # Adjusted = raw - (-50) = 250.0 MW
    assert p["expandingForecast"] == 250.0


def test_rolling_seven_day_window_boundary():
    """Verify that only the previous 7 decision dates [D-7, D-1] contribute to the rolling window."""
    actual = []
    forecast = []

    # Jan 4 decision (Jan 5 delivery): 24 pairs with bias +100
    for h in range(24):
        st = f"2026-01-05T{h:02d}:00:00Z"
        actual.append({"fuelType": "WIND", "startTime": st, "publishTime": "2026-01-05T23:00:00Z", "generation": 200.0})
        forecast.append({"startTime": st, "publishTime": "2026-01-04T09:00:00Z", "generation": 300.0})

    # Jan 5 to Jan 8 decisions (Jan 6 to Jan 9 delivery): 4 days * 24 = 96 pairs with bias 0 (actual=200, forecast=200)
    for day in range(6, 10):
        for h in range(24):
            st = f"2026-01-{day:02d}T{h:02d}:00:00Z"
            actual.append({"fuelType": "WIND", "startTime": st, "publishTime": f"2026-01-{day:02d}T23:00:00Z", "generation": 200.0})
            forecast.append({"startTime": st, "publishTime": f"2026-01-{day-1:02d}T09:00:00Z", "generation": 200.0})

    # Target decision date: 2026-01-12 (delivery 2026-01-13)
    forecast.append({"startTime": "2026-01-13T12:00:00Z", "publishTime": "2026-01-12T09:00:00Z", "generation": 400.0})

    res = decision_scenario_workbench(actual, forecast, cutoff="09:50", target_date="2026-01-12")
    exp_strat = res["exAnte"]["strategies"]["expanding"]
    roll_strat = res["exAnte"]["strategies"]["rolling7Day"]

    # Expanding includes Jan 4 (24 pairs) + Jan 5-8 (96 pairs) = 120 pairs
    assert exp_strat["trainingPairs"] == 120
    # Rolling excludes Jan 4, so it has only the 96 pairs from Jan 5-8
    assert roll_strat["trainingPairs"] == 96
    # Rolling bias = 0.0 MW, while Expanding bias includes the +100 errors from Jan 4
    assert roll_strat["meanBiasMw"] == 0.0
    assert exp_strat["meanBiasMw"] > 0.0


def test_like_for_like_evaluation_sample():
    """Retrospective evaluation must evaluate original and adjustments on identical matched timestamps."""
    # 72 pairs training on Jan 2-4
    actual = [{"fuelType": "WIND", "startTime": f"2026-01-02T{h:02d}:00:00Z", "publishTime": "2026-01-02T23:59:00Z", "generation": 100.0} for h in range(24)]
    actual += [{"fuelType": "WIND", "startTime": f"2026-01-03T{h:02d}:00:00Z", "publishTime": "2026-01-03T23:59:00Z", "generation": 100.0} for h in range(24)]
    actual += [{"fuelType": "WIND", "startTime": f"2026-01-04T{h:02d}:00:00Z", "publishTime": "2026-01-04T23:59:00Z", "generation": 100.0} for h in range(24)]

    forecast = [{"startTime": f"2026-01-02T{h:02d}:00:00Z", "publishTime": "2026-01-01T09:00:00Z", "generation": 120.0} for h in range(24)]
    forecast += [{"startTime": f"2026-01-03T{h:02d}:00:00Z", "publishTime": "2026-01-02T09:00:00Z", "generation": 120.0} for h in range(24)]
    forecast += [{"startTime": f"2026-01-04T{h:02d}:00:00Z", "publishTime": "2026-01-03T09:00:00Z", "generation": 120.0} for h in range(24)]

    # Target delivery day Jan 6 (decision Jan 5)
    # Suppose 10 periods have actuals, but only 8 have forecasts
    for h in range(10):
        actual.append({"fuelType": "WIND", "startTime": f"2026-01-06T{h:02d}:00:00Z", "publishTime": "2026-01-06T23:00:00Z", "generation": 300.0})
    for h in range(8):
        forecast.append({"startTime": f"2026-01-06T{h:02d}:00:00Z", "publishTime": "2026-01-05T09:00:00Z", "generation": 350.0})

    res = decision_scenario_workbench(actual, forecast, cutoff="09:50", target_date="2026-01-05")
    retro = res["retrospective"]
    assert retro["matchedPeriodCount"] == 8
    assert retro["metrics"]["original"]["count"] == 8
    assert retro["metrics"]["expanding"]["count"] == 8


def test_january_to_february_transition():
    """Decision on Jan 31 evaluates delivery on Feb 1."""
    actual = [{"fuelType": "WIND", "startTime": "2026-02-01T12:00:00Z", "publishTime": "2026-02-01T14:00:00Z", "generation": 500.0}]
    forecast = [{"startTime": "2026-02-01T12:00:00Z", "publishTime": "2026-01-31T09:00:00Z", "generation": 550.0}]

    res = decision_scenario_workbench(actual, forecast, cutoff="09:50", target_date="2026-01-31")
    assert res["identity"]["decisionDate"] == "2026-01-31"
    assert res["identity"]["deliveryDate"] == "2026-02-01"
    p = next(p for p in res["periods"] if p["startTime"].startswith("2026-02-01T12:00:00"))
    assert p["rawForecast"] == 550.0
    assert p["actual"] == 500.0


def test_correction_makes_accuracy_worse():
    """When wind regime unexpectedly surges, subtracting historical overprediction bias increases error."""
    # Historical training: 72 pairs with forecast 600, actual 500 -> bias = +100 MW
    actual = [{"fuelType": "WIND", "startTime": f"2026-01-02T{h:02d}:00:00Z", "publishTime": "2026-01-02T23:59:00Z", "generation": 500.0} for h in range(24)]
    actual += [{"fuelType": "WIND", "startTime": f"2026-01-03T{h:02d}:00:00Z", "publishTime": "2026-01-03T23:59:00Z", "generation": 500.0} for h in range(24)]
    actual += [{"fuelType": "WIND", "startTime": f"2026-01-04T{h:02d}:00:00Z", "publishTime": "2026-01-04T23:59:00Z", "generation": 500.0} for h in range(24)]

    forecast = [{"startTime": f"2026-01-02T{h:02d}:00:00Z", "publishTime": "2026-01-01T09:00:00Z", "generation": 600.0} for h in range(24)]
    forecast += [{"startTime": f"2026-01-03T{h:02d}:00:00Z", "publishTime": "2026-01-02T09:00:00Z", "generation": 600.0} for h in range(24)]
    forecast += [{"startTime": f"2026-01-04T{h:02d}:00:00Z", "publishTime": "2026-01-03T09:00:00Z", "generation": 600.0} for h in range(24)]

    # Target delivery day (Jan 6, decision Jan 5):
    # Raw forecast = 600 MW, but actual wind surges to 700 MW.
    # Raw error = |600 - 700| = 100 MW.
    # Adjusted forecast = 600 - 100 = 500 MW -> adjusted error = |500 - 700| = 200 MW (WORSE!).
    for h in range(24):
        actual.append({"fuelType": "WIND", "startTime": f"2026-01-06T{h:02d}:00:00Z", "publishTime": "2026-01-06T23:00:00Z", "generation": 700.0})
        forecast.append({"startTime": f"2026-01-06T{h:02d}:00:00Z", "publishTime": "2026-01-05T09:00:00Z", "generation": 600.0})

    res = decision_scenario_workbench(actual, forecast, cutoff="09:50", target_date="2026-01-05")
    retro = res["retrospective"]
    assert retro["metrics"]["original"]["maeMw"] == 100.0
    assert retro["metrics"]["expanding"]["maeMw"] == 200.0
    # Day comparison identifies original as superior
    assert retro["dayComparison"]["bestStrategy"] == "original"
    assert retro["dayComparison"]["expandingImprovement"]["worsenedPeriods"] == 24
    assert retro["dayComparison"]["expandingImprovement"]["improvedPeriods"] == 0


def test_api_scenarios_validation_and_errors(monkeypatch, tmp_path):
    monkeypatch.setattr(main, "CACHE_FILE", tmp_path / "empty.json")

    # 1. Cache not imported -> 503
    r = client.get("/api/scenarios?date=2026-01-15&cutoff=09:50")
    assert r.status_code == 503

    # 2. Invalid date -> 400
    r_bad_date = client.get("/api/scenarios?date=2026-02-15&cutoff=09:50")
    assert r_bad_date.status_code == 400
    assert "out of range" in r_bad_date.json()["detail"]

    # 3. Invalid cutoff -> 422
    r_bad_cutoff = client.get("/api/scenarios?date=2026-01-15&cutoff=99:99")
    assert r_bad_cutoff.status_code == 422


def test_api_scenarios_successful_response(monkeypatch, tmp_path):
    # Seed cache with real-format records
    cache_path = tmp_path / "cache.json"
    actual = [{"fuelType": "WIND", "startTime": f"2026-01-02T{h:02d}:00:00Z", "publishTime": "2026-01-02T23:59:00Z", "generation": 100.0} for h in range(24)]
    actual += [{"fuelType": "WIND", "startTime": f"2026-01-03T{h:02d}:00:00Z", "publishTime": "2026-01-03T23:59:00Z", "generation": 100.0} for h in range(24)]
    actual += [{"fuelType": "WIND", "startTime": f"2026-01-04T{h:02d}:00:00Z", "publishTime": "2026-01-04T23:59:00Z", "generation": 100.0} for h in range(24)]
    actual += [{"fuelType": "WIND", "startTime": f"2026-01-05T{h:02d}:00:00Z", "publishTime": "2026-01-05T23:59:00Z", "generation": 100.0} for h in range(24)]

    forecast = [{"startTime": f"2026-01-02T{h:02d}:00:00Z", "publishTime": "2026-01-01T09:00:00Z", "generation": 120.0} for h in range(24)]
    forecast += [{"startTime": f"2026-01-03T{h:02d}:00:00Z", "publishTime": "2026-01-02T09:00:00Z", "generation": 120.0} for h in range(24)]
    forecast += [{"startTime": f"2026-01-04T{h:02d}:00:00Z", "publishTime": "2026-01-03T09:00:00Z", "generation": 120.0} for h in range(24)]
    forecast += [{"startTime": f"2026-01-05T{h:02d}:00:00Z", "publishTime": "2026-01-04T09:00:00Z", "generation": 120.0} for h in range(24)]

    payload = {
        "schemaVersion": 2,
        "actual": actual,
        "forecast": forecast,
        "fetchedAt": datetime.now(timezone.utc).isoformat(),
        "sourceParams": main.SOURCES,
    }
    cache_path.write_text(json.dumps(payload))
    monkeypatch.setattr(main, "CACHE_FILE", cache_path)

    response = client.get("/api/scenarios?date=2026-01-04&cutoff=09:50")
    assert response.status_code == 200
    data = response.json()

    # Verify structured sections
    assert "identity" in data
    assert data["identity"]["decisionDate"] == "2026-01-04"
    assert data["identity"]["deliveryDate"] == "2026-01-05"
    assert "contentHash" in data["identity"]
    assert len(data["identity"]["contentHash"]) == 64

    # Verify ex-ante section
    assert "exAnte" in data
    assert "strategies" in data["exAnte"]
    assert "original" in data["exAnte"]["strategies"]
    assert "expanding" in data["exAnte"]["strategies"]
    assert "rolling7Day" in data["exAnte"]["strategies"]

    # Verify 48 periods exist
    assert len(data["periods"]) == 48

    # Verify retrospective section
    assert "retrospective" in data
    assert "metrics" in data["retrospective"]

    # Verify monthSummary and provenance
    assert "monthSummary" in data
    assert "provenance" in data
    assert data["provenance"]["contentHash"] == data["identity"]["contentHash"]
    assert "limitations" in data
    assert len(data["limitations"]) > 0

    # Verify no secrets in response
    json_str = json.dumps(data)
    for banned in ("/tmp/", "/app/data", "cache_file", "password", "api_key", "begin private"):
        assert banned not in json_str.lower()
    assert "KNOWN AT DECISION TIME" in data["report"]["markdown"]
    assert "LEARNED AFTER DELIVERY" in data["report"]["markdown"]
    assert data["report"]["provenance"]["contentHash"] == data["identity"]["contentHash"]
    assert "publishDateTimeFrom=2026-01-01" in data["provenance"]["windforUrl"]
    assert "settlementDateFrom=2026-01-02" in data["provenance"]["fuelhhUrl"]
    assert data["provenance"]["windforParams"]["publishDateTimeTo"] == "2026-02-01T00:00:00Z"
    assert data["reproducibility"]["algorithmVersion"]
    assert data["reproducibility"]["hourlyAlignmentSensitivity"].startswith("not_implemented")
    assert data["missingData"]["deliveryPeriods"] == 48


def test_missing_forecast_is_not_imputed():
    actual = [{"fuelType": "WIND", "startTime": f"2026-01-02T{h:02d}:00:00Z", "publishTime": "2026-01-02T23:00:00Z", "generation": 100.0} for h in range(24)]
    actual += [{"fuelType": "WIND", "startTime": f"2026-01-03T{h:02d}:00:00Z", "publishTime": "2026-01-03T23:00:00Z", "generation": 100.0} for h in range(24)]
    actual += [{"fuelType": "WIND", "startTime": f"2026-01-04T{h:02d}:00:00Z", "publishTime": "2026-01-04T23:00:00Z", "generation": 100.0} for h in range(24)]
    actual.append({"fuelType": "WIND", "startTime": "2026-01-06T12:30:00Z", "publishTime": "2026-01-06T14:00:00Z", "generation": 80.0})
    forecast = [{"startTime": f"2026-01-02T{h:02d}:00:00Z", "publishTime": "2026-01-01T09:00:00Z", "generation": 110.0} for h in range(24)]
    forecast += [{"startTime": f"2026-01-03T{h:02d}:00:00Z", "publishTime": "2026-01-02T09:00:00Z", "generation": 110.0} for h in range(24)]
    forecast += [{"startTime": f"2026-01-04T{h:02d}:00:00Z", "publishTime": "2026-01-03T09:00:00Z", "generation": 110.0} for h in range(24)]
    res = decision_scenario_workbench(actual, forecast, cutoff="09:50", target_date="2026-01-05")
    missing = next(p for p in res["periods"] if p["startTime"].startswith("2026-01-06T12:30:00"))
    assert missing["rawForecast"] is None
    assert missing["expandingForecast"] is None
    assert missing["rollingForecast"] is None
    assert missing["actual"] == 80.0
    assert res["exAnte"]["strategies"]["expanding"]["hasEnoughHistory"] is True


def test_expanding_bias_matches_walk_forward():
    from app.domain import walk_forward_backtest
    actual = [{"fuelType": "WIND", "startTime": f"2026-01-02T{h:02d}:00:00Z", "publishTime": "2026-01-02T23:59:00Z", "generation": 500.0} for h in range(24)]
    actual += [{"fuelType": "WIND", "startTime": f"2026-01-03T{h:02d}:00:00Z", "publishTime": "2026-01-03T23:59:00Z", "generation": 500.0} for h in range(24)]
    actual += [{"fuelType": "WIND", "startTime": f"2026-01-04T{h:02d}:00:00Z", "publishTime": "2026-01-04T23:59:00Z", "generation": 500.0} for h in range(24)]
    forecast = [{"startTime": f"2026-01-02T{h:02d}:00:00Z", "publishTime": "2026-01-01T09:00:00Z", "generation": 600.0} for h in range(24)]
    forecast += [{"startTime": f"2026-01-03T{h:02d}:00:00Z", "publishTime": "2026-01-02T09:00:00Z", "generation": 600.0} for h in range(24)]
    forecast += [{"startTime": f"2026-01-04T{h:02d}:00:00Z", "publishTime": "2026-01-03T09:00:00Z", "generation": 600.0} for h in range(24)]
    forecast.append({"startTime": "2026-01-06T12:00:00Z", "publishTime": "2026-01-05T09:00:00Z", "generation": 600.0})
    scenario = decision_scenario_workbench(actual, forecast, cutoff="09:50", target_date="2026-01-05")
    backtest = walk_forward_backtest(actual, forecast, cutoff="09:50")
    day = next(row for row in backtest["daily"] if row["decisionDate"] == "2026-01-05")
    expanding = scenario["exAnte"]["strategies"]["expanding"]
    assert expanding["trainingPairs"] == day["trainingPairs"]
    assert expanding["meanBiasMw"] == day["meanBiasMw"]
    assert expanding["hasEnoughHistory"] == day["hasEnoughHistory"]


def test_report_selection_and_source_params():
    from app.domain import WINDFOR_PARAMS, FUELHH_PARAMS, build_analysis_report
    from app.elexon import SOURCES
    assert WINDFOR_PARAMS == SOURCES["WINDFOR"]
    assert FUELHH_PARAMS == SOURCES["FUELHH"]
    res = decision_scenario_workbench([], [], cutoff="09:50", target_date="2026-01-01")
    selected = build_analysis_report(res, selected_strategy="expanding")
    assert selected["selectedStrategy"] == "expanding"
    assert selected["scenarioDefinition"]["selectedStrategyDetail"]["status"] == "insufficient_history"
    assert selected["scenarioDefinition"]["minTrainingPairs"] == MIN_BACKTEST_PAIRS
    with pytest.raises(ValueError):
        build_analysis_report(res, selected_strategy="optimal-procurement")


def test_api_rejects_unsupported_strategy(monkeypatch, tmp_path):
    monkeypatch.setattr(main, "CACHE_FILE", tmp_path / "empty.json")
    response = client.get("/api/scenarios?date=2026-01-15&cutoff=09:50&strategy=trade")
    assert response.status_code == 400
    assert "Unsupported strategy" in response.json()["detail"]


def test_scenario_logs_cache_miss(monkeypatch, tmp_path, caplog):
    import logging
    monkeypatch.setattr(main, "CACHE_FILE", tmp_path / "empty.json")
    with caplog.at_level(logging.INFO, logger="windward"):
        client.get("/api/scenarios?date=2026-01-15&cutoff=09:50")
    assert any("cache_miss" in record.message and "route=/api/scenarios" in record.message for record in caplog.records)


def test_latest_eligible_forecast_excludes_one_second_after_cutoff():
    delivery = "2026-01-16T12:00:00Z"
    actual = [{"fuelType": "WIND", "startTime": delivery, "publishTime": "2026-01-16T13:00:00Z", "generation": 10.0}]
    forecast = [
        {"startTime": delivery, "publishTime": "2026-01-15T08:00:00Z", "generation": 400.0},
        {"startTime": delivery, "publishTime": "2026-01-15T09:50:00Z", "generation": 450.0},
        {"startTime": delivery, "publishTime": "2026-01-15T09:50:01Z", "generation": 999.0},
    ]
    res = decision_scenario_workbench(actual, forecast, cutoff="09:50", target_date="2026-01-15")
    period = next(p for p in res["periods"] if p["startTime"].startswith("2026-01-16T12:00:00"))
    assert period["rawForecast"] == 450.0


def test_training_uses_latest_actual_published_by_cutoff():
    actual = [
        {"fuelType": "WIND", "startTime": "2026-01-02T00:00:00Z", "publishTime": "2026-01-02T12:00:00Z", "generation": 80.0},
        {"fuelType": "WIND", "startTime": "2026-01-02T00:00:00Z", "publishTime": "2026-01-03T12:00:00Z", "generation": 10.0},
    ]
    forecast = [{"startTime": "2026-01-02T00:00:00Z", "publishTime": "2026-01-01T09:00:00Z", "generation": 100.0}]
    res = decision_scenario_workbench(actual, forecast, cutoff="09:50", target_date="2026-01-03")
    observed = res["exAnte"]["strategies"]["expanding"]["trainingError"]
    assert observed["count"] == 1
    assert observed["meanSignedErrorMw"] == 20.0
    assert res["exAnte"]["strategies"]["expanding"]["adjustmentAppliedMw"] is None


def test_completely_missing_forecasts_are_not_manufactured():
    res = decision_scenario_workbench([], [], cutoff="09:50", target_date="2026-01-15")
    assert res["periods"]
    assert all(p["rawForecast"] is None and p["expandingForecast"] is None and p["rollingForecast"] is None for p in res["periods"])
    assert res["retrospective"]["matchedPeriodCount"] == 0
    assert res["missingData"]["periodsMissingForecast"] == res["missingData"]["deliveryPeriods"]
    assert res["exAnte"]["strategies"]["original"]["status"] == "ready"


def test_expanding_population_matches_walk_forward_scores():
    from app.domain import walk_forward_backtest
    actual, forecast = [], []
    for decision_day in range(1, 9):
        delivery = decision_day + 1
        for hour in range(24):
            start = f"2026-01-{delivery:02d}T{hour:02d}:00:00Z"
            actual.append({"fuelType": "WIND", "startTime": start, "publishTime": f"2026-01-{delivery:02d}T23:00:00Z", "generation": 100.0})
            forecast.append({"startTime": start, "publishTime": f"2026-01-{decision_day:02d}T09:00:00Z", "generation": 120.0})
    scenario = decision_scenario_workbench(actual, forecast, cutoff="09:50", target_date="2026-01-08")
    backtest = walk_forward_backtest(actual, forecast, cutoff="09:50")
    population = scenario["evaluationPopulations"]["expandingEligible"]
    assert population["eligibleDays"] == backtest["daysWithEnoughHistory"]
    assert population["matchedObservations"] == backtest["summaryOriginal"]["count"]
    assert population["original"]["maeMw"] == backtest["summaryOriginal"]["maeMw"]
    assert population["adjusted"]["maeMw"] == backtest["summaryAdjusted"]["maeMw"]
    assert population["original"]["count"] == population["adjusted"]["count"]
    common = scenario["evaluationPopulations"]["commonThreeWay"]["matchedObservations"]
    assert common <= population["matchedObservations"]


def test_report_hash_changes_when_source_changes_and_hides_secrets():
    from app.domain import build_analysis_report
    actual = [{"fuelType": "WIND", "startTime": "2026-01-02T00:00:00Z", "publishTime": "2026-01-02T01:00:00Z", "generation": 100.0}]
    forecast = [{"startTime": "2026-01-02T00:00:00Z", "publishTime": "2026-01-01T09:00:00Z", "generation": 110.0}]
    first = decision_scenario_workbench(actual, forecast, cutoff="09:50", target_date="2026-01-02")
    again = decision_scenario_workbench(actual, forecast, cutoff="09:50", target_date="2026-01-02")
    assert first["identity"]["contentHash"] == again["identity"]["contentHash"]
    changed = decision_scenario_workbench(
        [{"fuelType": "WIND", "startTime": "2026-01-02T00:00:00Z", "publishTime": "2026-01-02T01:00:00Z", "generation": 101.0}],
        forecast,
        cutoff="09:50",
        target_date="2026-01-02",
    )
    assert changed["identity"]["contentHash"] != first["identity"]["contentHash"]
    report = build_analysis_report(first, "original")
    blob = json.dumps(report)
    assert "LEARNED AFTER DELIVERY" in report["markdown"]
    assert "KNOWN AT DECISION TIME" in report["markdown"]
    assert report["selectedStrategy"] == "original"
    for banned in ("/tmp/", "/app/", "CACHE_FILE", "password", "api_key", "authorization"):
        assert banned not in blob


def test_api_rejects_unknown_query_and_calculation_failure(monkeypatch, tmp_path):
    monkeypatch.setattr(main, "CACHE_FILE", tmp_path / "empty.json")
    unknown = client.get("/api/scenarios?date=2026-01-15&cutoff=09:50&simulated=1")
    assert unknown.status_code == 400
    assert "Unsupported query parameter" in unknown.json()["detail"]

    cache_path = tmp_path / "cache.json"
    payload = {
        "schemaVersion": 2,
        "actual": [],
        "forecast": [],
        "fetchedAt": "2026-02-02T00:00:00+00:00",
        "sourceParams": main.SOURCES,
    }
    cache_path.write_text(json.dumps(payload))
    monkeypatch.setattr(main, "CACHE_FILE", cache_path)

    def explode(*_args, **_kwargs):
        raise RuntimeError("calculation failed")

    monkeypatch.setattr(main, "decision_scenario_workbench", explode)
    failed = client.get("/api/scenarios?date=2026-01-15&cutoff=09:50")
    assert failed.status_code == 500
    assert "fabricated" in failed.json()["detail"]
    assert "traceback" not in failed.text.lower()


def test_api_scenario_hash_is_stable_for_same_cache(monkeypatch, tmp_path):
    cache_path = tmp_path / "cache.json"
    payload = {
        "schemaVersion": 2,
        "actual": [{"fuelType": "WIND", "startTime": "2026-01-02T00:00:00Z", "publishTime": "2026-01-02T02:00:00Z", "generation": 100.0}],
        "forecast": [{"startTime": "2026-01-02T00:00:00Z", "publishTime": "2026-01-01T09:00:00Z", "generation": 110.0}],
        "fetchedAt": "2026-02-02T00:00:00+00:00",
        "sourceParams": main.SOURCES,
    }
    cache_path.write_text(json.dumps(payload))
    monkeypatch.setattr(main, "CACHE_FILE", cache_path)
    first = client.get("/api/scenarios?date=2026-01-02&cutoff=09:50&strategy=rolling7Day")
    second = client.get("/api/scenarios?date=2026-01-02&cutoff=09:50&strategy=rolling7Day")
    assert first.status_code == 200
    assert first.json()["identity"]["contentHash"] == second.json()["identity"]["contentHash"]
    assert first.json()["report"]["selectedStrategy"] == "rolling7Day"
    assert first.json()["requestedStrategy"] == "rolling7Day"
    assert "mock" not in json.dumps(first.json()).lower()
    assert first.json()["evaluationPopulations"]["expandingEligible"]["matchedObservations"] == first.json()["evaluationPopulations"]["expandingEligible"]["original"]["count"]

