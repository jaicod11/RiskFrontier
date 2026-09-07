"""Request and response models for the risk endpoints."""

from __future__ import annotations

import datetime as dt

from pydantic import BaseModel, Field, field_validator

from app.core.limitations import VAR_LIMITATIONS
from app.schemas.portfolio import Portfolio

class VarEstimate(BaseModel):
    """VaR and CVaR at one confidence level, in rupees and as a percentage."""

    confidence_level: float
    var_inr: float = Field(description="Loss not expected to be exceeded, in INR")
    var_pct: float = Field(description="Same loss as a percentage of total_value")
    cvar_inr: float = Field(description="Mean loss in the tail beyond VaR, in INR")
    cvar_pct: float


class MethodResult(BaseModel):
    """One simulation method's output."""

    method: str
    description: str
    estimates: list[VarEstimate]
    mean_horizon_return_pct: float
    worst_simulated_pnl_inr: float
    best_simulated_pnl_inr: float


class DataWindow(BaseModel):
    """Which data actually backed the estimate."""

    start: dt.date
    end: dt.date
    trading_days: int
    requested_lookback_days: int
    shrunk: bool = Field(
        description="True when the window is shorter than requested"
    )
    constrained_by: str | None = Field(
        default=None, description="Ticker whose history bound the window"
    )
    constraint_reason: str | None = None
    first_available_date_by_ticker: dict[str, dt.date] = Field(
        default_factory=dict,
        description="True start of each ticker's price history",
    )


class VarRequest(BaseModel):
    portfolio: Portfolio
    n_sims: int = Field(default=10_000, ge=100, le=200_000)
    horizon_days: int = Field(default=1, ge=1, le=252)
    confidence_levels: list[float] = Field(default=[0.95, 0.99])
    lookback_days: int = Field(default=504, ge=30, le=2520)
    seed: int | None = Field(
        default=None, description="Set for reproducible simulations"
    )

    @field_validator("confidence_levels")
    @classmethod
    def _validate_confidence(cls, values: list[float]) -> list[float]:
        if not values:
            raise ValueError("confidence_levels must not be empty")
        for value in values:
            if not 0.0 < value < 1.0:
                raise ValueError(
                    f"confidence_levels must be strictly between 0 and 1, got {value}"
                )
        return sorted(set(values))


class VarResponse(BaseModel):
    """Both methods side by side, plus the context needed to read them."""

    total_value: float
    tickers: list[str]
    weights: dict[str, float]
    n_sims: int
    horizon_days: int
    confidence_levels: list[float]
    seed: int | None
    data_window: DataWindow
    parametric: MethodResult
    historical_bootstrap: MethodResult
    limitations: list[str] = Field(
        default_factory=lambda: list(VAR_LIMITATIONS),
        description="Caveats that must be shown alongside these numbers",
    )
