from datetime import date
from app.domain import analyze, decision_cutoff, metrics, normalize, quantile
from datetime import datetime, timezone


def actual(target, generation):
    return {"startTime": target, "generation": generation, "fuelType": "WIND"}


def forecast(target, published, generation):
    return {"startTime": target, "publishTime": published, "generation": generation}

TARGET = "2026-01-02T00:00:00Z"


def test_latest_available_as_of_cutoff_prevents_leakage():
    output = analyze([actual(TARGET, 100)], [
        forecast(TARGET, "2026-01-01T08:00:00Z", 110),
        forecast(TARGET, "2026-01-01T09:30:00Z", 120),
        forecast(TARGET, "2026-01-01T10:00:00Z", 999),
    ])
    row = output["periods"][0]
    assert row["forecast"] == 120
    assert row["error"] == 20
    assert row["publishTime"].startswith("2026-01-01T09:30")


def test_cutoff_can_change_selected_revision():
    a = [actual(TARGET, 100)]
    f = [forecast(TARGET, "2026-01-01T09:30:00Z", 120), forecast(TARGET, "2026-01-01T12:00:00Z", 140)]
    assert analyze(a, f, "09:50")["periods"][0]["forecast"] == 120
    assert analyze(a, f, "12:30")["periods"][0]["forecast"] == 140


def test_december_publication_does_not_get_mislabeled_as_january_decision():
    data = analyze([actual(TARGET, 100)], [forecast(TARGET, "2025-12-31T22:00:00Z", 103)])
    assert data["periods"][0]["forecast"] == 103  # Already known at Jan 1 decision.
    assert data["daily"][0]["decisionDate"] == "2026-01-01"


def test_includes_february_first_delivery():
    data = analyze([actual("2026-02-01T00:00:00Z", 100)], [forecast("2026-02-01T00:00:00Z", "2026-01-31T09:00:00Z", 110)])
    assert data["daily"][-1]["deliveryDate"] == "2026-02-01"
    assert data["daily"][-1]["count"] == 1
    assert data["metrics"]["expectedPeriods"] == 1488


def test_no_forecast_or_actual_does_not_become_zero():
    data = analyze([actual(TARGET, 90)], [])
    assert data["periods"][0]["actual"] == 90
    assert data["periods"][0]["forecast"] is None
    assert data["metrics"]["maeMw"] is None
    assert data["metrics"]["coveragePct"] == 0


def test_normalize_filters_fuel_and_invalid_measurements():
    out = normalize([actual(TARGET, 30), {**actual(TARGET, 100), "fuelType": "CCGT"}, actual(TARGET, -1), actual(TARGET, "nan")], actual=True)
    assert len(out) == 1
    assert out[0]["generation"] == 30


def test_metrics_sign_and_p90():
    rows = [{"forecast": 120, "actual": 100}, {"forecast": 50, "actual": 100}]
    result = metrics(rows)
    assert result["maeMw"] == 35
    assert result["biasMw"] == -15
    assert result["p90AbsoluteErrorMw"] == 50
    assert result["overforecastRatePct"] == 50


def test_day_ahead_time_zone_cutoff():
    assert decision_cutoff(date(2026, 1, 1), "09:50").hour == 9
    assert decision_cutoff(date(2026, 7, 1), "09:50").hour == 8


def test_actual_revision_and_prior_known_error_use_actual_publish_time():
    early = '2026-01-02T00:00:00Z'
    next_target = '2026-01-03T00:00:00Z'
    rows = [
        {'startTime':early,'fuelType':'WIND','publishTime':'2026-01-02T01:00:00Z','generation':100},
        {'startTime':early,'fuelType':'WIND','publishTime':'2026-01-02T02:00:00Z','generation':90},
        {'startTime':next_target,'fuelType':'WIND','publishTime':'2026-01-03T01:00:00Z','generation':120},
    ]
    forecasts = [forecast(early, '2026-01-01T08:30:00Z', 110),
                 forecast(next_target,'2026-01-02T08:30:00Z',125)]
    result = analyze(rows, forecasts)
    assert result['periods'][0]['actual'] == 90
    assert result['daily'][0]['priorKnownMaeMw'] is None
    assert result['daily'][1]['priorKnownMaeMw'] == 20
    assert result['daily'][1]['priorKnownMatchedPeriods'] == 1
    assert result['metrics']['matchedForecastMWh'] == 117.5
    assert result['metrics']['matchedActualMWh'] == 105
    assert result['metrics']['netEnergyErrorMWh'] == 12.5


def test_prior_error_not_visible_if_actual_published_after_next_cutoff():
    early = '2026-01-02T00:00:00Z'
    rows = [{'startTime':early,'fuelType':'WIND','publishTime':'2026-01-03T12:00:00Z','generation':100}]
    result = analyze(rows,[forecast(early,'2026-01-01T08:00:00Z',120)])
    assert result['daily'][1]['priorKnownMaeMw'] is None


def test_observations_do_not_fill_in_missing_half_hours():
    result = analyze([actual(TARGET, 100)],[forecast(TARGET,'2026-01-01T08:30:00Z',110)])
    assert result['metrics']['expectedPeriods'] == 1488
    assert result['metrics']['count'] == 1
    assert result['daily'][0]['matched'] == 1
    assert result['metrics']['matchedCoveragePct'] == round(100/1488,1)
    assert result['periods'][1]['forecast'] is None
    assert result['periods'][1]['actual'] is None


def test_prior_estimate_uses_as_of_outturn_revision_not_later_correction():
    actual_rows=[
        {'fuelType':'WIND','startTime':TARGET,'publishTime':'2026-01-02T01:00:00Z','generation':100},
        {'fuelType':'WIND','startTime':TARGET,'publishTime':'2026-01-04T03:00:00Z','generation':150},
    ]
    data = analyze(actual_rows,[forecast(TARGET,'2026-01-01T08:00:00Z',120)])
    assert data['daily'][0]['maeMw'] == 30  # retrospective revision 150
    assert data['daily'][1]['priorKnownMaeMw'] == 20  # Jan 2 revision 100 known Jan 3
