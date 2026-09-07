"""Request and response models for Markowitz optimisation."""

from __future__ import annotations

from pydantic import BaseModel, Field, field_validator

from app.core.limitations import OPTIMIZER_LIMITATIONS
from app.core.tickers import to_nse_symbol
from app.schemas.risk import DataWindow
from app.services.markowitz import (
    DEFAULT_FRONTIER_POINTS,
    DEFAULT_MAX_WEIGHT_PER_ASSET,
    DEFAULT_RISK_FREE_RATE,
)


class OptimizedPortfolio(BaseModel):
    """One point on (or in) the frontier."""

    expected_return: float = Field(description="Annualised, as a fraction (0.14 = 14%)")
    volatility: float = Field(description="Annualised standard deviation")
    sharpe_ratio: float
    weights: dict[str, float]


class OptimizeRequest(BaseModel):
    tickers: list[str] = Field(
        min_length=2,
        description="Candidate universe; the optimiser determines the weights",
    )
    risk_free_rate: float = Field(
        default=DEFAULT_RISK_FREE_RATE, ge=0.0, le=1.0,
        description="Annualised, as a fraction. Default ~short-tenor Indian rate.",
    )
    max_weight_per_asset: float = Field(
        default=DEFAULT_MAX_WEIGHT_PER_ASSET, gt=0.0, le=1.0
    )
    allow_short: bool = Field(
        default=False,
        description="Permit negative weights, bounded by -max_weight_per_asset",
    )
    lookback_days: int = Field(default=504, ge=30, le=2520)
    n_frontier_points: int = Field(default=DEFAULT_FRONTIER_POINTS, ge=2, le=200)
    seed: int | None = Field(
        default=0, description="Seeds the optimiser's random restarts"
    )

    @field_validator("tickers")
    @classmethod
    def _normalise(cls, values: list[str]) -> list[str]:
        seen: list[str] = []
        for raw in values:
            symbol = to_nse_symbol(raw)
            if not symbol:
                raise ValueError("tickers must not contain empty strings")
            if symbol not in seen:
                seen.append(symbol)
        if len(seen) < 2:
            raise ValueError(
                "At least 2 distinct tickers are required to optimise a portfolio"
            )
        return seen


class OptimizeResponse(BaseModel):
    tickers: list[str]
    risk_free_rate: float
    max_weight_per_asset: float
    allow_short: bool
    data_window: DataWindow

    efficient_frontier: list[OptimizedPortfolio]
    min_variance_portfolio: OptimizedPortfolio
    max_sharpe_portfolio: OptimizedPortfolio

    frontier_points_requested: int
    frontier_points_returned: int
    skipped_target_returns: list[float] = Field(
        default_factory=list,
        description="Target returns the solver could not reach; these were "
                    "skipped so the rest of the frontier could still be returned",
    )

    limitations: list[str] = Field(
        default_factory=lambda: list(OPTIMIZER_LIMITATIONS),
        description="Caveats that must be shown alongside these numbers",
    )
