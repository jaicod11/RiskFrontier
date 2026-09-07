"""Risk endpoints. Thin: parse, delegate, return."""

from __future__ import annotations

import logging

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.orm import Session

from app.core.db import get_db
from app.schemas.risk import DataWindow, VarRequest, VarResponse
from app.services.monte_carlo import (
    run_historical_bootstrap_var,
    run_parametric_var,
)
from app.services.returns import UnknownTickerError, build_returns_matrix

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api/risk", tags=["risk"])


@router.post(
    "/var",
    response_model=VarResponse,
    summary="Monte Carlo VaR / CVaR by two methods",
)
def compute_var(request: VarRequest, db: Session = Depends(get_db)) -> VarResponse:
    """Run both simulations over the same return window and return them together.

    The two methods share one returns matrix so their numbers are directly
    comparable: any difference is the distributional assumption, not the data.
    """
    portfolio = request.portfolio

    try:
        returns_df, window = build_returns_matrix(
            db, portfolio.tickers, lookback_days=request.lookback_days
        )
    except UnknownTickerError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc

    # Align weights to the matrix column order rather than the request order.
    weights = [portfolio.weight_for(ticker) for ticker in returns_df.columns]

    common = {
        "returns_df": returns_df,
        "weights": weights,
        "total_value": portfolio.total_value,
        "n_sims": request.n_sims,
        "horizon_days": request.horizon_days,
        "confidence_levels": request.confidence_levels,
        "seed": request.seed,
    }

    try:
        parametric = run_parametric_var(**common)
        bootstrap = run_historical_bootstrap_var(**common)
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc

    return VarResponse(
        total_value=portfolio.total_value,
        tickers=list(returns_df.columns),
        weights={t: portfolio.weight_for(t) for t in returns_df.columns},
        n_sims=request.n_sims,
        horizon_days=request.horizon_days,
        confidence_levels=request.confidence_levels,
        seed=request.seed,
        data_window=DataWindow(
            start=window.start,
            end=window.end,
            trading_days=window.trading_days,
            requested_lookback_days=window.requested_lookback_days,
            shrunk=window.shrunk,
            constrained_by=window.constrained_by,
            constraint_reason=window.constraint_reason,
            first_available_date_by_ticker=window.first_available_date_by_ticker,
        ),
        parametric=parametric,
        historical_bootstrap=bootstrap,
    )
