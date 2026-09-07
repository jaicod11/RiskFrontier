"""Simulation maths, on synthetic data — no database, no network."""

from __future__ import annotations

import numpy as np
import pandas as pd
import pytest
from scipy.stats import norm

from app.schemas.portfolio import Portfolio
from app.services.monte_carlo import (
    run_historical_bootstrap_var,
    run_parametric_var,
)

TOTAL_VALUE = 1_000_000.0
BIG_N = 200_000  # large enough that quantile noise is well under the tolerances


@pytest.fixture
def single_asset() -> pd.DataFrame:
    rng = np.random.default_rng(20260907)
    return pd.DataFrame(
        {"RELIANCE": rng.normal(0.0006, 0.014, 1000)},
        index=pd.bdate_range("2022-01-03", periods=1000, name="date"),
    )


@pytest.fixture
def negatively_correlated() -> pd.DataFrame:
    """Two assets that genuinely offset each other."""
    rng = np.random.default_rng(11)
    n = 1500
    a = rng.normal(0.0005, 0.016, n)
    b = -0.65 * a + rng.normal(0.0005, 0.010, n)
    return pd.DataFrame(
        {"AAA": a, "BBB": b},
        index=pd.bdate_range("2020-01-01", periods=n, name="date"),
    )


# --- tie back to the textbook -------------------------------------------------


def test_parametric_var_matches_closed_form_normal(single_asset):
    """The sanity check: one asset, one day, versus the z-score formula."""
    mu = float(single_asset.mean().iloc[0])
    sigma = float(single_asset.std(ddof=1).iloc[0])

    # VaR_c = -(mu + z_{1-c} * sigma) * V
    z = norm.ppf(0.05)
    analytic_var = -(mu + z * sigma) * TOTAL_VALUE

    result = run_parametric_var(
        single_asset, [1.0], TOTAL_VALUE,
        n_sims=BIG_N, horizon_days=1, confidence_levels=[0.95], seed=123,
    )

    assert result.estimates[0].var_inr == pytest.approx(analytic_var, rel=0.02)


def test_parametric_cvar_matches_closed_form_expected_shortfall(single_asset):
    """CVaR against the analytic expected shortfall of a normal."""
    mu = float(single_asset.mean().iloc[0])
    sigma = float(single_asset.std(ddof=1).iloc[0])

    # ES_c = mu - sigma * phi(z_{1-c}) / (1-c)
    z = norm.ppf(0.05)
    analytic_es_return = mu - sigma * norm.pdf(z) / 0.05
    analytic_cvar = -analytic_es_return * TOTAL_VALUE

    result = run_parametric_var(
        single_asset, [1.0], TOTAL_VALUE,
        n_sims=BIG_N, horizon_days=1, confidence_levels=[0.95], seed=123,
    )

    assert result.estimates[0].cvar_inr == pytest.approx(analytic_cvar, rel=0.03)


def test_cvar_always_exceeds_var(single_asset):
    """Expected shortfall is a mean beyond the threshold, so it must be worse."""
    result = run_parametric_var(
        single_asset, [1.0], TOTAL_VALUE,
        n_sims=50_000, confidence_levels=[0.95, 0.99], seed=5,
    )
    for estimate in result.estimates:
        assert estimate.cvar_inr > estimate.var_inr


def test_higher_confidence_gives_larger_var(single_asset):
    result = run_parametric_var(
        single_asset, [1.0], TOTAL_VALUE,
        n_sims=50_000, confidence_levels=[0.90, 0.95, 0.99], seed=5,
    )
    values = [e.var_inr for e in result.estimates]
    assert values == sorted(values)


def test_percentages_are_consistent_with_rupees(single_asset):
    result = run_parametric_var(
        single_asset, [1.0], TOTAL_VALUE, n_sims=20_000, seed=3
    )
    for estimate in result.estimates:
        assert estimate.var_pct == pytest.approx(
            estimate.var_inr / TOTAL_VALUE * 100
        )
        assert estimate.cvar_pct == pytest.approx(
            estimate.cvar_inr / TOTAL_VALUE * 100
        )


