"""Rolling-window and block-bootstrap machinery."""

from __future__ import annotations

import datetime as dt

import numpy as np
import pandas as pd
import pytest
from sqlalchemy import func, select

from app.core.limitations import (
    BOOTSTRAP_LIMITATIONS,
    MULTIPLE_COMPARISONS_WARNING,
    OVERLAPPING_WINDOWS_WARNING,
)
from app.models import DailyPrice, Security
from app.services.bootstrap import (
    SERIES_KEYS,
    StrategyConfig,
    bootstrap_backtest,
    iid_indices,
    rolling_windows,
    stationary_block_indices,
)

UNIVERSE = ["RELIANCE", "TCS", "HDFCBANK", "INFY"]
EQUAL = {t: 0.25 for t in UNIVERSE}


def _has_data(db, ticker: str) -> bool:
    security = db.scalar(select(Security).where(Security.ticker == ticker))
    if security is None:
        return False
    return bool(
        db.scalar(
            select(func.count(DailyPrice.id)).where(
                DailyPrice.security_id == security.id
            )
        )
    )


def _autocorrelation(series: np.ndarray, max_lag: int = 10) -> float:
    """Mean autocorrelation of |returns| over the first `max_lag` lags."""
    centred = series - series.mean()
    denominator = float(np.dot(centred, centred))
    if denominator == 0:
        return 0.0
    return float(
        np.mean(
            [
                np.dot(centred[:-lag], centred[lag:]) / denominator
                for lag in range(1, max_lag + 1)
            ]
        )
    )


# --- rolling windows, hand-checked ------------------------------------------


def test_rolling_windows_match_a_hand_computation():
    dates = [d.date() for d in pd.bdate_range("2020-01-01", "2024-12-31")]
    windows = rolling_windows(dates, window_years=2)

    # Expected: first traded day of each month, kept when start + 2y <= last.
    month_starts: list[dt.date] = []
    seen: set[tuple[int, int]] = set()
    for day in dates:
        key = (day.year, day.month)
        if key not in seen:
            seen.add(key)
            month_starts.append(day)

    expected = [
        (start, start.replace(year=start.year + 2))
        for start in month_starts
        if start.replace(year=start.year + 2) <= dates[-1]
    ]

    assert windows == expected
    assert len(windows) == 36, f"2020-01 through 2022-12 inclusive, got {len(windows)}"
    assert windows[0][0] == dt.date(2020, 1, 1)
    assert windows[0][1] == dt.date(2022, 1, 1)
    assert windows[-1][1] <= dates[-1]


def test_rolling_windows_step_one_month_at_a_time():
    dates = [d.date() for d in pd.bdate_range("2020-01-01", "2025-12-31")]
    windows = rolling_windows(dates, window_years=3)

    starts = [w[0] for w in windows]
    for earlier, later in zip(starts, starts[1:]):
        months = (later.year - earlier.year) * 12 + (later.month - earlier.month)
        assert months == 1, f"{earlier} -> {later} is not one month"


def test_warmup_reserves_history_at_the_front():
    dates = [d.date() for d in pd.bdate_range("2020-01-01", "2025-12-31")]
    without = rolling_windows(dates, window_years=2, warmup_days=0)
    with_warmup = rolling_windows(dates, window_years=2, warmup_days=252)

    assert len(with_warmup) < len(without)
    assert with_warmup[0][0] >= dates[252]


def test_no_windows_when_the_span_is_too_short():
    dates = [d.date() for d in pd.bdate_range("2024-01-01", "2024-06-30")]
    assert rolling_windows(dates, window_years=5) == []


def test_fractional_window_years_supported():
    dates = [d.date() for d in pd.bdate_range("2020-01-01", "2024-12-31")]
    windows = rolling_windows(dates, window_years=1.5)
    start, end = windows[0]
    assert (end.year - start.year) * 12 + (end.month - start.month) == 18


# --- block bootstrap keeps the clustering -----------------------------------


