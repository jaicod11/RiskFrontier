"""Walk-forward strategy: no lookahead, graceful failure, honest telemetry."""

from __future__ import annotations

import datetime as dt

import numpy as np
import pandas as pd
import pytest

from app.services import strategies as strategies_module
from app.services.backtest import (
    DEFAULT_TRANSACTION_COST_BPS,
    constant_mix_strategy,
    rebalance_calendar,
    run_backtest,
)
from app.services.strategies import (
    InsufficientLookbackError,
    WalkForwardStrategy,
    average_turnover,
    rebalance_turnover,
    walk_forward_optimized_strategy,
)

CAPITAL = 1_000_000.0
LOOKBACK = 120


def _prices(n_days: int = 700, seed: int = 11, n_assets: int = 4) -> pd.DataFrame:
    rng = np.random.default_rng(seed)
    index = pd.bdate_range("2018-01-01", periods=n_days, name="date")
    data = {}
    for i in range(n_assets):
        drift = 0.0002 + 0.0002 * i
        steps = rng.normal(drift, 0.010 + 0.002 * i, n_days)
        data[f"T{i}"] = 100.0 * np.cumprod(1.0 + steps)
    return pd.DataFrame(data, index=index)


def _split(frame: pd.DataFrame, warmup: int = LOOKBACK):
    return frame.iloc[:warmup], frame.iloc[warmup:]


def _schedule(frame: pd.DataFrame, frequency: str = "monthly") -> list[dt.date]:
    return rebalance_calendar([d.date() for d in frame.index], frequency)


def _strategy(warmup: pd.DataFrame, **kwargs) -> WalkForwardStrategy:
    params = {
        "lookback_days": LOOKBACK,
        "max_weight_per_asset": 0.5,
        "objective": "max_sharpe",
        "seed": 0,
    }
    params.update(kwargs)
    return walk_forward_optimized_strategy(warmup, **params)


# --- no lookahead -----------------------------------------------------------


def test_truncation_invariance_for_walk_forward():
    """Re-optimising must not leak future data through the optimiser.

    Same byte-identical assertion as Phase 5, now with a strategy that fits a
    model at every rebalance date.
    """
    full = _prices(n_days=700)
    warmup, live = _split(full)
    cut = 300
    short = live.iloc[:cut]

    short_run = run_backtest(
        short, _strategy(warmup), CAPITAL, _schedule(short),
        DEFAULT_TRANSACTION_COST_BPS,
    )
    long_run = run_backtest(
        live, _strategy(warmup), CAPITAL, _schedule(live),
        DEFAULT_TRANSACTION_COST_BPS,
    )

    pd.testing.assert_series_equal(
        short_run.values, long_run.values.iloc[:cut], check_exact=True
    )
    pd.testing.assert_frame_equal(
        short_run.shares, long_run.shares.iloc[:cut], check_exact=True
    )
    pd.testing.assert_series_equal(
        short_run.costs, long_run.costs.iloc[:cut], check_exact=True
    )


def test_optimizer_only_ever_sees_data_up_to_the_rebalance_date(monkeypatch):
    """Spy on the optimiser itself, not just on the rebalance callable."""
    full = _prices(n_days=500)
    warmup, live = _split(full)

    seen: list[pd.Timestamp] = []
    original = strategies_module.max_sharpe_portfolio

    def spy(returns_df, *args, **kwargs):
        seen.append(returns_df.index.max())
        return original(returns_df, *args, **kwargs)

    monkeypatch.setattr(strategies_module, "max_sharpe_portfolio", spy)

    strategy = _strategy(warmup)
    schedule = _schedule(live)
    run_backtest(live, strategy, CAPITAL, schedule)

    assert seen, "the optimiser should have been called"
    assert len(seen) == len(schedule)
    for latest, as_of in zip(seen, schedule):
        assert latest.date() <= as_of, (
            f"optimiser saw data through {latest.date()} when rebalancing on {as_of}"
        )


def test_trailing_window_is_exactly_lookback_days():
    full = _prices(n_days=500)
    warmup, live = _split(full)
    strategy = _strategy(warmup)

    windows: list[int] = []
    original_window = strategy._trailing_window

    def record(history):
        window = original_window(history)
        windows.append(len(window))
        return window

    strategy._trailing_window = record
    run_backtest(live, strategy, CAPITAL, _schedule(live))

    # lookback_days returns require lookback_days + 1 prices.
    assert windows
    assert set(windows) == {LOOKBACK + 1}


