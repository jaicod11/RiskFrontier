"""Backtesting endpoints."""

from __future__ import annotations

import logging

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.orm import Session

from app.core.db import get_db
from app.schemas.bootstrap import (
    BootstrapRequest,
    BootstrapResponse,
    MetricSummaryOut,
    WindowMetricsOut,
    WindowResultOut,
    WinRatesOut,
)
from app.services.bootstrap import StrategyConfig, bootstrap_backtest
from app.services.strategies import InsufficientLookbackError
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
    InsufficientCoverageError,
    StrategyContractError,
    run_strategy_with_baselines,
)
from app.services.returns import UnknownTickerError

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
    return PerformanceMetrics(**vars(metrics))


def _series(label: str, run: BacktestRun, metrics: BacktestMetrics) -> BacktestSeries:
    return BacktestSeries(
        label=label,
        description=_DESCRIPTIONS[label],
        metrics=_metrics(metrics),
        values=[
            ValuePoint(date=index.date(), value=float(value))
            for index, value in run.values.items()
        ],
    )


@router.post(
    "/run",
    response_model=BacktestResponse,
    summary="Backtest a constant-mix strategy against two baselines",
)
def run(request: BacktestRequest, db: Session = Depends(get_db)) -> BacktestResponse:
    try:
        comparison = run_strategy_with_baselines(
            db,
            tickers=request.tickers,
            target_weights=request.target_weights,
            start_date=request.start_date,
            end_date=request.end_date,
            initial_capital=request.initial_capital,
            rebalance_frequency=request.rebalance_frequency,
            transaction_cost_bps=request.transaction_cost_bps,
            risk_free_rate=request.risk_free_rate,
        )
    except UnknownTickerError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    except (InsufficientCoverageError, StrategyContractError, ValueError) as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc

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
        start_date=comparison.start_date,
        end_date=comparison.end_date,
        trading_days=comparison.trading_days,
        initial_capital=request.initial_capital,
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
    summary="Distribution of backtest outcomes across many windows or resamples",
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

    try:
        result = bootstrap_backtest(
            db,
            tickers=request.tickers,
            config=config,
            window_years=request.window_years,
            initial_capital=request.initial_capital,
            rebalance_frequency=request.rebalance_frequency,
            transaction_cost_bps=request.transaction_cost_bps,
            risk_free_rate=request.risk_free_rate,
            method=request.method,
            n_resamples=request.n_resamples,
            expected_block_days=request.expected_block_days,
            seed=request.seed,
        )
    except UnknownTickerError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    except (
        InsufficientLookbackError,
        InsufficientCoverageError,
        StrategyContractError,
        ValueError,
    ) as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc

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
