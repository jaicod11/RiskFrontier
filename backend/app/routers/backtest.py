"""Backtesting endpoints."""

from __future__ import annotations

import logging

from fastapi import APIRouter, Depends
from sqlalchemy.orm import Session

from app.core.db import get_db
from app.core.errors import ErrorResponse
from app.schemas.common import DataWindow
from app.schemas.bootstrap import (
    BootstrapRequest,
    BootstrapResponse,
    MetricSummaryOut,
    WindowMetricsOut,
    WindowResultOut,
    WinRatesOut,
)
from app.services.bootstrap import StrategyConfig, bootstrap_backtest
from app.schemas.backtest import (
    BacktestRequest,
    BacktestResponse,
    BacktestSeries,
    PerformanceMetrics,
    ValuePoint,
)
from app.services.backtest import (
    BacktestMetrics,
    BacktestRun,
    run_strategy_with_baselines,
)

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api/backtest", tags=["backtest"])

_DESCRIPTIONS = {
    "strategy": (
        "Constant-mix strategy: rebalanced back to the target weights at each "
        "scheduled date, paying transaction costs on every trade."
    ),
    "buy_and_hold_same_stocks": (
        "The same holdings bought once on day one and never rebalanced. Weights "
        "drift with the market; only the initial purchase incurs a cost."
    ),
    "buy_and_hold_nifty50": (
        "The Nifty 50 index bought once on day one and held. The passive "
        "benchmark: did any of this beat an index fund?"
    ),
}


def _metrics(metrics: BacktestMetrics) -> PerformanceMetrics:
    fields = dict(vars(metrics))
    fields["start_value_inr"] = fields.pop("start_value")
    fields["end_value_inr"] = fields.pop("end_value")
    return PerformanceMetrics(**fields)


def _series(label: str, run: BacktestRun, metrics: BacktestMetrics) -> BacktestSeries:
    return BacktestSeries(
        label=label,
        description=_DESCRIPTIONS[label],
        metrics=_metrics(metrics),
        values=[
            ValuePoint(date=index.date(), value_inr=float(value))
            for index, value in run.values.items()
        ],
    )


@router.post(
    "/run",
    response_model=BacktestResponse,
    summary="Backtest a strategy against two baselines",
    description=(
        "Simulates a constant-mix strategy day by day and runs two baselines "
        "over the same calendar: the same holdings bought once and never "
        "rebalanced, and the Nifty 50 index.\n\n"
        "Share counts are the state, so weights drift naturally between "
        "rebalances. Prices are rebuilt from anomaly-corrected returns, so the "
        "TMPV demerger and TRENT reset never appear as losses.\n\n"
        "**Coverage failures are loud**: every ticker must span the entire "
        "requested window or the request returns 422 with "
        "`INSUFFICIENT_COVERAGE` naming the ticker and its actual range. The "
        "window is never silently shrunk.\n\n"
        "The `limitations` array includes the survivorship-bias warning, "
        "because this response compares against the index."
    ),
    response_description="Strategy, both baselines, their metrics, and the caveats",
    responses={
        422: {
            "model": ErrorResponse,
            "description": "Request cannot be satisfied — see `error_code`",
        },
    },
)
def run(request: BacktestRequest, db: Session = Depends(get_db)) -> BacktestResponse:
    comparison = run_strategy_with_baselines(
        db,
        tickers=request.tickers,
        target_weights=request.target_weights,
        start_date=request.start_date,
        end_date=request.end_date,
        initial_capital=request.initial_capital_inr,
        rebalance_frequency=request.rebalance_frequency,
        transaction_cost_bps=request.transaction_cost_bps,
        risk_free_rate=request.risk_free_rate,
    )

    benchmark = None
    if comparison.buy_and_hold_nifty50 is not None:
        benchmark = _series(
            "buy_and_hold_nifty50",
            comparison.buy_and_hold_nifty50,
            comparison.buy_and_hold_nifty50_metrics,
        )

    return BacktestResponse(
        tickers=request.tickers,
        target_weights=request.target_weights,
        data_window=DataWindow(
            start_date=comparison.start_date,
            end_date=comparison.end_date,
            trading_days=comparison.trading_days,
        ),
        initial_capital_inr=request.initial_capital_inr,
        rebalance_frequency=comparison.rebalance_frequency,
        transaction_cost_bps=comparison.transaction_cost_bps,
        risk_free_rate=comparison.risk_free_rate,
        strategy=_series("strategy", comparison.strategy, comparison.strategy_metrics),
        buy_and_hold_same_stocks=_series(
            "buy_and_hold_same_stocks",
            comparison.buy_and_hold_same_stocks,
            comparison.buy_and_hold_same_stocks_metrics,
        ),
        buy_and_hold_nifty50=benchmark,
        benchmark_unavailable_reason=comparison.benchmark_unavailable_reason,
    )


