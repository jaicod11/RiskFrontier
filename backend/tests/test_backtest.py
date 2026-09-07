"""Backtest engine mechanics, on synthetic prices — no database, no network."""

from __future__ import annotations

import datetime as dt

import numpy as np
import pandas as pd
import pytest

from app.services.backtest import (
    DEFAULT_TRANSACTION_COST_BPS,
    BacktestRun,
    StrategyContractError,
    compute_metrics,
    constant_mix_strategy,
    rebalance_calendar,
    run_backtest,
)

CAPITAL = 1_000_000.0


def _prices(n_days: int = 400, seed: int = 5, n_assets: int = 3) -> pd.DataFrame:
    """Random-walk prices on consecutive business days."""
    rng = np.random.default_rng(seed)
    index = pd.bdate_range("2020-01-01", periods=n_days, name="date")
    data = {}
    for i in range(n_assets):
        steps = rng.normal(0.0004, 0.012, n_days)
        data[f"T{i}"] = 100.0 * np.cumprod(1.0 + steps)
    return pd.DataFrame(data, index=index)


def _equal_weights(frame: pd.DataFrame) -> dict[str, float]:
    n = frame.shape[1]
    return {t: 1.0 / n for t in frame.columns}


def _schedule(frame: pd.DataFrame, frequency: str = "monthly") -> list[dt.date]:
    return rebalance_calendar([d.date() for d in frame.index], frequency)


# --- THE critical one: nothing after T may touch anything at or before T ----


def test_truncation_invariance():
    """Appending 30 more days must not change a single earlier value.

    This is the mechanical proof of no lookahead. Every field is compared
    exactly — not approximately — for every day up to the truncation point.
    """
    full = _prices(n_days=400)
    cut = 300
    short = full.iloc[:cut]

    strategy = constant_mix_strategy(_equal_weights(full))

    short_run = run_backtest(
        short, strategy, CAPITAL, _schedule(short), DEFAULT_TRANSACTION_COST_BPS
    )
    long_run = run_backtest(
        full, strategy, CAPITAL, _schedule(full), DEFAULT_TRANSACTION_COST_BPS
    )

    pd.testing.assert_series_equal(
        short_run.values, long_run.values.iloc[:cut], check_exact=True
    )
    pd.testing.assert_frame_equal(
        short_run.shares, long_run.shares.iloc[:cut], check_exact=True
    )
    pd.testing.assert_series_equal(
        short_run.cash, long_run.cash.iloc[:cut], check_exact=True
    )
    pd.testing.assert_series_equal(
        short_run.costs, long_run.costs.iloc[:cut], check_exact=True
    )
    assert short_run.rebalance_dates == [
        d for d in long_run.rebalance_dates if d <= short.index[-1].date()
    ]


@pytest.mark.parametrize("frequency", ["monthly", "quarterly"])
def test_truncation_invariance_across_frequencies(frequency):
    full = _prices(n_days=500, seed=9)
    cut = 377
    short = full.iloc[:cut]
    strategy = constant_mix_strategy(_equal_weights(full))

    short_run = run_backtest(short, strategy, CAPITAL, _schedule(short, frequency))
    long_run = run_backtest(full, strategy, CAPITAL, _schedule(full, frequency))

    pd.testing.assert_series_equal(
        short_run.values, long_run.values.iloc[:cut], check_exact=True
    )


def test_strategy_never_sees_data_after_the_as_of_date():
    """Structural check: capture what the callable is actually handed."""
    frame = _prices(n_days=250)
    seen: list[tuple[dt.date, dt.date]] = []

    def spy(as_of: dt.date, history: pd.DataFrame) -> dict[str, float]:
        seen.append((as_of, history.index[-1].date()))
        return _equal_weights(frame)

    run_backtest(frame, spy, CAPITAL, _schedule(frame))

    assert seen, "the strategy should have been called"
    for as_of, latest_row in seen:
        assert latest_row == as_of, (
            f"strategy called for {as_of} but handed data through {latest_row}"
        )


