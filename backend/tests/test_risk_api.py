"""Risk endpoint and its integration with the Phase 2 cleaning layer."""

from __future__ import annotations

import datetime as dt

import pandas as pd
import pytest
from sqlalchemy import select

from app.models import DailyPrice, Security
from app.core.limitations import VAR_LIMITATIONS
from app.services.returns import build_returns_matrix

BASE_PORTFOLIO = {
    "positions": [
        {"ticker": "RELIANCE", "weight": 0.4},
        {"ticker": "TCS", "weight": 0.3},
        {"ticker": "HDFCBANK", "weight": 0.3},
    ],
    "total_value_inr": 1_000_000,
}


def _post(client, **overrides):
    body = {"portfolio": BASE_PORTFOLIO, "n_sims": 2_000, "seed": 42}
    body.update(overrides)
    return client.post("/api/risk/var", json=body)


def _has_data(db, ticker: str) -> bool:
    security = db.scalar(select(Security).where(Security.ticker == ticker))
    return security is not None and bool(security.daily_prices)


# --- endpoint contract -----------------------------------------------------


def test_var_endpoint_returns_both_methods(client):
    response = _post(client)
    if response.status_code == 422:
        pytest.skip("database not ingested")
    assert response.status_code == 200

    payload = response.json()
    assert payload["parametric"]["method"] == "parametric"
    assert payload["historical_bootstrap"]["method"] == "historical_bootstrap"

    for method in ("parametric", "historical_bootstrap"):
        estimates = payload[method]["estimates"]
        assert [e["confidence_level"] for e in estimates] == [0.95, 0.99]
        for estimate in estimates:
            assert estimate["var_inr"] > 0
            assert estimate["cvar_inr"] > estimate["var_inr"]


def test_limitations_come_from_the_backend(client):
    response = _post(client)
    if response.status_code == 422:
        pytest.skip("database not ingested")

    limitations = response.json()["limitations"]
    assert limitations == VAR_LIMITATIONS
    assert len(limitations) == 2
    assert "normally distributed" in limitations[0]
    assert "correlation" in limitations[1].lower()


def test_response_reports_the_window_used(client):
    response = _post(client, lookback_days=504)
    if response.status_code == 422:
        pytest.skip("database not ingested")

    window = response.json()["data_window"]
    assert window["requested_lookback_days"] == 504
    assert window["trading_days"] <= 504
    assert window["shrunk"] is False
    assert set(window["first_available_date_by_ticker"]) == {
        "RELIANCE", "TCS", "HDFCBANK"
    }


def test_short_history_shrinks_window_and_names_the_constraint(client):
    """JIOFIN listed in Aug 2023, so a 2000-day request cannot be met."""
    response = _post(
        client,
        portfolio={
            "positions": [
                {"ticker": "RELIANCE", "weight": 0.5},
                {"ticker": "JIOFIN", "weight": 0.5},
            ],
            "total_value_inr": 1_000_000,
        },
        lookback_days=2000,
    )
    if response.status_code == 422:
        pytest.skip("database not ingested")
    assert response.status_code == 200

    window = response.json()["data_window"]
    assert window["shrunk"] is True
    assert window["constrained_by"] == "JIOFIN"
    assert window["trading_days"] < 2000
    assert "JIOFIN" in window["constraint_reason"]
    # The ticker is kept, not dropped.
    assert set(response.json()["weights"]) == {"RELIANCE", "JIOFIN"}


def test_same_seed_same_response(client):
    first = _post(client, seed=2026)
    if first.status_code == 422:
        pytest.skip("database not ingested")
    second = _post(client, seed=2026)

    assert first.json()["parametric"] == second.json()["parametric"]
    assert first.json()["historical_bootstrap"] == second.json()["historical_bootstrap"]


def test_bad_weights_rejected_with_a_clear_message(client):
    response = _post(
        client,
        portfolio={
            "positions": [
                {"ticker": "RELIANCE", "weight": 0.5},
                {"ticker": "TCS", "weight": 0.3},
            ],
            "total_value_inr": 1_000_000,
        },
    )
    assert response.status_code == 422
    assert "must sum to 1.0" in response.json()["message"]


def test_unknown_ticker_in_body_returns_422(client):
    response = _post(
        client,
        portfolio={
            "positions": [{"ticker": "NOTATICKER", "weight": 1.0}],
            "total_value_inr": 1_000_000,
        },
    )
    assert response.status_code == 422
    assert response.json()["error_code"] == "UNKNOWN_TICKER"


def test_confidence_levels_must_be_fractions(client):
    response = _post(client, confidence_levels=[95])
    assert response.status_code == 422


# --- the cleaning layer is actually in the path ----------------------------


