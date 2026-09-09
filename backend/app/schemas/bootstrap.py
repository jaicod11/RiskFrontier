"""Request and response models for bootstrapped backtests."""

from __future__ import annotations

import datetime as dt
from typing import Literal

from pydantic import BaseModel, Field, model_validator

from app.core.limitations import BOOTSTRAP_LIMITATIONS
from app.schemas.common import DataWindow
from app.core.errors import (
    InvalidParameterError,
    InvalidWeightsError,
    RequestLimitExceededError,
)
from app.core.limits import (
    MAX_BOOTSTRAP_RESAMPLES,
    MAX_WALK_FORWARD_BLOCK_RESAMPLES,
    MAX_TICKERS,
    MAX_WINDOW_YEARS,
    enforce_max,
)
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
    model_config = {
        "json_schema_extra": {
            "examples": [
                {
                    "summary": "Walk-forward, rolling 5-year windows",
                    "description": (
                        "Returns a distribution plus paired win rates. Read "
                        "`win_rates` only alongside the survivorship-bias and "
                        "benchmark price-index warnings in `limitations` — a "
                        "100% win rate against the index is an artefact of "
                        "universe selection, not evidence of skill."
                    ),
                    "value": {
                        "tickers": [
                            "RELIANCE", "TCS", "HDFCBANK", "INFY", "ITC",
                            "SUNPHARMA", "MARUTI", "NTPC",
                        ],
                        "strategy": {
                            "kind": "walk_forward",
                            "objective": "max_sharpe",
                            "lookback_days": 504,
                            "max_weight_per_asset": 0.35,
                        },
                        "window_years": 5,
                        "method": "rolling_windows",
                        "rebalance_frequency": "monthly",
                        "include_windows": False,
                        "seed": 0,
                    },
                }
            ]
        }
    }

    tickers: list[str] = Field(min_length=2)
    strategy: StrategySpec = Field(default_factory=StrategySpec)
    window_years: float = Field(
        default=5.0, gt=0.5,
        description=f"Length of each window (max {MAX_WINDOW_YEARS:g} years)",
        json_schema_extra={"maximum": MAX_WINDOW_YEARS},
    )
    method: Literal["rolling_windows", "block_bootstrap"] = "rolling_windows"
    initial_capital_inr: float = Field(
        default=1_000_000.0, gt=0, description="Starting portfolio value, in INR"
    )
    rebalance_frequency: Literal["monthly", "quarterly"] = "monthly"
    transaction_cost_bps: float = Field(
        default=DEFAULT_TRANSACTION_COST_BPS, ge=0.0, le=1000.0
    )
    risk_free_rate: float = Field(default=DEFAULT_RISK_FREE_RATE, ge=0.0, le=1.0)
    n_resamples: int = Field(
        default=200, ge=10,
        description=f"Block-bootstrap resamples (max {MAX_BOOTSTRAP_RESAMPLES:,})",
        json_schema_extra={"maximum": MAX_BOOTSTRAP_RESAMPLES},
    )
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
            raise InvalidParameterError("At least 2 distinct tickers are required")
        enforce_max(len(tickers), MAX_TICKERS, "tickers")
        enforce_max(self.window_years, MAX_WINDOW_YEARS, "window_years", "years")
        enforce_max(self.n_resamples, MAX_BOOTSTRAP_RESAMPLES, "n_resamples")

        # Block bootstrap with walk-forward re-optimisation is the one path
        # that cannot reuse the optimiser cache: every resample is fresh
        # synthetic data, so cost is linear in resamples and an order of
        # magnitude above every other configuration. Reject it above budget
        # with a message that names the alternative, rather than accepting the
        # request and letting the platform time it out with no explanation.
        if self.method == "block_bootstrap" and self.strategy.kind == "walk_forward":
            if self.n_resamples > MAX_WALK_FORWARD_BLOCK_RESAMPLES:
                raise RequestLimitExceededError(
                    f"n_resamples={self.n_resamples} is too high for "
                    f"block_bootstrap with the walk_forward strategy: this "
                    f"combination re-optimises on every resample and cannot "
                    f"reuse cached results, so it costs roughly a second per "
                    f"resample. The limit for this combination is "
                    f"{MAX_WALK_FORWARD_BLOCK_RESAMPLES}. Lower n_resamples, "
                    f"use method=rolling_windows (which caches and stays fast "
                    f"at any window count), or use the constant_mix strategy.",
                    details={
                        "parameter": "n_resamples",
                        "value": self.n_resamples,
                        "limit": MAX_WALK_FORWARD_BLOCK_RESAMPLES,
                        "method": self.method,
                        "strategy_kind": self.strategy.kind,
                    },
                )
        self.tickers = tickers

        if self.strategy.kind == "constant_mix":
            weights = self.strategy.target_weights
            if not weights:
                raise InvalidWeightsError(
                    "constant_mix requires target_weights: a fixed mix needs a "
                    "target to rebalance back to"
                )
            normalised = {to_nse_symbol(t): float(w) for t, w in weights.items()}
            unknown = set(normalised) - set(tickers)
            if unknown:
                raise InvalidWeightsError(
                    f"target_weights references tickers not in the universe: "
                    f"{', '.join(sorted(unknown))}",
                    details={"unexpected_tickers": sorted(unknown)},
                )
            uncovered = set(tickers) - set(normalised)
            if uncovered:
                raise InvalidWeightsError(
                    f"No target weight given for: {', '.join(sorted(uncovered))}",
                    details={"missing_tickers": sorted(uncovered)},
                )
            total = sum(normalised.values())
            if abs(total - 1.0) > _WEIGHT_TOLERANCE:
                raise InvalidWeightsError(
                    f"target_weights must sum to 1.0, got {total:.6f}",
                    details={"sum": total, "expected": 1.0},
                )
            self.strategy.target_weights = normalised
        return self


class BootstrapResponse(BaseModel):
    method: str
    strategy_kind: str
    tickers: list[str]
    data_window: DataWindow = Field(
        description="The full span of data the windows or resamples were drawn from"
    )
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