def test_warmup_frame_contains_no_future_data():
    """Structural: the strategy holds nothing dated at or after the start."""
    full = _prices(n_days=400)
    warmup, live = _split(full)
    strategy = _strategy(warmup)

    assert strategy.warmup_prices.index.max() < live.index.min()


# --- insufficient lookback --------------------------------------------------


def test_short_warmup_raises_a_specific_error():
    full = _prices(n_days=300)
    warmup, _ = _split(full, warmup=30)

    with pytest.raises(InsufficientLookbackError) as excinfo:
        _strategy(warmup, lookback_days=200)

    message = str(excinfo.value)
    assert "200 trading days" in message
    assert "only 30" in message
    assert str(warmup.index[0].date()) in message
    assert "not shortened automatically" in message


def test_empty_warmup_raises():
    with pytest.raises(InsufficientLookbackError):
        _strategy(pd.DataFrame(), lookback_days=10)


def test_invalid_objective_rejected():
    warmup, _ = _split(_prices(n_days=300))
    with pytest.raises(ValueError, match="objective must be one of"):
        _strategy(warmup, objective="max_return")


# --- optimiser failures -----------------------------------------------------


def test_optimizer_failure_holds_previous_weights(monkeypatch):
    full = _prices(n_days=500)
    warmup, live = _split(full)
    schedule = _schedule(live)
    fail_on = set(schedule[3:5])

    original = strategies_module.max_sharpe_portfolio
    calls = {"n": 0}

    def flaky(returns_df, *args, **kwargs):
        calls["n"] += 1
        if calls["n"] - 1 in (3, 4):
            raise RuntimeError("simulated non-convergence")
        return original(returns_df, *args, **kwargs)

    monkeypatch.setattr(strategies_module, "max_sharpe_portfolio", flaky)

    strategy = _strategy(warmup)
    run_backtest(live, strategy, CAPITAL, schedule)

    assert strategy.telemetry.n_optimizer_failures == 2
    assert len(strategy.telemetry.failure_dates) == 2
    assert strategy.telemetry.n_rebalances == len(schedule)

    # The held weights equal the last successful ones.
    failed_dates = strategy.telemetry.failure_dates
    previous_date = schedule[schedule.index(failed_dates[0]) - 1]
    assert (
        strategy.telemetry.weights_by_date[failed_dates[0]]
        == strategy.telemetry.weights_by_date[previous_date]
    )
    assert 0 < strategy.telemetry.failure_rate < 1


def test_failure_on_the_very_first_rebalance_falls_back_to_equal_weight(monkeypatch):
    full = _prices(n_days=400)
    warmup, live = _split(full)

    monkeypatch.setattr(
        strategies_module, "max_sharpe_portfolio",
        lambda *a, **k: (_ for _ in ()).throw(RuntimeError("always fails")),
    )
    strategy = _strategy(warmup)
    schedule = _schedule(live)
    run = run_backtest(live, strategy, CAPITAL, schedule)

    assert strategy.telemetry.n_optimizer_failures == len(schedule)
    first = strategy.telemetry.weights_by_date[schedule[0]]
    assert len(first) == 4
    assert all(w == pytest.approx(0.25) for w in first.values())
    # The run completed rather than crashing.
    assert len(run.values) == len(live)


def test_a_total_optimizer_outage_still_produces_a_run(monkeypatch):
    full = _prices(n_days=400)
    warmup, live = _split(full)
    monkeypatch.setattr(
        strategies_module, "max_sharpe_portfolio",
        lambda *a, **k: (_ for _ in ()).throw(ValueError("nope")),
    )
    run = run_backtest(live, _strategy(warmup), CAPITAL, _schedule(live))
    assert np.isfinite(run.values.to_numpy()).all()


# --- objectives and telemetry ----------------------------------------------


@pytest.mark.parametrize("objective", ["max_sharpe", "min_variance"])
def test_both_objectives_run(objective):
    full = _prices(n_days=500)
    warmup, live = _split(full)
    strategy = _strategy(warmup, objective=objective)
    run = run_backtest(live, strategy, CAPITAL, _schedule(live))

    assert strategy.telemetry.n_rebalances > 5
    assert strategy.telemetry.n_optimizer_failures == 0
    assert np.isfinite(run.values.to_numpy()).all()


def test_weights_respect_the_cap_at_every_rebalance():
    full = _prices(n_days=500)
    warmup, live = _split(full)
    strategy = _strategy(warmup, max_weight_per_asset=0.4)
    run_backtest(live, strategy, CAPITAL, _schedule(live))

    for as_of, weights in strategy.telemetry.weights_by_date.items():
        assert sum(weights.values()) == pytest.approx(1.0, abs=1e-6), as_of
        assert max(weights.values()) <= 0.4 + 1e-6, as_of
        assert min(weights.values()) >= -1e-9, as_of