def test_anomaly_dates_do_not_distort_the_covariance(ingested_db):
    """A TMPV portfolio must see 0.0 on 2025-10-14, not the raw -40.2%.

    This is the proof that get_daily_returns is in the path: computing returns
    straight from daily_prices over the same window gives a far larger standard
    deviation, because the demerger break is still in it.
    """
    if not _has_data(ingested_db, "TMPV"):
        pytest.skip("TMPV not ingested")

    frame, window = build_returns_matrix(
        ingested_db, ["TMPV", "RELIANCE"], lookback_days=504
    )
    anomaly = pd.Timestamp(2025, 10, 14)
    assert anomaly in frame.index, "window should span the demerger date"
    assert frame.loc[anomaly, "TMPV"] == 0.0

    # Same dates, computed from raw prices — the uncleaned comparison.
    security = ingested_db.scalar(select(Security).where(Security.ticker == "TMPV"))
    rows = ingested_db.execute(
        select(DailyPrice.date, DailyPrice.adj_close)
        .where(
            DailyPrice.security_id == security.id,
            DailyPrice.date >= window.start - dt.timedelta(days=5),
            DailyPrice.date <= window.end,
        )
        .order_by(DailyPrice.date)
    ).all()
    raw = pd.Series(
        [float(c) for _, c in rows],
        index=pd.DatetimeIndex([d for d, _ in rows]),
    ).pct_change().dropna()
    raw = raw.loc[frame.index[0]:frame.index[-1]]

    cleaned = frame["TMPV"]
    cleaned_std = float(cleaned.std())
    raw_std = float(raw.std())

    # The break is still in the raw data, and only in the raw data.
    assert raw.loc[anomaly] < -0.35
    assert cleaned.loc[anomaly] == 0.0

    # Outside the anomaly date the two series are identical -- so the entire
    # difference in dispersion is attributable to that one day.
    pd.testing.assert_series_equal(
        raw.drop(anomaly), cleaned.drop(anomaly),
        check_names=False, check_freq=False, rtol=1e-9,
    )

    # One outlier of size r in n observations adds about r^2/(n-1) to the
    # variance. That predicts the inflation; assert the measured gap matches,
    # rather than asserting an arbitrary ratio.
    n = len(raw)
    predicted_extra_variance = float(raw.loc[anomaly]) ** 2 / (n - 1)
    measured_extra_variance = raw_std**2 - cleaned_std**2
    assert measured_extra_variance == pytest.approx(
        predicted_extra_variance, rel=0.15
    ), (
        f"variance inflation {measured_extra_variance:.6f} does not match the "
        f"single-outlier prediction {predicted_extra_variance:.6f}"
    )
    assert raw_std > cleaned_std * 1.25


def test_trent_anomaly_also_neutralised_in_the_matrix(ingested_db):
    if not _has_data(ingested_db, "TRENT"):
        pytest.skip("TRENT not ingested")

    frame, _ = build_returns_matrix(
        ingested_db, ["TRENT", "RELIANCE"], lookback_days=504
    )
    anomaly = pd.Timestamp(2026, 1, 1)
    if anomaly not in frame.index:
        pytest.skip("window does not span the TRENT reset")
    assert frame.loc[anomaly, "TRENT"] == 0.0


def test_var_with_an_anomalous_ticker_is_not_inflated(client):
    """The 40% artefact must not leak into TMPV's VaR."""
    response = _post(
        client,
        portfolio={
            "positions": [{"ticker": "TMPV", "weight": 1.0}],
            "total_value_inr": 1_000_000,
        },
        lookback_days=504,
        n_sims=20_000,
    )
    if response.status_code == 422:
        pytest.skip("database not ingested")

    var_pct = response.json()["parametric"]["estimates"][0]["var_pct"]
    # A single Indian large-cap's 1-day 95% VaR is a low single-digit percent.
    # Leaving the -40% day in would push this well into double digits.
    assert 0 < var_pct < 10, f"VaR looks contaminated: {var_pct:.2f}%"


def test_matrix_columns_align_with_requested_tickers(ingested_db):
    if not _has_data(ingested_db, "RELIANCE"):
        pytest.skip("database not ingested")

    tickers = ["TCS", "RELIANCE", "INFY"]
    frame, _ = build_returns_matrix(ingested_db, tickers, lookback_days=100)

    assert list(frame.columns) == tickers
    assert len(frame) == 100
    assert frame.index.is_monotonic_increasing
    assert not frame.isna().any().any()


def test_var_response_carries_the_pnl_distribution(client):
    response = _post(client, n_sims=5_000)
    if response.status_code == 422:
        pytest.skip("database not ingested")

    distribution = response.json()["distribution"]
    assert len(distribution["bin_edges"]) == distribution["n_bins"] + 1
    assert sum(distribution["parametric_counts"]) == 5_000
    assert sum(distribution["historical_bootstrap_counts"]) == 5_000
    # Shared edges: the two series are directly overlayable.
    assert len(distribution["parametric_counts"]) == distribution["n_bins"]
    assert len(distribution["historical_bootstrap_counts"]) == distribution["n_bins"]


def test_distribution_bin_count_is_configurable_and_capped(client):
    from app.core.limits import MAX_DISTRIBUTION_BINS

    response = _post(client, n_sims=2_000, distribution_bins=24)
    if response.status_code == 422:
        pytest.skip("database not ingested")
    assert response.json()["distribution"]["n_bins"] == 24

    over = _post(client, distribution_bins=MAX_DISTRIBUTION_BINS + 1)
    assert over.status_code == 422
    assert over.json()["error_code"] == "REQUEST_LIMIT_EXCEEDED"


def test_raw_simulation_array_is_not_returned(client):
    """200k floats is a payload problem; the chart needs bins, not samples."""
    response = _post(client, n_sims=5_000)
    if response.status_code == 422:
        pytest.skip("database not ingested")

    payload = response.json()
    assert "path_returns" not in payload
    assert "simulations" not in payload
    for key in ("parametric", "historical_bootstrap"):
        assert not any(
            isinstance(value, list) and len(value) > 1000
            for value in payload[key].values()
        )
