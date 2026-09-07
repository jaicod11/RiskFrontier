"""Backtest against real ingested data, plus the endpoint contract."""

from __future__ import annotations

import datetime as dt

import pandas as pd
import pytest
from sqlalchemy import func, select

from app.core.limitations import (
    BACKTEST_LIMITATIONS,
    CORRELATION_BREAKDOWN_WARNING,
    HISTORICAL_ESTIMATE_WARNING,
    TRANSACTION_COST_WARNING,
)
from app.models import DailyPrice, Security
from app.services.backtest import (
    BENCHMARK_TICKER,
    InsufficientCoverageError,
    build_price_matrix,
    constant_mix_strategy,
    rebalance_calendar,
    run_backtest,
    run_strategy_with_baselines,
)
from app.services.returns import get_daily_returns

START = dt.date(2022, 1, 3)
END = dt.date(2026, 6, 30)
CAPITAL = 1_000_000.0

BASE_REQUEST = {
    "tickers": ["RELIANCE", "TCS", "HDFCBANK"],
    "target_weights": {"RELIANCE": 0.4, "TCS": 0.3, "HDFCBANK": 0.3},
    "start_date": START.isoformat(),
    "end_date": END.isoformat(),
    "initial_capital": CAPITAL,
    "rebalance_frequency": "monthly",
}


def _post(client, **overrides):
    body = dict(BASE_REQUEST)
    body.update(overrides)
    return client.post("/api/backtest/run", json=body)


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


# --- the benchmark ----------------------------------------------------------


def test_benchmark_is_ingested_and_flagged(ingested_db):
    security = ingested_db.scalar(
        select(Security).where(Security.ticker == BENCHMARK_TICKER)
    )
    if security is None:
        pytest.skip("benchmark not ingested")

    assert security.is_benchmark is True
    assert security.name == "Nifty 50"
    assert security.exchange == "NSE"

    bars = ingested_db.scalar(
        select(func.count(DailyPrice.id)).where(
            DailyPrice.security_id == security.id
        )
    )
    assert bars > 1000, "expected years of index history"


def test_constituents_are_not_flagged_as_benchmarks(ingested_db):
    if not _has_data(ingested_db, "RELIANCE"):
        pytest.skip("database not ingested")
    flagged = ingested_db.scalars(
        select(Security.ticker).where(Security.is_benchmark.is_(True))
    ).all()
    assert set(flagged) <= {BENCHMARK_TICKER}


def test_benchmark_returns_go_through_the_shared_layer(ingested_db):
    """^NSEI is not special-cased: same function as any stock."""
    if not _has_data(ingested_db, BENCHMARK_TICKER):
        pytest.skip("benchmark not ingested")

    series = get_daily_returns(ingested_db, BENCHMARK_TICKER, START, END)

    assert not series.empty
    assert series.name == BENCHMARK_TICKER
    assert isinstance(series.index, pd.DatetimeIndex)
    assert series.index.is_monotonic_increasing
    # An index moves less than a single stock, but it does move.
    assert 0.0 < float(series.std()) < 0.05


# --- price matrix and coverage ----------------------------------------------


def test_missing_coverage_raises_a_specific_actionable_error(ingested_db):
    """JIOFIN listed in Aug 2023, so a 2022 start cannot be covered."""
    if not _has_data(ingested_db, "JIOFIN"):
        pytest.skip("database not ingested")

    with pytest.raises(InsufficientCoverageError) as excinfo:
        build_price_matrix(
            ingested_db, ["RELIANCE", "JIOFIN"], dt.date(2022, 1, 3), END
        )
    message = str(excinfo.value)

    assert "JIOFIN" in message
    assert "2023-08-21" in message, "the actual available range must be named"
    assert "RELIANCE" not in message, "only the offending ticker should be named"
    assert "not shrunk automatically" in message


def test_end_date_beyond_available_data_also_raises(ingested_db):
    if not _has_data(ingested_db, "RELIANCE"):
        pytest.skip("database not ingested")
    with pytest.raises(InsufficientCoverageError, match="RELIANCE"):
        build_price_matrix(
            ingested_db, ["RELIANCE"], START, dt.date(2030, 1, 1)
        )


