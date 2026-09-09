"""Request and response models for backtesting."""

from __future__ import annotations

import datetime as dt

from pydantic import BaseModel, Field, model_validator

from app.core.limitations import BACKTEST_LIMITATIONS
from app.schemas.common import DataWindow
from app.core.errors import InvalidParameterError, InvalidWeightsError
from app.core.limits import MAX_BACKTEST_YEARS, MAX_TICKERS, enforce_max
from app.core.tickers import to_nse_symbol
from app.services.backtest import DEFAULT_TRANSACTION_COST_BPS
from app.services.markowitz import DEFAULT_RISK_FREE_RATE

_WEIGHT_TOLERANCE = 1e-6


class ValuePoint(BaseModel):
    date: dt.date
    value_inr: float = Field(description="Portfolio value on this date, in INR")


class PerformanceMetrics(BaseModel):
    cagr: float
    annualised_return: float = Field(
        description="Arithmetic mean daily return x 252; matches the optimiser's "
                    "convention so Sharpe ratios are comparable across endpoints"
    )
    annualised_volatility: float
    sharpe_ratio: float
    sortino_ratio: float
    max_drawdown: float
    max_drawdown_peak_date: dt.date | None
    max_drawdown_trough_date: dt.date | None
    total_transaction_costs_inr: float
    total_transaction_costs_pct: float
    n_rebalances: int = Field(
        description="Rebalance events executed, including the day-one purchase"
    )
    start_value_inr: float
    end_value_inr: float
    total_return: float


class BacktestSeries(BaseModel):
    label: str
    description: str
    metrics: PerformanceMetrics
    values: list[ValuePoint]


class BacktestRequest(BaseModel):
    model_config = {
        "json_schema_extra": {
            "examples": [
                {
                    "summary": "Monthly rebalanced, 8-year window",
                    "description": (
                        "The response carries a `limitations` array whose first "
                        "entry is the survivorship-bias warning: the universe is "
                        "today's index constituents, so the comparison against "
                        "the Nifty 50 is biased in the strategy's favour."
                    ),
                    "value": {
                        "tickers": ["RELIANCE", "TCS", "HDFCBANK", "ITC"],
                        "target_weights": {
                            "RELIANCE": 0.25, "TCS": 0.25,
                            "HDFCBANK": 0.25, "ITC": 0.25,
                        },
                        "start_date": "2018-09-10",
                        "end_date": "2026-09-07",
                        "initial_capital_inr": 1000000,
                        "rebalance_frequency": "monthly",
                        "transaction_cost_bps": 15,
                    },
                }
            ]
        }
    }

    tickers: list[str] = Field(min_length=1)
    target_weights: dict[str, float]
    start_date: dt.date
    end_date: dt.date
    initial_capital_inr: float = Field(
        default=1_000_000.0, gt=0, description="Starting portfolio value, in INR"
    )
    rebalance_frequency: str = Field(default="monthly", pattern="^(monthly|quarterly)$")
    transaction_cost_bps: float = Field(
        default=DEFAULT_TRANSACTION_COST_BPS, ge=0.0, le=1000.0
    )
    risk_free_rate: float = Field(default=DEFAULT_RISK_FREE_RATE, ge=0.0, le=1.0)

    @model_validator(mode="after")
    def _validate(self) -> "BacktestRequest":
        if self.start_date >= self.end_date:
            raise InvalidParameterError(
                f"start_date ({self.start_date}) must be before "
                f"end_date ({self.end_date})"
            )

        span_years = (self.end_date - self.start_date).days / 365.25
        enforce_max(round(span_years, 2), MAX_BACKTEST_YEARS, "date range", "years")

        tickers: list[str] = []
        for raw in self.tickers:
            symbol = to_nse_symbol(raw)
            if symbol and symbol not in tickers:
                tickers.append(symbol)
        if not tickers:
            raise InvalidParameterError("At least one ticker is required")
        enforce_max(len(tickers), MAX_TICKERS, "tickers")
        self.tickers = tickers

        weights = {to_nse_symbol(t): float(w) for t, w in self.target_weights.items()}
        if not weights:
            raise InvalidWeightsError("target_weights must not be empty")

        unknown = set(weights) - set(tickers)
        if unknown:
            raise InvalidWeightsError(
                f"target_weights references tickers not in the backtest: "
                f"{', '.join(sorted(unknown))}",
                details={"unexpected_tickers": sorted(unknown)},
            )
        uncovered = set(tickers) - set(weights)
        if uncovered:
            raise InvalidWeightsError(
                f"No target weight given for: {', '.join(sorted(uncovered))}. "
                "Give every ticker a weight (use 0.0 to exclude one).",
                details={"missing_tickers": sorted(uncovered)},
            )

        total = sum(weights.values())
        if abs(total - 1.0) > _WEIGHT_TOLERANCE:
            breakdown = ", ".join(f"{t}={w:g}" for t, w in weights.items())
            raise InvalidWeightsError(
                f"target_weights must sum to 1.0, got {total:.6f} "
                f"(off by {total - 1.0:+.6f}). Weights given: {breakdown}",
                details={"sum": total, "expected": 1.0},
            )
        self.target_weights = weights
        return self


class BacktestResponse(BaseModel):
    tickers: list[str]
    target_weights: dict[str, float]
    data_window: DataWindow = Field(
        description="The trading days the simulation actually ran over"
    )
    initial_capital_inr: float
    rebalance_frequency: str
    transaction_cost_bps: float
    risk_free_rate: float

    strategy: BacktestSeries
    buy_and_hold_same_stocks: BacktestSeries
    buy_and_hold_nifty50: BacktestSeries | None = None
    benchmark_unavailable_reason: str | None = None

    limitations: list[str] = Field(
        default_factory=lambda: list(BACKTEST_LIMITATIONS),
        description="Caveats that must be shown alongside these numbers",
    )