def test_execution_uses_that_days_close_only():
    """Change tomorrow's price wildly; today's trade must be unaffected."""
    frame = _prices(n_days=120, seed=3)
    schedule = _schedule(frame)
    rebalance_day = schedule[3]
    position = list(frame.index).index(pd.Timestamp(rebalance_day))

    strategy = constant_mix_strategy(_equal_weights(frame))
    baseline = run_backtest(frame, strategy, CAPITAL, schedule)

    # A 10x spike on the very next day — detectable if it leaked backwards.
    tampered = frame.copy()
    tampered.iloc[position + 1] = tampered.iloc[position + 1] * 10.0
    tampered_run = run_backtest(tampered, strategy, CAPITAL, schedule)

    np.testing.assert_array_equal(
        baseline.shares.iloc[position].to_numpy(),
        tampered_run.shares.iloc[position].to_numpy(),
    )
    assert baseline.values.iloc[position] == tampered_run.values.iloc[position]
    # ...and the change genuinely would have been visible one day later.
    assert baseline.values.iloc[position + 1] != tampered_run.values.iloc[position + 1]


def test_rebalance_shares_match_a_hand_computation():
    """Shares bought = target value / that day's close, net of costs."""
    index = pd.bdate_range("2021-01-04", periods=3, name="date")
    frame = pd.DataFrame(
        {"AAA": [100.0, 200.0, 400.0], "BBB": [50.0, 50.0, 50.0]}, index=index
    )
    weights = {"AAA": 0.6, "BBB": 0.4}
    first_day = index[0].date()

    run = run_backtest(
        frame, constant_mix_strategy(weights), CAPITAL, [first_day],
        transaction_cost_bps=0.0,
    )

    # With zero costs, day one buys 600,000 of AAA at 100 and 400,000 of BBB at 50.
    assert run.shares.iloc[0]["AAA"] == pytest.approx(6_000.0)
    assert run.shares.iloc[0]["BBB"] == pytest.approx(8_000.0)
    assert run.values.iloc[0] == pytest.approx(CAPITAL)
    # Day two: AAA doubled, BBB flat -> 6000*200 + 8000*50 = 1,600,000.
    assert run.values.iloc[1] == pytest.approx(1_600_000.0)
    # Day three: AAA doubled again -> 6000*400 + 8000*50 = 2,800,000.
    assert run.values.iloc[2] == pytest.approx(2_800_000.0)
    # No trading after day one, so shares never move.
    assert run.shares.nunique().to_dict() == {"AAA": 1, "BBB": 1}


# --- costs ------------------------------------------------------------------


def test_costs_are_charged_only_on_rebalance_days():
    frame = _prices(n_days=300)
    schedule = _schedule(frame)
    run = run_backtest(frame, constant_mix_strategy(_equal_weights(frame)), CAPITAL, schedule)

    scheduled = {pd.Timestamp(d) for d in schedule}
    for timestamp, cost in run.costs.items():
        if timestamp in scheduled:
            assert cost > 0, f"expected a cost on rebalance day {timestamp.date()}"
        else:
            assert cost == 0.0, f"cost charged on a non-rebalance day {timestamp.date()}"


def test_no_trading_between_rebalances():
    """Share counts change only on rebalance days; value drifts in between."""
    frame = _prices(n_days=200)
    schedule = _schedule(frame)
    run = run_backtest(frame, constant_mix_strategy(_equal_weights(frame)), CAPITAL, schedule)

    scheduled = {pd.Timestamp(d) for d in schedule}
    previous = None
    for timestamp, row in run.shares.iterrows():
        if previous is not None and timestamp not in scheduled:
            np.testing.assert_array_equal(row.to_numpy(), previous)
        previous = row.to_numpy()