def test_price_matrix_reproduces_adj_close_for_a_clean_ticker(ingested_db):
    """Reconstruction from cleaned returns is lossless without anomalies."""
    if not _has_data(ingested_db, "RELIANCE"):
        pytest.skip("database not ingested")

    frame = build_price_matrix(ingested_db, ["RELIANCE"], START, END)
    security = ingested_db.scalar(
        select(Security).where(Security.ticker == "RELIANCE")
    )
    rows = ingested_db.execute(
        select(DailyPrice.date, DailyPrice.adj_close)
        .where(
            DailyPrice.security_id == security.id,
            DailyPrice.date >= START,
            DailyPrice.date <= END,
        )
        .order_by(DailyPrice.date)
    ).all()
    raw = pd.Series(
        [float(c) for _, c in rows],
        index=pd.DatetimeIndex([d for d, _ in rows]),
    )
    pd.testing.assert_series_equal(
        frame["RELIANCE"], raw, check_names=False, check_freq=False, rtol=1e-9
    )


def test_price_matrix_neutralises_the_tmpv_break(ingested_db):
    """A TMPV backtest must not book the demerger as a 40% loss."""
    if not _has_data(ingested_db, "TMPV"):
        pytest.skip("TMPV not ingested")

    start, end = dt.date(2025, 9, 1), dt.date(2025, 11, 30)
    frame = build_price_matrix(ingested_db, ["TMPV"], start, end)
    prices = frame["TMPV"]

    break_day = pd.Timestamp(2025, 10, 14)
    assert break_day in prices.index
    previous = prices.loc[:break_day].iloc[-2]
    assert prices.loc[break_day] == pytest.approx(previous), (
        "the demerger date should carry no price change in the cleaned series"
    )
    # Raw adj_close still shows the break, untouched.
    security = ingested_db.scalar(select(Security).where(Security.ticker == "TMPV"))
    raw = {
        d: float(c)
        for d, c in ingested_db.execute(
            select(DailyPrice.date, DailyPrice.adj_close).where(
                DailyPrice.security_id == security.id,
                DailyPrice.date.in_([dt.date(2025, 10, 13), dt.date(2025, 10, 14)]),
            )
        )
    }
    assert raw[dt.date(2025, 10, 14)] / raw[dt.date(2025, 10, 13)] - 1 < -0.35


# --- truncation invariance on real data -------------------------------------


def test_truncation_invariance_on_real_data(ingested_db):
    """The critical guarantee, end to end through the real data path."""
    if not _has_data(ingested_db, "RELIANCE"):
        pytest.skip("database not ingested")

    tickers = ["RELIANCE", "TCS", "HDFCBANK"]
    weights = {"RELIANCE": 0.4, "TCS": 0.3, "HDFCBANK": 0.3}
    short_end = dt.date(2025, 6, 30)
    long_end = dt.date(2026, 6, 30)

    short = run_strategy_with_baselines(
        ingested_db, tickers, weights, START, short_end, CAPITAL
    )
    long = run_strategy_with_baselines(
        ingested_db, tickers, weights, START, long_end, CAPITAL
    )

    overlap = short.strategy.values.index
    pd.testing.assert_series_equal(
        short.strategy.values,
        long.strategy.values.loc[overlap],
        check_exact=True,
    )
    pd.testing.assert_frame_equal(
        short.strategy.shares,
        long.strategy.shares.loc[overlap],
        check_exact=True,
    )
    pd.testing.assert_series_equal(
        short.strategy.costs, long.strategy.costs.loc[overlap], check_exact=True
    )


# --- endpoint contract ------------------------------------------------------


