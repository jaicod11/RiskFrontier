"""Optimiser endpoint and its integration with the shared cleaning layer."""

from __future__ import annotations

import numpy as np
import pandas as pd
import pytest
from sqlalchemy import select

from app.core.limitations import (
    CORRELATION_BREAKDOWN_WARNING,
    EXPECTED_RETURN_ESTIMATION_WARNING,
    OPTIMIZER_LIMITATIONS,
    TRANSACTION_COST_WARNING,
    VAR_LIMITATIONS,
)
from app.models import Security
from app.services.markowitz import annualised_moments, max_sharpe_portfolio
from app.services.returns import build_returns_matrix

UNIVERSE = ["RELIANCE", "TCS", "HDFCBANK", "INFY", "ITC"]


def _post(client, **overrides):
    body = {"tickers": UNIVERSE, "n_frontier_points": 12, "seed": 0}
    body.update(overrides)
    return client.post("/api/portfolio/optimize", json=body)


def _has_data(db, ticker: str) -> bool:
    security = db.scalar(select(Security).where(Security.ticker == ticker))
    return security is not None and bool(security.daily_prices)


# --- endpoint contract ------------------------------------------------------


def test_optimize_returns_frontier_and_both_portfolios(client):
    response = _post(client)
    if response.status_code == 422:
        pytest.skip("database not ingested")
    assert response.status_code == 200

    payload = response.json()
    assert payload["frontier_points_returned"] > 0
    assert len(payload["efficient_frontier"]) == payload["frontier_points_returned"]

    for key in ("min_variance_portfolio", "max_sharpe_portfolio"):
        portfolio = payload[key]
        assert set(portfolio) == {
            "expected_return", "volatility", "sharpe_ratio", "weights"
        }
        assert set(portfolio["weights"]) == set(UNIVERSE)


def test_all_returned_weights_obey_the_constraints(client):
    response = _post(client, max_weight_per_asset=0.35)
    if response.status_code == 422:
        pytest.skip("database not ingested")
    payload = response.json()

    everything = (
        payload["efficient_frontier"]
        + [payload["min_variance_portfolio"], payload["max_sharpe_portfolio"]]
    )
    for portfolio in everything:
        weights = list(portfolio["weights"].values())
        assert sum(weights) == pytest.approx(1.0, abs=1e-6)
        assert min(weights) >= -1e-9
        assert max(weights) <= 0.35 + 1e-9


def test_min_variance_is_the_leftmost_frontier_point(client):
    response = _post(client, n_frontier_points=25)
    if response.status_code == 422:
        pytest.skip("database not ingested")
    payload = response.json()

    floor = payload["min_variance_portfolio"]["volatility"]
    for point in payload["efficient_frontier"]:
        assert point["volatility"] >= floor - 1e-6


def test_max_sharpe_beats_every_frontier_point(client):
    response = _post(client, n_frontier_points=30)
    if response.status_code == 422:
        pytest.skip("database not ingested")
    payload = response.json()

    best = payload["max_sharpe_portfolio"]["sharpe_ratio"]
    on_frontier = max(p["sharpe_ratio"] for p in payload["efficient_frontier"])
    assert best >= on_frontier - 1e-4


def test_limitations_come_from_the_shared_module(client):
    response = _post(client)
    if response.status_code == 422:
        pytest.skip("database not ingested")

    limitations = response.json()["limitations"]
    assert limitations == OPTIMIZER_LIMITATIONS
    assert EXPECTED_RETURN_ESTIMATION_WARNING in limitations
    assert TRANSACTION_COST_WARNING in limitations
    # Shared wording, defined once.
    assert CORRELATION_BREAKDOWN_WARNING in limitations
    assert CORRELATION_BREAKDOWN_WARNING in VAR_LIMITATIONS


def test_limitations_mention_the_key_risks(client):
    response = _post(client)
    if response.status_code == 422:
        pytest.skip("database not ingested")
    joined = " ".join(response.json()["limitations"]).lower()

    assert "noisier" in joined              # historical means are noisy
    assert "scepticism" in joined or "skepticism" in joined
    assert "transaction costs" in joined
    assert "correlation" in joined


def test_risk_free_rate_is_echoed_and_honoured(client):
    response = _post(client, risk_free_rate=0.08)
    if response.status_code == 422:
        pytest.skip("database not ingested")
    payload = response.json()

    assert payload["risk_free_rate"] == 0.08
    portfolio = payload["max_sharpe_portfolio"]
    recomputed = (
        portfolio["expected_return"] - 0.08
    ) / portfolio["volatility"]
    assert portfolio["sharpe_ratio"] == pytest.approx(recomputed, rel=1e-6)