# --- diversification ----------------------------------------------------------


@pytest.mark.parametrize(
    "method", [run_parametric_var, run_historical_bootstrap_var]
)
def test_portfolio_var_beats_weighted_sum_of_asset_vars(
    negatively_correlated, method
):
    """A portfolio of offsetting assets must risk less than its parts."""
    frame = negatively_correlated
    kwargs = dict(n_sims=BIG_N, horizon_days=1, confidence_levels=[0.95], seed=99)

    var_a = method(frame[["AAA"]], [1.0], TOTAL_VALUE, **kwargs).estimates[0].var_inr
    var_b = method(frame[["BBB"]], [1.0], TOTAL_VALUE, **kwargs).estimates[0].var_inr
    portfolio = method(frame, [0.5, 0.5], TOTAL_VALUE, **kwargs).estimates[0].var_inr

    weighted_sum = 0.5 * var_a + 0.5 * var_b
    assert portfolio < weighted_sum, (
        f"no diversification benefit: portfolio {portfolio:,.0f} "
        f"vs weighted sum {weighted_sum:,.0f}"
    )


def test_bootstrap_preserves_cross_sectional_correlation():
    """Resampling whole days must keep the correlation; per-ticker would not.

    Two perfectly anti-correlated assets held 50/50 have almost no portfolio
    risk. That only survives if each simulated day takes both assets from the
    *same* historical date.
    """
    rng = np.random.default_rng(4)
    a = rng.normal(0.0, 0.02, 800)
    frame = pd.DataFrame(
        {"AAA": a, "BBB": -a},  # exactly offsetting
        index=pd.bdate_range("2021-01-01", periods=800, name="date"),
    )

    result = run_historical_bootstrap_var(
        frame, [0.5, 0.5], TOTAL_VALUE,
        n_sims=20_000, horizon_days=1, confidence_levels=[0.95], seed=8,
    )
    # A 50/50 book of exact opposites is flat every day: VaR ≈ 0.
    assert abs(result.estimates[0].var_inr) < TOTAL_VALUE * 0.001


# --- reproducibility ----------------------------------------------------------


@pytest.mark.parametrize(
    "method", [run_parametric_var, run_historical_bootstrap_var]
)
def test_same_seed_same_result(negatively_correlated, method):
    kwargs = dict(
        n_sims=10_000, horizon_days=5, confidence_levels=[0.95, 0.99], seed=2026
    )
    first = method(negatively_correlated, [0.5, 0.5], TOTAL_VALUE, **kwargs)
    second = method(negatively_correlated, [0.5, 0.5], TOTAL_VALUE, **kwargs)

    assert first.model_dump() == second.model_dump()


@pytest.mark.parametrize(
    "method", [run_parametric_var, run_historical_bootstrap_var]
)
def test_different_seed_different_result(negatively_correlated, method):
    kwargs = dict(n_sims=10_000, horizon_days=5, confidence_levels=[0.95])
    first = method(negatively_correlated, [0.5, 0.5], TOTAL_VALUE, seed=1, **kwargs)
    second = method(negatively_correlated, [0.5, 0.5], TOTAL_VALUE, seed=2, **kwargs)

    assert first.estimates[0].var_inr != second.estimates[0].var_inr


# --- horizon behaviour --------------------------------------------------------


def test_longer_horizon_increases_var(single_asset):
    kwargs = dict(n_sims=50_000, confidence_levels=[0.95], seed=17)
    one_day = run_parametric_var(
        single_asset, [1.0], TOTAL_VALUE, horizon_days=1, **kwargs
    )
    ten_day = run_parametric_var(
        single_asset, [1.0], TOTAL_VALUE, horizon_days=10, **kwargs
    )
    assert ten_day.estimates[0].var_inr > one_day.estimates[0].var_inr


