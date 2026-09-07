"""Backtesting endpoints."""

from __future__ import annotations

import logging

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.orm import Session

from app.core.db import get_db
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
