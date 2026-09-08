"""Request and response models for bootstrapped backtests."""

from __future__ import annotations

import datetime as dt
from typing import Literal

from pydantic import BaseModel, Field, model_validator

from app.core.limitations import BOOTSTRAP_LIMITATIONS
from app.core.tickers import to_nse_symbol
from app.services.backtest import DEFAULT_TRANSACTION_COST_BPS
from app.services.bootstrap import DEFAULT_EXPECTED_BLOCK_DAYS
from app.services.markowitz import (
    DEFAULT_MAX_WEIGHT_PER_ASSET,
    DEFAULT_RISK_FREE_RATE,
)
from app.services.strategies import DEFAULT_WALK_FORWARD_LOOKBACK

_WEIGHT_TOLERANCE = 1e-6


class StrategySpec(BaseModel):
    kind: Literal["constant_mix", "walk_forward"] = "constant_mix"
    target_weights: dict[str, float] | None = Field(
        default=None, description="Required for constant_mix; ignored otherwise"
    )
    lookback_days: int = Field(default=DEFAULT_WALK_FORWARD_LOOKBACK, ge=30, le=2520)
    max_weight_per_asset: float = Field(
        default=DEFAULT_MAX_WEIGHT_PER_ASSET, gt=0.0, le=1.0
    )
    objective: Literal["max_sharpe", "min_variance"] = "max_sharpe"
    allow_short: bool = False


class MetricSummaryOut(BaseModel):
    median: float
    p5: float
    p95: float
    min: float
    max: float
    n_windows: int


class WinRatesOut(BaseModel):
    """Paired, window by window — the headline number for this endpoint."""

    vs_nifty50_cagr: float = Field(
        description="Fraction of windows where the strategy's CAGR beat the index"
    )
    vs_nifty50_sharpe: float = Field(
        description="Fraction of windows where the strategy's Sharpe beat the index"
    )
    vs_buy_and_hold_cagr: float
    vs_buy_and_hold_sharpe: float
    n_windows: int


class WindowMetricsOut(BaseModel):
    cagr: float
    sharpe_ratio: float
    sortino_ratio: float
    max_drawdown: float


class WindowResultOut(BaseModel):
    index: int
    start_date: dt.date
    end_date: dt.date
    trading_days: int
    n_rebalances: int
    n_optimizer_failures: int
    average_turnover: float
    metrics: dict[str, WindowMetricsOut]


class BootstrapRequest(BaseModel):
    tickers: list[str] = Field(min_length=2)
    strategy: StrategySpec = Field(default_factory=StrategySpec)
    window_years: float = Field(default=5.0, gt=0.5, le=20.0)
    method: Literal["rolling_windows", "block_bootstrap"] = "rolling_windows"
    initial_capital: float = Field(default=1_000_000.0, gt=0)
    rebalance_frequency: Literal["monthly", "quarterly"] = "monthly"
    transaction_cost_bps: float = Field(
        default=DEFAULT_TRANSACTION_COST_BPS, ge=0.0, le=1000.0
    )
    risk_free_rate: float = Field(default=DEFAULT_RISK_FREE_RATE, ge=0.0, le=1.0)
    n_resamples: int = Field(default=200, ge=10, le=2000)
    expected_block_days: int = Field(default=DEFAULT_EXPECTED_BLOCK_DAYS, ge=2, le=252)
    seed: int | None = 0
    include_windows: bool = Field(
        default=True, description="Include the per-window array for charting"
    )

    @model_validator(mode="after")
    def _validate(self) -> "BootstrapRequest":
        tickers: list[str] = []
        for raw in self.tickers:
            symbol = to_nse_symbol(raw)
            if symbol and symbol not in tickers:
                tickers.append(symbol)
        if len(tickers) < 2:
            raise ValueError("At least 2 distinct tickers are required")
        self.tickers = tickers

        if self.strategy.kind == "constant_mix":
            weights = self.strategy.target_weights
            if not weights:
                raise ValueError(
                    "constant_mix requires target_weights"
                )
            normalised = {to_nse_symbol(t): float(w) for t, w in weights.items()}
            unknown = set(normalised) - set(tickers)
            if unknown:
                raise ValueError(
                    f"target_weights references tickers not in the universe: "
                    f"{', '.join(sorted(unknown))}"
                )
            uncovered = set(tickers) - set(normalised)
            if uncovered:
                raise ValueError(
                    f"No target weight given for: {', '.join(sorted(uncovered))}"
                )
            total = sum(normalised.values())
            if abs(total - 1.0) > _WEIGHT_TOLERANCE:
                raise ValueError(
                    f"target_weights must sum to 1.0, got {total:.6f}"
                )
            self.strategy.target_weights = normalised
        return self


class BootstrapResponse(BaseModel):
    method: str
    strategy_kind: str
    tickers: list[str]
    window_years: float
    n_windows: int
    rebalance_frequency: str
    transaction_cost_bps: float
    risk_free_rate: float

    #: The headline. A distribution beats a point estimate, and a paired win
    #: rate beats a distribution compared against someone else's point estimate.
    win_rates: WinRatesOut

    summaries: dict[str, dict[str, MetricSummaryOut]]

    total_rebalances: int
    total_optimizer_failures: int
    average_turnover_per_rebalance: float
    skipped_windows: list[str] = Field(default_factory=list)

    windows: list[WindowResultOut] = Field(default_factory=list)

    limitations: list[str] = Field(
        default_factory=lambda: list(BOOTSTRAP_LIMITATIONS),
        description="Caveats that must be shown alongside these numbers",
    )
