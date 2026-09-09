"""Request and response models for the risk endpoints."""

from __future__ import annotations

from pydantic import BaseModel, Field, field_validator

from app.core.limitations import VAR_LIMITATIONS
from app.core.limits import (
    MAX_HORIZON_DAYS,
    MAX_LOOKBACK_DAYS,
    MAX_N_SIMS,
    enforce_max,
)
from app.schemas.common import DataWindow
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


class VarRequest(BaseModel):
    model_config = {
        "json_schema_extra": {
            "examples": [
                {
                    "portfolio": {
                        "positions": [
                            {"ticker": "RELIANCE", "weight": 0.4},
                            {"ticker": "TCS", "weight": 0.3},
                            {"ticker": "HDFCBANK", "weight": 0.3},
                        ],
                        "total_value_inr": 1000000,
                    },
                    "n_sims": 10000,
                    "horizon_days": 10,
                    "confidence_levels": [0.95, 0.99],
                    "lookback_days": 504,
                    "seed": 42,
                }
            ]
        }
    }

    portfolio: Portfolio
    n_sims: int = Field(
        default=10_000, ge=100,
        description=f"Simulation paths (max {MAX_N_SIMS:,})",
        json_schema_extra={"maximum": MAX_N_SIMS},
    )
    horizon_days: int = Field(
        default=1, ge=1,
        description=f"Trading days to simulate forward (max {MAX_HORIZON_DAYS})",
        json_schema_extra={"maximum": MAX_HORIZON_DAYS},
    )
    confidence_levels: list[float] = Field(default=[0.95, 0.99])
    lookback_days: int = Field(
        default=504, ge=30,
        description=f"Estimation window in trading days (max {MAX_LOOKBACK_DAYS:,})",
        json_schema_extra={"maximum": MAX_LOOKBACK_DAYS},
    )
    seed: int | None = Field(
        default=None, description="Set for reproducible simulations"
    )

    @field_validator("n_sims")
    @classmethod
    def _cap_sims(cls, value: int) -> int:
        return enforce_max(value, MAX_N_SIMS, "n_sims")

    @field_validator("horizon_days")
    @classmethod
    def _cap_horizon(cls, value: int) -> int:
        return enforce_max(value, MAX_HORIZON_DAYS, "horizon_days", "trading days")

    @field_validator("lookback_days")
    @classmethod
    def _cap_lookback(cls, value: int) -> int:
        return enforce_max(value, MAX_LOOKBACK_DAYS, "lookback_days", "trading days")

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

    total_value_inr: float = Field(
        description="Portfolio value the P&L figures are denominated against"
    )
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
