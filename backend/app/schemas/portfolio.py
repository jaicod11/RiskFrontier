"""Portfolio input, shared by every analytical phase.

Deliberately generic: this describes *what is held*, with no risk-, optimiser-
or backtest-specific fields. Monte Carlo VaR, Markowitz optimisation and the
backtester all take the same object.
"""

from __future__ import annotations

from pydantic import BaseModel, Field, field_validator, model_validator

from app.core.tickers import to_nse_symbol

#: Weights are floats, so an exact sum of 1.0 is not a reasonable demand.
#: This tolerance accepts honest rounding (0.333/0.333/0.334) while still
#: rejecting a genuinely mis-specified book.
WEIGHT_SUM_TOLERANCE = 1e-6


class Position(BaseModel):
    """One holding: a ticker and its share of the portfolio."""

    ticker: str = Field(description="NSE symbol; the '.NS' suffix is accepted and stripped")
    weight: float = Field(description="Share of total_value, as a fraction (0.25 = 25%)")

    @field_validator("ticker")
    @classmethod
    def _normalise_ticker(cls, value: str) -> str:
        symbol = to_nse_symbol(value)
        if not symbol:
            raise ValueError("ticker must not be empty")
        return symbol

    @field_validator("weight")
    @classmethod
    def _weight_must_be_finite(cls, value: float) -> float:
        if value != value or value in (float("inf"), float("-inf")):
            raise ValueError("weight must be a finite number")
        return value


class Portfolio(BaseModel):
    """A set of positions and the rupee value they represent.

    Weights must sum to 1.0. Negative weights are permitted so the same schema
    can carry a short book later; the sum constraint still applies.
    """

    positions: list[Position] = Field(min_length=1)
    total_value: float = Field(gt=0, description="Total portfolio value in INR")

    @model_validator(mode="after")
    def _validate_book(self) -> "Portfolio":
        tickers = [p.ticker for p in self.positions]

        duplicates = sorted({t for t in tickers if tickers.count(t) > 1})
        if duplicates:
            raise ValueError(
                f"Duplicate tickers in portfolio: {', '.join(duplicates)}. "
                "Combine them into a single position."
            )

        total = sum(p.weight for p in self.positions)
        if abs(total - 1.0) > WEIGHT_SUM_TOLERANCE:
            breakdown = ", ".join(f"{p.ticker}={p.weight:g}" for p in self.positions)
            raise ValueError(
                f"Portfolio weights must sum to 1.0, got {total:.6f} "
                f"(off by {total - 1.0:+.6f}). Weights given: {breakdown}"
            )
        return self

    @property
    def tickers(self) -> list[str]:
        return [p.ticker for p in self.positions]

    @property
    def weights(self) -> list[float]:
        return [p.weight for p in self.positions]

    def weight_for(self, ticker: str) -> float:
        symbol = to_nse_symbol(ticker)
        for position in self.positions:
            if position.ticker == symbol:
                return position.weight
        raise KeyError(f"{ticker} is not in this portfolio")
