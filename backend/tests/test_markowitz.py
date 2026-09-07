"""Optimiser maths, on synthetic data — no database, no network."""

from __future__ import annotations

import numpy as np
import pandas as pd
import pytest

from app.services.markowitz import (
    DEFAULT_MAX_WEIGHT_PER_ASSET,
    InfeasibleConstraintsError,
    annualised_moments,
    efficient_frontier,
    max_achievable_return,
    max_sharpe_portfolio,
    min_variance_portfolio,
    sharpe_ratio,
)

RISK_FREE = 0.065


def _correlated_frame(
    sigma1: float, sigma2: float, rho: float, n: int = 3000, seed: int = 7
) -> pd.DataFrame:
    """Two daily return series with a target annualised vol and correlation."""
    rng = np.random.default_rng(seed)
    daily1 = sigma1 / np.sqrt(252)
    daily2 = sigma2 / np.sqrt(252)

    z1 = rng.standard_normal(n)
    z2 = rho * z1 + np.sqrt(1 - rho**2) * rng.standard_normal(n)
    return pd.DataFrame(
        {"AAA": z1 * daily1, "BBB": z2 * daily2},
        index=pd.bdate_range("2014-01-01", periods=n, name="date"),
    )


def _multi_asset_frame(n: int = 1500, seed: int = 3) -> pd.DataFrame:
    rng = np.random.default_rng(seed)
    base = rng.normal(0.0003, 0.010, n)
    return pd.DataFrame(
        {
            "AAA": base + rng.normal(0.0004, 0.008, n),
            "BBB": 0.5 * base + rng.normal(0.0002, 0.011, n),
            "CCC": -0.3 * base + rng.normal(0.0003, 0.009, n),
            "DDD": rng.normal(0.0001, 0.013, n),
        },
        index=pd.bdate_range("2018-01-01", periods=n, name="date"),
    )


# --- tie back to the textbook ----------------------------------------------


def test_two_asset_min_variance_matches_closed_form():
    """w* = (s2^2 - rho*s1*s2) / (s1^2 + s2^2 - 2*rho*s1*s2)."""
    frame = _correlated_frame(sigma1=0.20, sigma2=0.30, rho=0.2)

    # Use the SAMPLE moments the optimiser actually sees, not the generating ones.
    _, cov = annualised_moments(frame)
    var1, var2 = cov[0, 0], cov[1, 1]
    covariance = cov[0, 1]

    expected_w1 = (var2 - covariance) / (var1 + var2 - 2 * covariance)

    # Uncapped, so the closed form's unconstrained solution is reachable.
    result = min_variance_portfolio(
        frame, risk_free_rate=RISK_FREE, max_weight_per_asset=1.0
    )

    assert 0.0 < expected_w1 < 1.0, "test setup should give an interior solution"
    assert result.weights["AAA"] == pytest.approx(expected_w1, abs=1e-3)
    assert result.weights["BBB"] == pytest.approx(1 - expected_w1, abs=1e-3)


@pytest.mark.parametrize("rho", [-0.5, 0.0, 0.3, 0.7])
def test_closed_form_holds_across_correlations(rho):
    frame = _correlated_frame(sigma1=0.18, sigma2=0.25, rho=rho, seed=11)
    _, cov = annualised_moments(frame)
    var1, var2, covariance = cov[0, 0], cov[1, 1], cov[0, 1]
    expected_w1 = (var2 - covariance) / (var1 + var2 - 2 * covariance)

    result = min_variance_portfolio(frame, max_weight_per_asset=1.0)
    assert result.weights["AAA"] == pytest.approx(expected_w1, abs=2e-3)


def test_annualisation_scales_by_252():
    frame = _multi_asset_frame()
    mu, cov = annualised_moments(frame)

    np.testing.assert_allclose(mu, frame.mean().to_numpy() * 252, rtol=1e-12)
    np.testing.assert_allclose(cov, frame.cov().to_numpy() * 252, rtol=1e-12)


# --- constraint compliance --------------------------------------------------


def _all_portfolios(frame, **kwargs):
    trace = efficient_frontier(frame, n_points=15, **kwargs)
    return (
        [min_variance_portfolio(frame, **kwargs), max_sharpe_portfolio(frame, **kwargs)]
        + trace.points
    )


