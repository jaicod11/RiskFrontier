"""Risk endpoints. Thin: parse, delegate, return."""

from __future__ import annotations

import logging

from fastapi import APIRouter, Depends
from sqlalchemy.orm import Session

from app.core.db import get_db
from app.core.errors import ErrorResponse
from app.schemas.common import DataWindow
from app.schemas.risk import VarRequest, VarResponse
from app.services.monte_carlo import (
    run_historical_bootstrap_var,
    run_parametric_var,
)
from app.services.returns import build_returns_matrix

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api/risk", tags=["risk"])


@router.post(
    "/var",
    response_model=VarResponse,
    summary="Monte Carlo VaR / CVaR",
    description=(
        "Runs two independent simulations over the same return window and "
        "returns them side by side, so any difference between them is the "
        "distributional assumption rather than the data.\n\n"
        "**Parametric** fits a multivariate normal and draws correlated shocks "
        "via a Cholesky factor. **Historical bootstrap** resamples whole "
        "historical days, preserving the cross-sectional correlation that "
        "actually occurred. Both compound day by day — no sqrt-time scaling.\n\n"
        "The response carries a `limitations` array; the parametric method "
        "understates tail risk and both estimate correlation from history."
    ),
    response_description="Both methods, the data window used, and the caveats",
    responses={
        422: {
            "model": ErrorResponse,
            "description": "Request cannot be satisfied — see `error_code`",
        },
    },
)
def compute_var(request: VarRequest, db: Session = Depends(get_db)) -> VarResponse:
    """Run both simulations over the same return window and return them together.

    The two methods share one returns matrix so their numbers are directly
    comparable: any difference is the distributional assumption, not the data.
    """
    portfolio = request.portfolio

    returns_df, window = build_returns_matrix(
        db, portfolio.tickers, lookback_days=request.lookback_days
    )

    # Align weights to the matrix column order rather than the request order.
    weights = [portfolio.weight_for(ticker) for ticker in returns_df.columns]

    common = {
        "returns_df": returns_df,
        "weights": weights,
        "total_value": portfolio.total_value_inr,
        "n_sims": request.n_sims,
        "horizon_days": request.horizon_days,
        "confidence_levels": request.confidence_levels,
        "seed": request.seed,
    }

    parametric = run_parametric_var(**common)
    bootstrap = run_historical_bootstrap_var(**common)

    return VarResponse(
        total_value_inr=portfolio.total_value_inr,
        tickers=list(returns_df.columns),
        weights={t: portfolio.weight_for(t) for t in returns_df.columns},
        n_sims=request.n_sims,
        horizon_days=request.horizon_days,
        confidence_levels=request.confidence_levels,
        seed=request.seed,
        data_window=DataWindow(
            start_date=window.start,
            end_date=window.end,
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