def test_compounding_is_not_sqrt_time(single_asset):
    """Compounded multi-day VaR should not equal the sqrt(T) shortcut exactly."""
    kwargs = dict(n_sims=BIG_N, confidence_levels=[0.95], seed=31)
    one_day = run_parametric_var(
        single_asset, [1.0], TOTAL_VALUE, horizon_days=1, **kwargs
    ).estimates[0].var_inr
    ten_day = run_parametric_var(
        single_asset, [1.0], TOTAL_VALUE, horizon_days=10, **kwargs
    ).estimates[0].var_inr

    sqrt_time = one_day * np.sqrt(10)
    # Close in magnitude (drift and compounding are second-order here) but the
    # simulation is doing real path compounding, not a closed-form scale-up.
    assert ten_day != pytest.approx(sqrt_time, rel=1e-9)
    assert 0.7 < ten_day / sqrt_time < 1.3


# --- guardrails ---------------------------------------------------------------


def test_weight_count_must_match_columns(single_asset):
    with pytest.raises(ValueError, match="weights for"):
        run_parametric_var(single_asset, [0.5, 0.5], TOTAL_VALUE, n_sims=100)


def test_empty_frame_rejected():
    with pytest.raises(ValueError, match="empty"):
        run_parametric_var(pd.DataFrame(), [], TOTAL_VALUE, n_sims=100)


def test_singular_covariance_is_repaired():
    """Two identical columns make the covariance singular; it must not crash."""
    rng = np.random.default_rng(0)
    a = rng.normal(0.0, 0.01, 300)
    frame = pd.DataFrame(
        {"AAA": a, "BBB": a},
        index=pd.bdate_range("2022-01-03", periods=300, name="date"),
    )
    result = run_parametric_var(
        frame, [0.5, 0.5], TOTAL_VALUE, n_sims=5_000, seed=1
    )
    assert np.isfinite(result.estimates[0].var_inr)


def test_blocking_does_not_change_results(monkeypatch, single_asset):
    """Memory blocking is an implementation detail, not a numerical one."""
    import app.services.monte_carlo as mc

    full = run_parametric_var(
        single_asset, [1.0], TOTAL_VALUE, n_sims=8_000, horizon_days=3, seed=77
    )
    monkeypatch.setattr(mc, "MAX_BLOCK_ELEMENTS", 1_000)  # force many blocks
    blocked = run_parametric_var(
        single_asset, [1.0], TOTAL_VALUE, n_sims=8_000, horizon_days=3, seed=77
    )
    # Same seed, same draws in the same order -> identical output.
    assert full.model_dump() == blocked.model_dump()


# --- portfolio schema ---------------------------------------------------------


def test_weights_must_sum_to_one():
    with pytest.raises(ValueError, match="must sum to 1.0"):
        Portfolio(
            positions=[
                {"ticker": "RELIANCE", "weight": 0.5},
                {"ticker": "TCS", "weight": 0.3},
            ],
            total_value=1_000_000,
        )


def test_weight_error_names_the_actual_sum():
    with pytest.raises(ValueError) as excinfo:
        Portfolio(
            positions=[{"ticker": "RELIANCE", "weight": 0.8}],
            total_value=1_000_000,
        )
    message = str(excinfo.value)
    assert "0.800000" in message
    assert "RELIANCE=0.8" in message


def test_rounding_tolerance_is_accepted():
    portfolio = Portfolio(
        positions=[
            {"ticker": "A", "weight": 0.333333},
            {"ticker": "B", "weight": 0.333333},
            {"ticker": "C", "weight": 0.333334},
        ],
        total_value=1_000_000,
    )
    assert len(portfolio.positions) == 3


def test_duplicate_tickers_rejected():
    with pytest.raises(ValueError, match="Duplicate tickers"):
        Portfolio(
            positions=[
                {"ticker": "RELIANCE", "weight": 0.5},
                {"ticker": "RELIANCE.NS", "weight": 0.5},
            ],
            total_value=1_000_000,
        )


def test_ticker_suffix_is_normalised():
    portfolio = Portfolio(
        positions=[{"ticker": "reliance.ns", "weight": 1.0}],
        total_value=1_000_000,
    )
    assert portfolio.tickers == ["RELIANCE"]


def test_total_value_must_be_positive():
    with pytest.raises(ValueError):
        Portfolio(
            positions=[{"ticker": "RELIANCE", "weight": 1.0}], total_value=0
        )