@pytest.mark.parametrize("cap", [0.35, 0.5, 1.0])
def test_every_portfolio_respects_the_constraints(cap):
    frame = _multi_asset_frame()
    for point in _all_portfolios(frame, max_weight_per_asset=cap):
        weights = np.array(list(point.weights.values()))
        assert weights.sum() == pytest.approx(1.0, abs=1e-6)
        assert (weights >= -1e-9).all(), f"negative weight: {point.weights}"
        assert (weights <= cap + 1e-9).all(), f"cap breached: {point.weights}"


def test_infeasible_cap_is_rejected_with_a_clear_message():
    frame = _multi_asset_frame()  # 4 assets
    with pytest.raises(InfeasibleConstraintsError) as excinfo:
        min_variance_portfolio(frame, max_weight_per_asset=0.2)  # 4 * 0.2 = 0.8
    message = str(excinfo.value)
    assert "0.8" in message
    assert "0.25" in message  # suggests 1/n


def test_two_assets_with_the_default_cap_is_infeasible():
    """A guardrail worth stating: 2 x 0.35 = 0.70, which cannot sum to 1."""
    frame = _correlated_frame(0.2, 0.3, 0.1, n=500)
    with pytest.raises(InfeasibleConstraintsError):
        min_variance_portfolio(
            frame, max_weight_per_asset=DEFAULT_MAX_WEIGHT_PER_ASSET
        )


# --- frontier properties ----------------------------------------------------


def test_min_variance_is_the_leftmost_point():
    frame = _multi_asset_frame()
    kwargs = {"max_weight_per_asset": 0.5, "risk_free_rate": RISK_FREE}

    floor = min_variance_portfolio(frame, **kwargs)
    trace = efficient_frontier(frame, n_points=25, **kwargs)

    assert trace.points, "frontier should not be empty"
    for point in trace.points:
        assert point.volatility >= floor.volatility - 1e-6, (
            f"frontier point vol {point.volatility:.6f} is below the "
            f"minimum-variance vol {floor.volatility:.6f}"
        )


def test_frontier_is_monotonic_in_return_and_volatility():
    """Walking up the frontier, both risk and return should increase."""
    frame = _multi_asset_frame()
    trace = efficient_frontier(
        frame, n_points=25, max_weight_per_asset=0.5, risk_free_rate=RISK_FREE
    )
    vols = [p.volatility for p in trace.points]
    rets = [p.expected_return for p in trace.points]

    assert vols == sorted(vols)
    # Allow tiny numerical wobble between adjacent points.
    for earlier, later in zip(rets, rets[1:]):
        assert later >= earlier - 1e-6


def test_max_sharpe_beats_every_frontier_point():
    """It must actually be optimal, not merely 'a' portfolio."""
    frame = _multi_asset_frame()
    kwargs = {"max_weight_per_asset": 0.5, "risk_free_rate": RISK_FREE}

    best = max_sharpe_portfolio(frame, **kwargs)
    trace = efficient_frontier(frame, n_points=40, **kwargs)

    assert trace.points
    best_on_frontier = max(p.sharpe_ratio for p in trace.points)
    assert best.sharpe_ratio >= best_on_frontier - 1e-4, (
        f"max-Sharpe {best.sharpe_ratio:.6f} is beaten by a frontier point "
        f"at {best_on_frontier:.6f}"
    )


def test_reported_statistics_are_consistent_with_the_weights():
    frame = _multi_asset_frame()
    mu, cov = annualised_moments(frame)
    point = max_sharpe_portfolio(
        frame, max_weight_per_asset=0.5, risk_free_rate=RISK_FREE
    )
    weights = np.array([point.weights[t] for t in frame.columns])

    assert point.expected_return == pytest.approx(weights @ mu, rel=1e-6)
    assert point.volatility == pytest.approx(np.sqrt(weights @ cov @ weights), rel=1e-6)
    assert point.sharpe_ratio == pytest.approx(
        sharpe_ratio(weights, mu, cov, RISK_FREE), rel=1e-6
    )