def test_endpoint_returns_strategy_and_both_baselines(client):
    response = _post(client)
    if response.status_code == 422:
        pytest.skip("database not ingested")
    assert response.status_code == 200
    payload = response.json()

    for key in ("strategy", "buy_and_hold_same_stocks", "buy_and_hold_nifty50"):
        series = payload[key]
        assert series is not None, f"{key} missing"
        assert len(series["values"]) == payload["trading_days"]
        assert series["values"][0]["date"] == payload["start_date"]
        assert series["values"][-1]["date"] == payload["end_date"]
        assert series["metrics"]["start_value"] > 0


def test_all_three_series_share_one_calendar(client):
    response = _post(client)
    if response.status_code == 422:
        pytest.skip("database not ingested")
    payload = response.json()

    dates = {
        key: [v["date"] for v in payload[key]["values"]]
        for key in ("strategy", "buy_and_hold_same_stocks", "buy_and_hold_nifty50")
    }
    assert dates["strategy"] == dates["buy_and_hold_same_stocks"]
    assert dates["strategy"] == dates["buy_and_hold_nifty50"]


def test_buy_and_hold_baseline_trades_once(client):
    response = _post(client)
    if response.status_code == 422:
        pytest.skip("database not ingested")
    payload = response.json()

    assert payload["buy_and_hold_same_stocks"]["metrics"]["n_rebalances"] == 1
    assert payload["buy_and_hold_nifty50"]["metrics"]["n_rebalances"] == 1
    # The monthly strategy rebalances many more times, and pays for it.
    # Each rebalance only trades the drift since the last one -- a few percent
    # of the book -- so the extra cost accumulates gradually rather than being
    # a multiple of the initial purchase. Assert the real relationship, not an
    # arbitrary multiplier.
    strategy = payload["strategy"]["metrics"]
    hold = payload["buy_and_hold_same_stocks"]["metrics"]

    assert strategy["n_rebalances"] > 40
    assert strategy["total_transaction_costs_inr"] > hold["total_transaction_costs_inr"]
    assert strategy["total_transaction_costs_pct"] > hold["total_transaction_costs_pct"]

    # Buy-and-hold's entire cost is the single day-one purchase.
    assert hold["total_transaction_costs_inr"] == pytest.approx(
        CAPITAL * 0.0015 * (1 - 0.0015), rel=1e-9
    )
    # Every rebalance after the first adds cost, but each is small.
    extra = strategy["total_transaction_costs_inr"] - hold["total_transaction_costs_inr"]
    per_rebalance = extra / (strategy["n_rebalances"] - 1)
    assert 0 < per_rebalance < CAPITAL * 0.0015


def test_quarterly_rebalances_less_often_than_monthly(client):
    monthly = _post(client, rebalance_frequency="monthly")
    if monthly.status_code == 422:
        pytest.skip("database not ingested")
    quarterly = _post(client, rebalance_frequency="quarterly")

    assert (
        quarterly.json()["strategy"]["metrics"]["n_rebalances"]
        < monthly.json()["strategy"]["metrics"]["n_rebalances"]
    )
    assert (
        quarterly.json()["strategy"]["metrics"]["total_transaction_costs_inr"]
        < monthly.json()["strategy"]["metrics"]["total_transaction_costs_inr"]
    )


def test_transaction_cost_override_is_honoured(client):
    cheap = _post(client, transaction_cost_bps=0)
    if cheap.status_code == 422:
        pytest.skip("database not ingested")
    dear = _post(client, transaction_cost_bps=100)

    assert cheap.json()["strategy"]["metrics"]["total_transaction_costs_inr"] == 0
    assert (
        dear.json()["strategy"]["metrics"]["end_value"]
        < cheap.json()["strategy"]["metrics"]["end_value"]
    )


def test_limitations_come_from_the_shared_module(client):
    response = _post(client)
    if response.status_code == 422:
        pytest.skip("database not ingested")

    limitations = response.json()["limitations"]
    assert limitations == BACKTEST_LIMITATIONS
    assert TRANSACTION_COST_WARNING in limitations
    assert CORRELATION_BREAKDOWN_WARNING in limitations
    assert HISTORICAL_ESTIMATE_WARNING in limitations
    joined = " ".join(limitations).lower()
    assert "one draw from history" in joined
    assert "transaction costs" in joined