def _clustered_returns(n: int = 3000, seed: int = 4) -> np.ndarray:
    """Alternating high/low volatility regimes — strong clustering in |r|."""
    rng = np.random.default_rng(seed)
    regime = (np.arange(n) // 60) % 2
    volatility = np.where(regime == 0, 0.030, 0.004)
    return rng.normal(0.0, volatility)


def test_block_bootstrap_preserves_autocorrelation_better_than_iid():
    """The reason blocks exist: i.i.d. days destroy volatility clustering."""
    returns = _clustered_returns()
    rng = np.random.default_rng(0)
    length = 2000

    original = _autocorrelation(np.abs(returns))

    block = np.abs(
        returns[stationary_block_indices(len(returns), length, rng, expected_block=21)]
    )
    iid = np.abs(returns[iid_indices(len(returns), length, rng)])

    block_acf = _autocorrelation(block)
    iid_acf = _autocorrelation(iid)

    assert original > 0.2, "test setup should have strong clustering"
    assert iid_acf < 0.05, f"i.i.d. resampling should destroy it, got {iid_acf:.3f}"
    assert block_acf > iid_acf + 0.10, (
        f"block {block_acf:.3f} should retain materially more than iid {iid_acf:.3f}"
    )
    assert block_acf > original * 0.4


def test_longer_blocks_retain_more_structure():
    returns = _clustered_returns()
    rng = np.random.default_rng(1)

    short = _autocorrelation(
        np.abs(returns[stationary_block_indices(len(returns), 2000, rng, 3)])
    )
    long = _autocorrelation(
        np.abs(returns[stationary_block_indices(len(returns), 2000, rng, 40)])
    )
    assert long > short


def test_block_indices_are_in_range_and_contiguous_within_blocks():
    rng = np.random.default_rng(7)
    n, length = 500, 1200
    indices = stationary_block_indices(n, length, rng, expected_block=21)

    assert len(indices) == length
    assert indices.min() >= 0 and indices.max() < n

    # A meaningful share of consecutive pairs should step by exactly +1.
    steps = (indices[1:] - indices[:-1]) % n
    assert float((steps == 1).mean()) > 0.7


def test_block_indices_reproducible_with_a_seed():
    first = stationary_block_indices(400, 900, np.random.default_rng(3))
    second = stationary_block_indices(400, 900, np.random.default_rng(3))
    np.testing.assert_array_equal(first, second)


def test_empty_series_rejected():
    with pytest.raises(ValueError, match="empty"):
        stationary_block_indices(0, 10, np.random.default_rng(0))


# --- end to end, against real data ------------------------------------------


@pytest.mark.parametrize("kind", ["constant_mix", "walk_forward"])
def test_paired_comparison_integrity(ingested_db, kind):
    """Every window measures all three series over identical data."""
    if not _has_data(ingested_db, "RELIANCE"):
        pytest.skip("database not ingested")

    config = StrategyConfig(
        kind=kind,
        target_weights=EQUAL if kind == "constant_mix" else None,
        lookback_days=378,
    )
    result = bootstrap_backtest(
        ingested_db, UNIVERSE, config, window_years=3.0, seed=0
    )

    assert result.n_windows > 5
    for window in result.windows:
        assert set(window.metrics) == set(SERIES_KEYS), (
            f"window {window.index} is missing a series: {set(window.metrics)}"
        )
        assert window.trading_days > 0
        assert window.start_date < window.end_date

    # Window spans are strictly increasing and never duplicated.
    spans = [(w.start_date, w.end_date) for w in result.windows]
    assert spans == sorted(spans)
    assert len(set(spans)) == len(spans)

    assert result.win_rates.n_windows == result.n_windows


def test_summaries_cover_every_series_and_metric(ingested_db):
    if not _has_data(ingested_db, "RELIANCE"):
        pytest.skip("database not ingested")

    result = bootstrap_backtest(
        ingested_db, UNIVERSE,
        StrategyConfig(kind="constant_mix", target_weights=EQUAL),
        window_years=3.0, seed=0,
    )
    for key in SERIES_KEYS:
        assert key in result.summaries
        for metric in ("cagr", "sharpe_ratio", "sortino_ratio", "max_drawdown"):
            summary = result.summaries[key][metric]
            assert summary.n_windows == result.n_windows
            assert summary.min <= summary.p5 <= summary.median <= summary.p95 <= summary.max


def test_win_rates_are_paired_fractions(ingested_db):
    if not _has_data(ingested_db, "RELIANCE"):
        pytest.skip("database not ingested")

    result = bootstrap_backtest(
        ingested_db, UNIVERSE,
        StrategyConfig(kind="constant_mix", target_weights=EQUAL),
        window_years=3.0, seed=0,
    )
    rates = result.win_rates
    for value in (
        rates.vs_nifty50_cagr, rates.vs_nifty50_sharpe,
        rates.vs_buy_and_hold_cagr, rates.vs_buy_and_hold_sharpe,
    ):
        assert 0.0 <= value <= 1.0

    # Recompute the CAGR win rate directly from the per-window array.
    wins = sum(
        w.metrics["strategy"].cagr > w.metrics["buy_and_hold_nifty50"].cagr
        for w in result.windows
    )
    assert rates.vs_nifty50_cagr == pytest.approx(wins / len(result.windows))


def test_rolling_windows_are_reproducible(ingested_db):
    if not _has_data(ingested_db, "RELIANCE"):
        pytest.skip("database not ingested")

    config = StrategyConfig(kind="constant_mix", target_weights=EQUAL)
    first = bootstrap_backtest(ingested_db, UNIVERSE, config, window_years=3.0, seed=0)
    second = bootstrap_backtest(ingested_db, UNIVERSE, config, window_years=3.0, seed=0)

    assert first.n_windows == second.n_windows
    assert first.win_rates == second.win_rates
    for a, b in zip(first.windows, second.windows):
        assert a.metrics["strategy"].cagr == b.metrics["strategy"].cagr


def test_block_bootstrap_is_reproducible_and_paired(ingested_db):
    if not _has_data(ingested_db, "RELIANCE"):
        pytest.skip("database not ingested")

    config = StrategyConfig(kind="constant_mix", target_weights=EQUAL)
    kwargs = dict(
        window_years=3.0, method="block_bootstrap", n_resamples=15, seed=42
    )
    first = bootstrap_backtest(ingested_db, UNIVERSE, config, **kwargs)
    second = bootstrap_backtest(ingested_db, UNIVERSE, config, **kwargs)

    assert first.n_windows == second.n_windows == 15
    assert first.win_rates == second.win_rates
    for window in first.windows:
        assert set(window.metrics) == set(SERIES_KEYS)

    different = bootstrap_backtest(
        ingested_db, UNIVERSE, config,
        window_years=3.0, method="block_bootstrap", n_resamples=15, seed=7,
    )
    assert different.win_rates != first.win_rates


def test_walk_forward_reports_failures_and_turnover(ingested_db):
    if not _has_data(ingested_db, "RELIANCE"):
        pytest.skip("database not ingested")

    result = bootstrap_backtest(
        ingested_db, UNIVERSE,
        StrategyConfig(kind="walk_forward", lookback_days=378),
        window_years=3.0, seed=0,
    )
    assert result.total_rebalances > 0
    assert result.total_optimizer_failures >= 0
    assert result.average_turnover_per_rebalance > 0
    assert all(w.n_rebalances > 0 for w in result.windows)


def test_walk_forward_turns_over_more_than_constant_mix(ingested_db):
    if not _has_data(ingested_db, "RELIANCE"):
        pytest.skip("database not ingested")

    shared = dict(window_years=3.0, seed=0)
    constant = bootstrap_backtest(
        ingested_db, UNIVERSE,
        StrategyConfig(kind="constant_mix", target_weights=EQUAL), **shared,
    )
    walk = bootstrap_backtest(
        ingested_db, UNIVERSE,
        StrategyConfig(kind="walk_forward", lookback_days=378), **shared,
    )
    assert (
        walk.average_turnover_per_rebalance
        > constant.average_turnover_per_rebalance * 2
    )


def test_impossible_window_raises_with_guidance(ingested_db):
    if not _has_data(ingested_db, "RELIANCE"):
        pytest.skip("database not ingested")

    with pytest.raises(Exception) as excinfo:
        bootstrap_backtest(
            ingested_db, UNIVERSE,
            StrategyConfig(kind="constant_mix", target_weights=EQUAL),
            window_years=19.0,
        )
    assert "window_years" in str(excinfo.value)


# --- endpoint ---------------------------------------------------------------


def _post(client, **overrides):
    body = {
        "tickers": UNIVERSE,
        "strategy": {"kind": "constant_mix", "target_weights": EQUAL},
        "window_years": 3.0,
        "method": "rolling_windows",
        "include_windows": True,
    }
    body.update(overrides)
    return client.post("/api/backtest/bootstrap", json=body)


def test_endpoint_returns_win_rates_and_summaries(client):
    response = _post(client)
    if response.status_code == 422:
        pytest.skip("database not ingested")
    assert response.status_code == 200
    payload = response.json()

    assert payload["n_windows"] > 5
    assert set(payload["summaries"]) == set(SERIES_KEYS)
    assert 0.0 <= payload["win_rates"]["vs_nifty50_cagr"] <= 1.0
    assert len(payload["windows"]) == payload["n_windows"]
    for window in payload["windows"]:
        assert set(window["metrics"]) == set(SERIES_KEYS)


def test_endpoint_limitations_include_the_two_new_warnings(client):
    response = _post(client)
    if response.status_code == 422:
        pytest.skip("database not ingested")

    limitations = response.json()["limitations"]
    assert limitations == BOOTSTRAP_LIMITATIONS
    assert OVERLAPPING_WINDOWS_WARNING in limitations
    assert MULTIPLE_COMPARISONS_WARNING in limitations
    joined = " ".join(limitations).lower()
    assert "not independent samples" in joined
    assert "biased upward" in joined
    assert "transaction costs" in joined
    assert "correlation" in joined


def test_endpoint_walk_forward_config(client):
    response = _post(
        client,
        strategy={
            "kind": "walk_forward",
            "objective": "max_sharpe",
            "lookback_days": 378,
            "max_weight_per_asset": 0.35,
        },
    )
    if response.status_code == 422:
        pytest.skip("database not ingested")
    payload = response.json()

    assert payload["strategy_kind"] == "walk_forward"
    assert payload["total_rebalances"] > 0
    assert payload["average_turnover_per_rebalance"] > 0


def test_endpoint_can_omit_the_window_array(client):
    response = _post(client, include_windows=False)
    if response.status_code == 422:
        pytest.skip("database not ingested")
    assert response.json()["windows"] == []


def test_constant_mix_without_weights_rejected(client):
    response = _post(client, strategy={"kind": "constant_mix"})
    assert response.status_code == 422
    assert response.json()["error_code"] == "INVALID_WEIGHTS"


def test_bad_weights_rejected(client):
    response = _post(
        client,
        strategy={"kind": "constant_mix", "target_weights": {"RELIANCE": 0.9}},
    )
    assert response.status_code == 422


def test_survivorship_warning_is_attached_to_the_bootstrap_response(client):
    """The win-rate-vs-index headline is exactly what this warning qualifies."""
    from app.core.limitations import SURVIVORSHIP_BIAS_WARNING

    response = _post(client)
    if response.status_code == 422:
        pytest.skip("database not ingested")

    limitations = response.json()["limitations"]
    assert SURVIVORSHIP_BIAS_WARNING in limitations
    assert limitations[0] == SURVIVORSHIP_BIAS_WARNING


def test_survivorship_warning_only_where_the_benchmark_is_compared(client):
    """VaR and the optimiser make no index comparison, so they must not carry it."""
    from app.core.limitations import (
        BACKTEST_LIMITATIONS,
        BOOTSTRAP_LIMITATIONS,
        OPTIMIZER_LIMITATIONS,
        SURVIVORSHIP_BIAS_WARNING,
        VAR_LIMITATIONS,
    )

    assert SURVIVORSHIP_BIAS_WARNING in BACKTEST_LIMITATIONS
    assert SURVIVORSHIP_BIAS_WARNING in BOOTSTRAP_LIMITATIONS
    assert SURVIVORSHIP_BIAS_WARNING not in VAR_LIMITATIONS
    assert SURVIVORSHIP_BIAS_WARNING not in OPTIMIZER_LIMITATIONS


def test_benchmark_price_index_warning_accompanies_index_comparisons():
    """^NSEI excludes dividends while constituent returns include them."""
    from app.core.limitations import (
        BACKTEST_LIMITATIONS,
        BENCHMARK_PRICE_INDEX_WARNING,
        BOOTSTRAP_LIMITATIONS,
        OPTIMIZER_LIMITATIONS,
        VAR_LIMITATIONS,
    )

    assert BENCHMARK_PRICE_INDEX_WARNING in BACKTEST_LIMITATIONS
    assert BENCHMARK_PRICE_INDEX_WARNING in BOOTSTRAP_LIMITATIONS
    assert BENCHMARK_PRICE_INDEX_WARNING not in VAR_LIMITATIONS
    assert BENCHMARK_PRICE_INDEX_WARNING not in OPTIMIZER_LIMITATIONS
    assert "price* index" in BENCHMARK_PRICE_INDEX_WARNING