@router.post(
    "/bootstrap",
    response_model=BootstrapResponse,
    summary="Bootstrapped distribution of backtest outcomes",
    description=(
        "Runs the strategy and both baselines over many paired windows, so the "
        "result is a distribution rather than a point estimate.\n\n"
        "`rolling_windows` steps a fixed-length window forward one month at a "
        "time. `block_bootstrap` resamples blocks of consecutive days "
        "(geometric lengths, ~21-day mean) to preserve volatility clustering — "
        "resampling individual days would destroy it and produce falsely narrow "
        "intervals.\n\n"
        "The headline is `win_rates`: how often the strategy beat each baseline, "
        "compared window by window. **Read it against the survivorship-bias and "
        "price-index warnings in `limitations` before drawing any conclusion.**"
    ),
    response_description="Percentile summaries, paired win rates, and the caveats",
    responses={
        422: {
            "model": ErrorResponse,
            "description": "Request cannot be satisfied — see `error_code`",
        },
    },
)
def bootstrap(
    request: BootstrapRequest, db: Session = Depends(get_db)
) -> BootstrapResponse:
    """Run the strategy and both baselines over many paired windows.

    Within every window all three are measured over identical data, so the win
    rates are genuine paired comparisons rather than a distribution held up
    against someone else's single number.
    """
    config = StrategyConfig(
        kind=request.strategy.kind,
        target_weights=request.strategy.target_weights,
        lookback_days=request.strategy.lookback_days,
        max_weight_per_asset=request.strategy.max_weight_per_asset,
        objective=request.strategy.objective,
        allow_short=request.strategy.allow_short,
    )

    result = bootstrap_backtest(
        db,
        tickers=request.tickers,
        config=config,
        window_years=request.window_years,
        initial_capital=request.initial_capital_inr,
        rebalance_frequency=request.rebalance_frequency,
        transaction_cost_bps=request.transaction_cost_bps,
        risk_free_rate=request.risk_free_rate,
        method=request.method,
        n_resamples=request.n_resamples,
        expected_block_days=request.expected_block_days,
        seed=request.seed,
    )

    windows: list[WindowResultOut] = []
    if request.include_windows:
        for window in result.windows:
            windows.append(
                WindowResultOut(
                    index=window.index,
                    start_date=window.start_date,
                    end_date=window.end_date,
                    trading_days=window.trading_days,
                    n_rebalances=window.n_rebalances,
                    n_optimizer_failures=window.n_optimizer_failures,
                    average_turnover=window.average_turnover,
                    metrics={
                        key: WindowMetricsOut(
                            cagr=metrics.cagr,
                            sharpe_ratio=metrics.sharpe_ratio,
                            sortino_ratio=metrics.sortino_ratio,
                            max_drawdown=metrics.max_drawdown,
                        )
                        for key, metrics in window.metrics.items()
                    },
                )
            )

    return BootstrapResponse(
        method=result.method,
        strategy_kind=result.strategy_kind,
        tickers=result.tickers,
        data_window=DataWindow(
            start_date=result.data_start,
            end_date=result.data_end,
            trading_days=result.data_trading_days,
        ),
        window_years=result.window_years,
        n_windows=result.n_windows,
        rebalance_frequency=request.rebalance_frequency,
        transaction_cost_bps=request.transaction_cost_bps,
        risk_free_rate=request.risk_free_rate,
        win_rates=WinRatesOut(**vars(result.win_rates)),
        summaries={
            key: {
                metric: MetricSummaryOut(**vars(summary))
                for metric, summary in metrics.items()
            }
            for key, metrics in result.summaries.items()
        },
        total_rebalances=result.total_rebalances,
        total_optimizer_failures=result.total_optimizer_failures,
        average_turnover_per_rebalance=result.average_turnover_per_rebalance,
        skipped_windows=result.skipped_windows,
        windows=windows,
    )