def test_uncovered_window_returns_422_naming_the_ticker(client):
    response = _post(
        client,
        tickers=["RELIANCE", "JIOFIN"],
        target_weights={"RELIANCE": 0.5, "JIOFIN": 0.5},
        start_date="2022-01-03",
    )
    assert response.status_code == 422
    detail = str(response.json()["detail"])
    assert "JIOFIN" in detail
    assert "2023-08-21" in detail


def test_unknown_ticker_returns_404(client):
    response = _post(
        client,
        tickers=["RELIANCE", "NOTATICKER"],
        target_weights={"RELIANCE": 0.5, "NOTATICKER": 0.5},
    )
    assert response.status_code == 404


def test_weights_must_sum_to_one(client):
    response = _post(
        client, target_weights={"RELIANCE": 0.4, "TCS": 0.3, "HDFCBANK": 0.2}
    )
    assert response.status_code == 422
    assert "must sum to 1.0" in str(response.json()["detail"])


def test_missing_weight_for_a_ticker_is_rejected(client):
    response = _post(client, target_weights={"RELIANCE": 0.5, "TCS": 0.5})
    assert response.status_code == 422
    assert "HDFCBANK" in str(response.json()["detail"])


def test_inverted_dates_rejected(client):
    response = _post(client, start_date="2026-01-01", end_date="2022-01-01")
    assert response.status_code == 422


def test_metrics_are_computed_identically_for_all_three(ingested_db):
    """Same formulas, so the comparison is apples to apples."""
    if not _has_data(ingested_db, BENCHMARK_TICKER):
        pytest.skip("database not ingested")

    comparison = run_strategy_with_baselines(
        ingested_db,
        ["RELIANCE", "TCS", "HDFCBANK"],
        {"RELIANCE": 0.4, "TCS": 0.3, "HDFCBANK": 0.3},
        START, END, CAPITAL,
    )
    everything = [
        comparison.strategy_metrics,
        comparison.buy_and_hold_same_stocks_metrics,
        comparison.buy_and_hold_nifty50_metrics,
    ]
    for metrics in everything:
        assert metrics is not None
        assert metrics.start_value > 0
        assert metrics.annualised_volatility > 0
        assert -1.0 <= metrics.max_drawdown <= 0.0
        assert metrics.max_drawdown_peak_date <= metrics.max_drawdown_trough_date


def test_engine_accepts_a_custom_strategy_over_real_data(ingested_db):
    """The Phase 6 seam, exercised against the real price matrix."""
    if not _has_data(ingested_db, "RELIANCE"):
        pytest.skip("database not ingested")

    tickers = ["RELIANCE", "TCS", "HDFCBANK"]
    prices = build_price_matrix(ingested_db, tickers, START, END)
    schedule = rebalance_calendar([d.date() for d in prices.index], "quarterly")

    calls: list[dt.date] = []

    def best_trailing_month(as_of: dt.date, history: pd.DataFrame) -> dict[str, float]:
        calls.append(as_of)
        window = history.tail(21)
        if len(window) < 2:
            return {t: 1.0 / len(tickers) for t in tickers}
        trailing = window.iloc[-1] / window.iloc[0] - 1.0
        winner = str(trailing.idxmax())
        return {t: (1.0 if t == winner else 0.0) for t in tickers}

    run = run_backtest(prices, best_trailing_month, CAPITAL, schedule)

    assert calls == schedule
    assert run.n_rebalances == len(schedule)
    # A winner-takes-all strategy holds exactly one name after each rebalance.
    for day in schedule[1:]:
        held = run.shares.loc[pd.Timestamp(day)]
        assert (held > 0).sum() == 1, f"expected a single holding on {day}"

    baseline = run_backtest(prices, constant_mix_strategy(
        {t: 1.0 / len(tickers) for t in tickers}), CAPITAL, schedule)
    assert run.values.iloc[-1] != baseline.values.iloc[-1]