def test_initial_purchase_cost_matches_the_bps():
    frame = _prices(n_days=50)
    first_day = frame.index[0].date()
    run = run_backtest(
        frame, constant_mix_strategy(_equal_weights(frame)), CAPITAL,
        [first_day], transaction_cost_bps=15.0,
    )
    # Costs are paid out of the same pot being invested, so the engine invests
    # capital NET of the cost rather than the full amount. Turnover is
    # therefore (C - C*rate), not C, and the cost is C * rate * (1 - rate) --
    # 1,497.75 here, not a naive 1,500. This pins that semantics exactly.
    rate = 0.0015
    expected_cost = CAPITAL * rate * (1 - rate)
    assert run.costs.iloc[0] == pytest.approx(expected_cost, rel=1e-12)
    assert run.values.iloc[0] == pytest.approx(CAPITAL - expected_cost)

    # No money is created or destroyed: what left the pot is exactly the cost.
    invested = float((run.shares.iloc[0] * frame.iloc[0]).sum())
    assert invested + run.cash.iloc[0] + run.costs.iloc[0] == pytest.approx(CAPITAL)


def test_zero_cost_setting_charges_nothing():
    frame = _prices(n_days=200)
    run = run_backtest(
        frame, constant_mix_strategy(_equal_weights(frame)), CAPITAL,
        _schedule(frame), transaction_cost_bps=0.0,
    )
    assert run.total_cost == 0.0


def test_higher_costs_reduce_final_value():
    frame = _prices(n_days=400)
    schedule = _schedule(frame)
    strategy = constant_mix_strategy(_equal_weights(frame))

    cheap = run_backtest(frame, strategy, CAPITAL, schedule, transaction_cost_bps=1.0)
    dear = run_backtest(frame, strategy, CAPITAL, schedule, transaction_cost_bps=50.0)

    assert dear.values.iloc[-1] < cheap.values.iloc[-1]
    assert dear.total_cost > cheap.total_cost


def test_value_accounting_is_exact():
    """value must always equal cash + shares . prices, to the cent."""
    frame = _prices(n_days=300)
    run = run_backtest(frame, constant_mix_strategy(_equal_weights(frame)), CAPITAL, _schedule(frame))

    recomputed = (run.shares * frame).sum(axis=1) + run.cash
    pd.testing.assert_series_equal(
        run.values, recomputed, check_names=False, rtol=1e-12
    )


# --- buy and hold -----------------------------------------------------------


@pytest.mark.parametrize("n_days", [50, 400, 1500])
def test_buy_and_hold_executes_exactly_one_trade(n_days):
    """However long the period, holding trades once."""
    frame = _prices(n_days=n_days)
    run = run_backtest(
        frame, constant_mix_strategy(_equal_weights(frame)), CAPITAL,
        [frame.index[0].date()],
    )
    assert run.n_rebalances == 1
    assert (run.costs.iloc[1:] == 0).all()
    assert run.costs.iloc[0] > 0
    assert run.shares.nunique().max() == 1  # never changes


def test_buy_and_hold_weights_drift():
    """The whole point of not rebalancing."""
    index = pd.bdate_range("2021-01-04", periods=2, name="date")
    frame = pd.DataFrame(
        {"AAA": [100.0, 300.0], "BBB": [100.0, 100.0]}, index=index
    )
    run = run_backtest(
        frame, constant_mix_strategy({"AAA": 0.5, "BBB": 0.5}), CAPITAL,
        [index[0].date()], transaction_cost_bps=0.0,
    )
    final_values = run.shares.iloc[-1] * frame.iloc[-1]
    weights = final_values / final_values.sum()
    assert weights["AAA"] == pytest.approx(0.75)  # 3x vs flat -> 75/25
    assert weights["BBB"] == pytest.approx(0.25)


# --- the strategy seam ------------------------------------------------------