# --- the weight cap must actually bind --------------------------------------


def test_weight_cap_binds_on_a_dominant_asset():
    """One ticker with an artificially high mean; the cap must stop it."""
    rng = np.random.default_rng(42)
    n = 1200
    frame = pd.DataFrame(
        {
            # Strong return, modest vol -> the optimiser wants all of it.
            "STAR": rng.normal(0.0020, 0.008, n),
            "MEH1": rng.normal(0.0001, 0.012, n),
            "MEH2": rng.normal(0.0000, 0.013, n),
        },
        index=pd.bdate_range("2020-01-01", periods=n, name="date"),
    )

    capped = max_sharpe_portfolio(
        frame, risk_free_rate=RISK_FREE, max_weight_per_asset=0.5
    )
    uncapped = max_sharpe_portfolio(
        frame, risk_free_rate=RISK_FREE, max_weight_per_asset=1.0
    )

    # Without the cap the optimiser concentrates hard...
    assert uncapped.weights["STAR"] > 0.8, uncapped.weights
    # ...and with it, it sits exactly on the ceiling rather than going further.
    assert capped.weights["STAR"] == pytest.approx(0.5, abs=1e-4), capped.weights
    assert capped.sharpe_ratio < uncapped.sharpe_ratio


def test_max_achievable_return_respects_the_cap():
    frame = _multi_asset_frame()
    mu, _ = annualised_moments(frame)

    uncapped = max_achievable_return(mu, [(0.0, 1.0)] * len(mu))
    capped = max_achievable_return(mu, [(0.0, 0.35)] * len(mu))

    assert uncapped == pytest.approx(mu.max())
    assert capped < uncapped


# --- resilience -------------------------------------------------------------


def test_unreachable_frontier_targets_are_skipped_not_fatal():
    """One impossible target must not cost the caller the whole frontier."""
    frame = _multi_asset_frame()
    mu, _ = annualised_moments(frame)
    reachable = float(mu.max()) * 0.5

    trace = efficient_frontier(
        frame,
        max_weight_per_asset=0.5,
        risk_free_rate=RISK_FREE,
        # Two reachable targets bracketing two wildly impossible ones.
        target_returns=[reachable * 0.5, reachable, 5.0, 50.0],
    )

    assert trace.requested_points == 4
    assert len(trace.points) == 2, "the reachable targets should still be returned"
    assert len(trace.skipped_target_returns) == 2
    assert 5.0 in trace.skipped_target_returns
    assert 50.0 in trace.skipped_target_returns


def test_frontier_survives_a_pathological_covariance():
    """Collinear columns make the problem degenerate; it must not crash."""
    rng = np.random.default_rng(0)
    base = rng.normal(0.0002, 0.01, 600)
    frame = pd.DataFrame(
        {
            "AAA": base,
            "BBB": base,               # perfectly collinear
            "CCC": base * 1e-8,        # near-zero variance, wildly different scale
        },
        index=pd.bdate_range("2021-01-01", periods=600, name="date"),
    )

    trace = efficient_frontier(frame, n_points=10, max_weight_per_asset=0.6)
    result = min_variance_portfolio(frame, max_weight_per_asset=0.6)

    assert np.isfinite(result.volatility)
    for point in trace.points:
        assert np.isfinite(point.volatility)
        assert sum(point.weights.values()) == pytest.approx(1.0, abs=1e-6)


def test_results_are_reproducible():
    frame = _multi_asset_frame()
    kwargs = {"max_weight_per_asset": 0.5, "risk_free_rate": RISK_FREE, "seed": 99}

    first = max_sharpe_portfolio(frame, **kwargs)
    second = max_sharpe_portfolio(frame, **kwargs)
    assert first.weights == second.weights


def test_short_selling_permits_negative_weights():
    frame = _multi_asset_frame()
    result = min_variance_portfolio(
        frame, max_weight_per_asset=0.6, allow_short=True
    )
    weights = np.array(list(result.weights.values()))

    assert weights.sum() == pytest.approx(1.0, abs=1e-6)
    assert (weights >= -0.6 - 1e-9).all()
    assert (weights <= 0.6 + 1e-9).all()
