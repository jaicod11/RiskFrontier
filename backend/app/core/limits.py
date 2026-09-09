"""Request guardrails, so one call cannot occupy the server indefinitely.

Three of these are environment-configurable (``MAX_N_SIMS``,
``MAX_BOOTSTRAP_RESAMPLES``, ``MAX_WALK_FORWARD_BLOCK_RESAMPLES``) because the
right value depends on the machine. Local development keeps the generous
defaults; the free production instance sets lower ones in ``render.yaml``.

Measured on a local M-series machine with a 10-ticker universe:

===========================================  ========
Endpoint / configuration                      Time
===========================================  ========
GET  /health                                  0.04 s
GET  /api/securities                          0.37 s
POST /api/risk/var        10k sims, 10d       0.49 s
POST /api/risk/var        200k sims, 60d     11.48 s
POST /api/portfolio/optimize   30 points      2.60 s
POST /api/backtest/run    8 years, monthly    0.49 s
POST /api/backtest/bootstrap  rolling,
     constant-mix, 61 windows                 1.91 s
POST /api/backtest/bootstrap  rolling,
     walk-forward, 37 windows                 2.59 s
===========================================  ========

Rolling windows stay cheap even with walk-forward because the optimiser result
at a given rebalance date is identical across every window containing it, so it
is computed once and cached (see ``WalkForwardStrategy``).

Block bootstrap with walk-forward is the exception: every resample is fresh
synthetic data, so nothing can be cached and cost is linear in resamples —
15.7 s at 10, 38.7 s at 25, 78.4 s at 50, 102.8 s at 100. A shared-CPU free
instance is several times slower again, which is why that combination has its
own much lower budget.

Exceeding any of these returns 422 with ``REQUEST_LIMIT_EXCEEDED`` naming the
parameter, the value and the limit — never a timeout.
"""

from __future__ import annotations

from typing import Any

from app.core.config import settings
from app.core.errors import RequestLimitExceededError

#: Monte Carlo. Environment-configurable: 200k sims over a 60-day horizon takes
#: 11.5 s locally, which a free instance would stretch well past a proxy limit.
MAX_N_SIMS = settings.max_n_sims
MAX_HORIZON_DAYS = 252
#: Histogram resolution. Beyond a few hundred bins a chart gains nothing and
#: the response just grows.
DEFAULT_DISTRIBUTION_BINS = 50
MAX_DISTRIBUTION_BINS = 250

#: Universe size, shared by every endpoint that takes a ticker list.
MAX_TICKERS = 50

#: Estimation windows
MAX_LOOKBACK_DAYS = 2_520  # ~10 years
MAX_FRONTIER_POINTS = 200

#: Backtesting
MAX_BACKTEST_YEARS = 30
MAX_BOOTSTRAP_RESAMPLES = settings.max_bootstrap_resamples
#: The block_bootstrap x walk_forward combination only.
MAX_WALK_FORWARD_BLOCK_RESAMPLES = settings.max_walk_forward_block_resamples
MAX_BOOTSTRAP_WINDOWS = 500
MAX_WINDOW_YEARS = 20.0


def enforce_max(value: float, limit: float, parameter: str, unit: str = "") -> Any:
    """Raise a 422 naming the parameter, the value and the limit."""
    if value > limit:
        suffix = f" {unit}" if unit else ""
        raise RequestLimitExceededError(
            f"{parameter}={value:g}{suffix} exceeds the maximum of "
            f"{limit:g}{suffix}. Lower {parameter}, or split the work across "
            f"several requests.",
            details={"parameter": parameter, "value": value, "limit": limit},
        )
    return value


def enforce_ticker_count(tickers: list[str]) -> list[str]:
    enforce_max(len(tickers), MAX_TICKERS, "tickers")
    return tickers