def test_a_different_strategy_plugs_into_the_same_engine():
    """Momentum: 100% into whichever ticker had the best trailing month.

    Proves the loop is strategy-agnostic — Phase 6 can supply its own callable
    without this engine changing.
    """
    index = pd.bdate_range("2021-01-01", periods=90, name="date")
    # AAA wins the first stretch, BBB the second.
    aaa = np.concatenate([np.linspace(100, 200, 45), np.linspace(200, 190, 45)])
    bbb = np.concatenate([np.linspace(100, 95, 45), np.linspace(95, 300, 45)])
    frame = pd.DataFrame({"AAA": aaa, "BBB": bbb}, index=index)

    def momentum(as_of: dt.date, history: pd.DataFrame) -> dict[str, float]:
        window = history.tail(21)
        if len(window) < 2:
            return {"AAA": 0.5, "BBB": 0.5}
        trailing = window.iloc[-1] / window.iloc[0] - 1.0
        winner = str(trailing.idxmax())
        return {t: (1.0 if t == winner else 0.0) for t in history.columns}

    schedule = _schedule(frame)
    run = run_backtest(frame, momentum, CAPITAL, schedule, transaction_cost_bps=0.0)

    # Early rebalances should hold AAA; late ones should have rotated to BBB.
    early = run.shares.loc[pd.Timestamp(schedule[1])]
    late = run.shares.iloc[-1]
    assert early["AAA"] > 0 and early["BBB"] == 0
    assert late["BBB"] > 0 and late["AAA"] == 0


def test_strategy_returning_bad_weights_fails_loudly():
    frame = _prices(n_days=60)

    def broken(as_of, history):
        return {"T0": 0.5}  # sums to 0.5

    with pytest.raises(StrategyContractError, match="sum to"):
        run_backtest(frame, broken, CAPITAL, _schedule(frame))


def test_strategy_naming_an_unknown_ticker_fails_loudly():
    frame = _prices(n_days=60)

    def broken(as_of, history):
        return {"NOPE": 1.0}

    with pytest.raises(StrategyContractError, match="not in the backtest"):
        run_backtest(frame, broken, CAPITAL, _schedule(frame))


def test_constant_mix_rejects_weights_that_do_not_sum_to_one():
    with pytest.raises(ValueError, match="must sum to 1.0"):
        constant_mix_strategy({"AAA": 0.5, "BBB": 0.3})


# --- rebalance calendar -----------------------------------------------------


def test_monthly_calendar_picks_first_traded_day_of_each_month():
    index = pd.bdate_range("2021-01-01", periods=200, name="date")
    dates = [d.date() for d in index]
    schedule = rebalance_calendar(dates, "monthly")

    months = {(d.year, d.month) for d in dates}
    assert len(schedule) == len(months)
    for day in schedule:
        earlier_same_month = [
            d for d in dates if (d.year, d.month) == (day.year, day.month) and d < day
        ]
        assert not earlier_same_month, f"{day} is not the first traded day"


def test_quarterly_calendar_has_roughly_a_quarter_of_the_dates():
    index = pd.bdate_range("2020-01-01", periods=1000, name="date")
    dates = [d.date() for d in index]
    monthly = rebalance_calendar(dates, "monthly")
    quarterly = rebalance_calendar(dates, "quarterly")

    assert len(quarterly) < len(monthly)
    assert set(quarterly).issubset(set(monthly))
    for day in quarterly:
        if day != dates[0]:
            assert day.month in (1, 4, 7, 10)


def test_first_day_is_always_a_rebalance():
    """Even mid-month: that is the initial purchase."""
    index = pd.bdate_range("2021-02-17", periods=100, name="date")
    dates = [d.date() for d in index]
    assert rebalance_calendar(dates, "monthly")[0] == dates[0]


def test_unknown_frequency_is_rejected():
    dates = [d.date() for d in pd.bdate_range("2021-01-01", periods=10)]
    with pytest.raises(ValueError, match="Unsupported rebalance_frequency"):
        rebalance_calendar(dates, "weekly")


# --- metrics, hand-verified -------------------------------------------------