def test_window_is_reported_like_phase_three(client):
    response = _post(
        client, tickers=["RELIANCE", "TCS", "JIOFIN"], lookback_days=2000
    )
    if response.status_code == 422:
        pytest.skip("database not ingested")

    window = response.json()["data_window"]
    assert window["shrunk"] is True
    assert window["constrained_by"] == "JIOFIN"
    assert "JIOFIN" in window["constraint_reason"]


def test_infeasible_cap_returns_422_with_guidance(client):
    response = _post(client, max_weight_per_asset=0.1)  # 5 * 0.1 = 0.5
    assert response.status_code == 422
    assert "0.2" in str(response.json()["detail"])  # suggests 1/n


def test_unknown_ticker_returns_404(client):
    response = _post(client, tickers=["RELIANCE", "NOTATICKER"])
    assert response.status_code == 404


def test_at_least_two_tickers_required(client):
    response = _post(client, tickers=["RELIANCE"])
    assert response.status_code == 422


def test_duplicate_tickers_are_collapsed(client):
    response = _post(client, tickers=["RELIANCE", "RELIANCE.NS", "TCS", "INFY"])
    if response.status_code == 422:
        pytest.skip("database not ingested")
    assert response.json()["tickers"] == ["RELIANCE", "TCS", "INFY"]


def test_same_seed_same_weights(client):
    first = _post(client, seed=5)
    if first.status_code == 422:
        pytest.skip("database not ingested")
    second = _post(client, seed=5)
    assert (
        first.json()["max_sharpe_portfolio"]["weights"]
        == second.json()["max_sharpe_portfolio"]["weights"]
    )


# --- the cleaning layer is in the path --------------------------------------


def test_optimizer_uses_cleaned_returns_not_raw_prices(ingested_db):
    """Same check as Phase 3: the TMPV artefact must not reach the covariance.

    The optimiser consumes build_returns_matrix, so proving the matrix is clean
    proves the covariance it estimates from is clean.
    """
    if not _has_data(ingested_db, "TMPV"):
        pytest.skip("TMPV not ingested")

    frame, _ = build_returns_matrix(
        ingested_db, ["TMPV", "RELIANCE", "TCS"], lookback_days=504
    )
    anomaly = pd.Timestamp(2025, 10, 14)
    assert anomaly in frame.index
    assert frame.loc[anomaly, "TMPV"] == 0.0

    _, cov = annualised_moments(frame)
    tmpv_index = list(frame.columns).index("TMPV")
    annualised_vol = float(np.sqrt(cov[tmpv_index, tmpv_index]))

    # With the -40% day left in, one outlier alone contributes
    # 0.4015^2/503*252 ≈ 0.20 to the annualised variance — a vol floor near 45%.
    # A cleaned large-cap sits far below that.
    assert annualised_vol < 0.40, (
        f"TMPV annualised vol {annualised_vol:.3f} looks contaminated by the "
        "demerger break"
    )


def test_optimizer_weights_for_anomalous_ticker_are_sane(client):
    """TMPV should be treated as an ordinary stock, not a 40%-crash outlier."""
    response = _post(
        client,
        tickers=["TMPV", "RELIANCE", "TCS", "HDFCBANK"],
        max_weight_per_asset=0.4,
    )
    if response.status_code == 422:
        pytest.skip("database not ingested")
    payload = response.json()

    volatility = payload["min_variance_portfolio"]["volatility"]
    # A four-stock large-cap min-variance portfolio: tens of percent, not 100%+.
    assert 0.0 < volatility < 0.5, f"volatility looks contaminated: {volatility}"


def test_optimizer_and_var_agree_on_the_window(client):
    """Both endpoints read the same data path, so windows must match."""
    optimize = _post(client, tickers=UNIVERSE, lookback_days=504)
    if optimize.status_code == 422:
        pytest.skip("database not ingested")

    var = client.post(
        "/api/risk/var",
        json={
            "portfolio": {
                "positions": [{"ticker": t, "weight": 0.2} for t in UNIVERSE],
                "total_value": 1_000_000,
            },
            "n_sims": 1_000,
            "lookback_days": 504,
            "seed": 1,
        },
    )
    assert var.status_code == 200
    assert optimize.json()["data_window"] == var.json()["data_window"]
