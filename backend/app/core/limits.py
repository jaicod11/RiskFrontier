"""Request guardrails, so one call cannot occupy the server indefinitely.

Values are set from timings measured in earlier phases, with headroom:

* 10,000 simulations over a 10-day horizon and 8 tickers took ~85 ms;
  100,000 over 60 days took ~3.2 s. 200,000 is roughly 2x the latter's work.
* A 30-point efficient frontier over 10 tickers took ~1.4 s (~96 SLSQP solves).
* A 200-resample block bootstrap took ~3 s; 1,000 is the ceiling that keeps a
  worst case inside a normal HTTP timeout.
* Rolling windows are bounded by the data itself (61 windows over the ingested
  decade), so the cap only guards against absurd `window_years` values.

Exceeding any of these returns 422 with ``REQUEST_LIMIT_EXCEEDED`` naming the
parameter, the value and the limit — never a timeout.
"""

from __future__ import annotations

from typing import Any

from app.core.errors import RequestLimitExceededError

#: Monte Carlo
MAX_N_SIMS = 200_000
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
MAX_BOOTSTRAP_RESAMPLES = 1_000
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