def _run_from_values(values: list[float]) -> BacktestRun:
    index = pd.bdate_range("2021-01-04", periods=len(values), name="date")
    zeros = pd.Series(0.0, index=index)
    return BacktestRun(
        values=pd.Series(values, index=index, dtype=float),
        shares=pd.DataFrame(index=index),
        cash=zeros.copy(),
        costs=zeros.copy(),
        rebalance_dates=[index[0].date()],
    )


def test_max_drawdown_matches_a_hand_computation():
    # Peak 120 on day 2, trough 90 on day 3 -> 90/120 - 1 = -0.25.
    run = _run_from_values([100.0, 120.0, 90.0, 150.0, 140.0])
    metrics = compute_metrics(run, initial_capital=100.0)

    assert metrics.max_drawdown == pytest.approx(-0.25)
    assert metrics.max_drawdown_peak_date == run.values.index[1].date()
    assert metrics.max_drawdown_trough_date == run.values.index[2].date()


def test_max_drawdown_is_zero_for_a_monotonic_rise():
    run = _run_from_values([100.0, 110.0, 120.0, 130.0])
    assert compute_metrics(run, 100.0).max_drawdown == pytest.approx(0.0)


def test_sharpe_matches_a_hand_computation():
    values = [100.0, 110.0, 99.0, 118.8]  # returns of +10%, -10%, +20%
    run = _run_from_values(values)
    risk_free = 0.065

    returns = np.array([0.10, -0.10, 0.20])
    annualised_return = returns.mean() * 252
    annualised_vol = returns.std(ddof=1) * np.sqrt(252)
    expected = (annualised_return - risk_free) / annualised_vol

    metrics = compute_metrics(run, 100.0, risk_free_rate=risk_free)
    assert metrics.annualised_return == pytest.approx(annualised_return)
    assert metrics.annualised_volatility == pytest.approx(annualised_vol)
    assert metrics.sharpe_ratio == pytest.approx(expected)


def test_sortino_matches_a_hand_computation():
    values = [100.0, 110.0, 99.0, 118.8]
    run = _run_from_values(values)
    risk_free = 0.065

    returns = np.array([0.10, -0.10, 0.20])
    daily_mar = risk_free / 252
    shortfall = np.minimum(returns - daily_mar, 0.0)
    downside_deviation = np.sqrt(np.mean(shortfall**2)) * np.sqrt(252)
    expected = (returns.mean() * 252 - risk_free) / downside_deviation

    metrics = compute_metrics(run, 100.0, risk_free_rate=risk_free)
    assert metrics.sortino_ratio == pytest.approx(expected)


def test_sortino_exceeds_sharpe_when_upside_dominates():
    """Downside deviation ignores the big up days that inflate total vol."""
    run = _run_from_values([100.0, 110.0, 99.0, 118.8])
    metrics = compute_metrics(run, 100.0)
    assert metrics.sortino_ratio > metrics.sharpe_ratio


def test_cagr_matches_a_hand_computation():
    index = pd.DatetimeIndex(
        [dt.date(2020, 1, 1), dt.date(2022, 1, 1)], name="date"
    )
    run = BacktestRun(
        values=pd.Series([100.0, 121.0], index=index),
        shares=pd.DataFrame(index=index),
        cash=pd.Series(0.0, index=index),
        costs=pd.Series(0.0, index=index),
        rebalance_dates=[index[0].date()],
    )
    years = (index[-1] - index[0]).days / 365.25
    assert compute_metrics(run, 100.0).cagr == pytest.approx(
        (121.0 / 100.0) ** (1 / years) - 1
    )


def test_cost_metrics_are_reported_in_rupees_and_percent():
    frame = _prices(n_days=300)
    run = run_backtest(
        frame, constant_mix_strategy(_equal_weights(frame)), CAPITAL, _schedule(frame)
    )
    metrics = compute_metrics(run, CAPITAL)

    assert metrics.total_transaction_costs_inr == pytest.approx(run.total_cost)
    assert metrics.total_transaction_costs_pct == pytest.approx(
        run.total_cost / CAPITAL * 100
    )
    assert metrics.n_rebalances == run.n_rebalances > 1