def test_walk_forward_turns_over_far_more_than_constant_mix():
    """The whole point of reporting turnover: re-optimising trades a lot."""
    full = _prices(n_days=700)
    warmup, live = _split(full)
    schedule = _schedule(live)

    wf_run = run_backtest(live, _strategy(warmup), CAPITAL, schedule)
    equal = {t: 0.25 for t in live.columns}
    cm_run = run_backtest(live, constant_mix_strategy(equal), CAPITAL, schedule)

    wf_turnover = average_turnover(wf_run, live)
    cm_turnover = average_turnover(cm_run, live)

    assert wf_turnover > cm_turnover * 3, (
        f"walk-forward {wf_turnover:.4f} vs constant-mix {cm_turnover:.4f}"
    )
    assert wf_run.total_cost > cm_run.total_cost


def test_turnover_is_measured_only_on_rebalance_days():
    full = _prices(n_days=400)
    warmup, live = _split(full)
    schedule = _schedule(live)
    run = run_backtest(live, _strategy(warmup), CAPITAL, schedule)

    turnover = rebalance_turnover(run, live)
    assert len(turnover) == len(schedule)
    assert set(turnover.index.date) == set(schedule)
    assert (turnover >= 0).all()


def test_initial_purchase_turnover_is_one():
    """Buying the book from cash turns over exactly the portfolio once."""
    full = _prices(n_days=200)
    warmup, live = _split(full)
    equal = {t: 0.25 for t in live.columns}
    run = run_backtest(
        live, constant_mix_strategy(equal), CAPITAL, [live.index[0].date()],
        transaction_cost_bps=0.0,
    )
    turnover = rebalance_turnover(run, live)
    assert float(turnover.iloc[0]) == pytest.approx(1.0, abs=1e-9)


# --- caching correctness ----------------------------------------------------


def test_shared_cache_gives_identical_weights():
    """The cache must be an optimisation, never a change in behaviour."""
    full = _prices(n_days=600)
    warmup, live = _split(full)
    schedule = _schedule(live)

    uncached = _strategy(warmup)
    run_backtest(live, uncached, CAPITAL, schedule)

    shared: dict = {}
    cached = walk_forward_optimized_strategy(
        warmup, lookback_days=LOOKBACK, max_weight_per_asset=0.5,
        objective="max_sharpe", seed=0, cache=shared,
    )
    run_backtest(live, cached, CAPITAL, schedule)

    assert uncached.telemetry.weights_by_date == cached.telemetry.weights_by_date
    assert shared, "the cache should have been populated"


def test_warmup_with_mismatched_columns_fails_loudly():
    """Regression: a stray column used to empty the trailing window silently.

    The block bootstrap passed a warmup frame that still carried the benchmark
    column. On concat it went all-NaN across the engine-supplied rows, dropna()
    removed them, and the strategy degraded to "hold previous" for ~64% of
    rebalances while reporting nothing unusual. It must fail instead.
    """
    full = _prices(n_days=400)
    warmup, live = _split(full)
    contaminated = warmup.copy()
    contaminated["EXTRA"] = 100.0

    strategy = _strategy(contaminated)
    with pytest.raises(ValueError, match="warmup columns do not match"):
        run_backtest(live, strategy, CAPITAL, _schedule(live))


def test_block_bootstrap_walk_forward_has_no_spurious_failures(ingested_db):
    """The same regression, through the real bootstrap path."""
    from sqlalchemy import func, select

    from app.models import DailyPrice, Security
    from app.services.bootstrap import StrategyConfig, bootstrap_backtest

    security = ingested_db.scalar(select(Security).where(Security.ticker == "RELIANCE"))
    if security is None or not ingested_db.scalar(
        select(func.count(DailyPrice.id)).where(DailyPrice.security_id == security.id)
    ):
        pytest.skip("database not ingested")

    result = bootstrap_backtest(
        ingested_db,
        ["RELIANCE", "TCS", "HDFCBANK", "INFY"],
        StrategyConfig(kind="walk_forward", lookback_days=378),
        window_years=3.0, method="block_bootstrap", n_resamples=5, seed=0,
    )
    assert result.total_rebalances > 0
    assert result.total_optimizer_failures == 0, (
        f"{result.total_optimizer_failures}/{result.total_rebalances} rebalances "
        "failed — the trailing window is probably being emptied"
    )
