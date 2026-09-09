"""Portfolio construction endpoints."""

from __future__ import annotations

import logging

from fastapi import APIRouter, Depends
from sqlalchemy.orm import Session

from app.core.db import get_db
from app.schemas.optimizer import (
    OptimizedPortfolio,
    OptimizeRequest,
    OptimizeResponse,
)
from app.schemas.common import DataWindow
from app.core.errors import ErrorResponse
from app.services.markowitz import (
    OptimizedPoint,
    efficient_frontier,
    max_sharpe_portfolio,
    min_variance_portfolio,
)
from app.services.returns import build_returns_matrix

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api/portfolio", tags=["portfolio"])


def _to_schema(point: OptimizedPoint) -> OptimizedPortfolio:
    return OptimizedPortfolio(
        expected_return=point.expected_return,
        volatility=point.volatility,
        sharpe_ratio=point.sharpe_ratio,
        weights=point.weights,
    )


@router.post(
    "/optimize",
    response_model=OptimizeResponse,
    summary="Markowitz mean-variance optimisation",
    description=(
        "Takes a candidate universe — **no weights**, the optimiser determines "
        "those — and traces the efficient frontier, returning the "
        "minimum-variance and maximum-Sharpe portfolios alongside it.\n\n"
        "Every optimisation runs from three starting points and keeps the best "
        "converged result. A frontier target the solver cannot reach is skipped "
        "and reported in `skipped_target_returns` rather than failing the "
        "request.\n\n"
        "A `max_weight_per_asset` below `1/len(tickers)` is infeasible and "
        "returns 422 with `INFEASIBLE_CONSTRAINTS`."
    ),
    response_description="The frontier, its two named portfolios, and the caveats",
    responses={
        422: {
            "model": ErrorResponse,
            "description": "Request cannot be satisfied — see `error_code`",
        },
    },
)
def optimize(request: OptimizeRequest, db: Session = Depends(get_db)) -> OptimizeResponse:
    """Trace the efficient frontier and return its two named portfolios.

    Returns come from the same shared cleaning layer as every other phase, so
    registered price anomalies never reach the covariance matrix.
    """
    returns_df, window = build_returns_matrix(
        db, request.tickers, lookback_days=request.lookback_days
    )

    shared = {
        "risk_free_rate": request.risk_free_rate,
        "max_weight_per_asset": request.max_weight_per_asset,
        "allow_short": request.allow_short,
        "seed": request.seed,
    }

    minimum_variance = min_variance_portfolio(returns_df, **shared)
    maximum_sharpe = max_sharpe_portfolio(returns_df, **shared)
    trace = efficient_frontier(
        returns_df, n_points=request.n_frontier_points, **shared
    )

    return OptimizeResponse(
        tickers=list(returns_df.columns),
        risk_free_rate=request.risk_free_rate,
        max_weight_per_asset=request.max_weight_per_asset,
        allow_short=request.allow_short,
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
        efficient_frontier=[_to_schema(p) for p in trace.points],
        min_variance_portfolio=_to_schema(minimum_variance),
        max_sharpe_portfolio=_to_schema(maximum_sharpe),
        frontier_points_requested=trace.requested_points,
        frontier_points_returned=len(trace.points),
        skipped_target_returns=trace.skipped_target_returns,
    )
