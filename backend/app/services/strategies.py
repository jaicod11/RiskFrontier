"""Rebalancing strategies that plug into the Phase 5 engine seam.

Every strategy here is a ``RebalanceFn``: ``(as_of, history) -> {ticker: weight}``.
The engine calls them and never inspects what they are, so nothing in
``backtest.py`` changes to add one.

Where the trailing history comes from
-------------------------------------
A walk-forward strategy needs ``lookback_days`` of data *before* the backtest
starts, but the engine only ever hands it data from the backtest's own window.
Rather than let the strategy reach into the future-bearing full price matrix and
slice it by date -- which would work but would leave the door open -- it is
constructed with a **warmup frame that ends strictly before the backtest's first
day**. At each rebalance the trailing window is warmup + engine-supplied
history, so the strategy structurally cannot see past ``as_of``: the future is
not in any object it holds.
"""

from __future__ import annotations

import datetime as dt
import logging
from dataclasses import dataclass, field

import numpy as np
import pandas as pd

from app.core.errors import InsufficientLookbackError, InvalidParameterError
from app.services.backtest import BacktestRun
from app.services.markowitz import (
    DEFAULT_MAX_WEIGHT_PER_ASSET,
    DEFAULT_RISK_FREE_RATE,
    InfeasibleConstraintsError,
    max_sharpe_portfolio,
    min_variance_portfolio,
)

logger = logging.getLogger(__name__)

OBJECTIVES = ("max_sharpe", "min_variance")

DEFAULT_WALK_FORWARD_LOOKBACK = 504


@dataclass
class WalkForwardTelemetry:
    """What the strategy did, so the cost of re-optimising is visible."""

    n_rebalances: int = 0
    n_optimizer_failures: int = 0
    failure_dates: list[dt.date] = field(default_factory=list)
    weights_by_date: dict[dt.date, dict[str, float]] = field(default_factory=dict)

    @property
    def failure_rate(self) -> float:
        if not self.n_rebalances:
            return 0.0
        return self.n_optimizer_failures / self.n_rebalances


class WalkForwardStrategy:
    """Re-optimise on the trailing window at every rebalance date.

    Calls the Phase 4 optimiser directly -- no reimplementation. If it fails to
    converge the previous weights are held unchanged and the event is recorded;
    the run never crashes on an optimiser failure.

    A callable object rather than a closure so the telemetry has somewhere to
    live and the instance can be inspected after a run.
    """

    def __init__(
        self,
        warmup_prices: pd.DataFrame,
        lookback_days: int = DEFAULT_WALK_FORWARD_LOOKBACK,
        max_weight_per_asset: float = DEFAULT_MAX_WEIGHT_PER_ASSET,
        objective: str = "max_sharpe",
        risk_free_rate: float = DEFAULT_RISK_FREE_RATE,
        allow_short: bool = False,
        seed: int | None = 0,
        cache: dict | None = None,
    ) -> None:
        if objective not in OBJECTIVES:
            raise InvalidParameterError(
                f"objective must be one of {OBJECTIVES}, got {objective!r}"
            )
        if lookback_days < 2:
            raise InvalidParameterError(
                f"lookback_days must be at least 2, got {lookback_days}"
            )

        available = len(warmup_prices)
        if available < lookback_days:
            first = warmup_prices.index[0].date() if available else None
            last = warmup_prices.index[-1].date() if available else None
            raise InsufficientLookbackError(
                f"walk-forward needs {lookback_days} trading days of history "
                f"before the backtest start, but only {available} are available"
                + (f" ({first} to {last})" if available else "")
                + ". Start the backtest later, shorten lookback_days, or ingest "
                "more history — the window is not shortened automatically "
                "because a shorter estimation window silently changes the "
                "strategy being tested."
            )

        self.warmup_prices = warmup_prices
        self.lookback_days = lookback_days
        self.max_weight_per_asset = max_weight_per_asset
        self.objective = objective
        self.risk_free_rate = risk_free_rate
        self.allow_short = allow_short
        self.seed = seed
        # Optional shared cache: at a given as_of date the trailing window is
        # fixed by the data, so the optimum is identical across every rolling
        # window containing that date. Keyed on (as_of, columns).
        self._cache = cache if cache is not None else {}

        self.telemetry = WalkForwardTelemetry()
        self._previous_weights: dict[str, float] | None = None

    # -- the seam ----------------------------------------------------------

    def __call__(self, as_of: dt.date, history: pd.DataFrame) -> dict[str, float]:
        self.telemetry.n_rebalances += 1
        tickers = list(history.columns)

        # A warmup carrying different columns than the engine supplies would go
        # all-NaN on concat and quietly empty the trailing window, degrading the
        # strategy to "hold previous" without anything looking broken. Fail loudly.
        if len(self.warmup_prices) and set(self.warmup_prices.columns) != set(tickers):
            raise InvalidParameterError(
                "walk-forward warmup columns do not match the backtest universe: "
                f"warmup has {sorted(self.warmup_prices.columns)}, "
                f"backtest has {sorted(tickers)}"
            )

        key = (as_of, tuple(tickers))
        if key in self._cache:
            weights = self._cache[key]
            if weights is not None:
                self._previous_weights = weights
                self.telemetry.weights_by_date[as_of] = weights
                return dict(weights)
            # A cached failure: fall through to the hold-previous path.
            return self._hold_previous(as_of, tickers)

        window = self._trailing_window(history)
        returns = window.pct_change().dropna()

        if len(returns) < 2:
            self._cache[key] = None
            return self._hold_previous(as_of, tickers)

        try:
            if self.objective == "max_sharpe":
                point = max_sharpe_portfolio(
                    returns,
                    risk_free_rate=self.risk_free_rate,
                    max_weight_per_asset=self.max_weight_per_asset,
                    allow_short=self.allow_short,
                    seed=self.seed,
                )
            else:
                point = min_variance_portfolio(
                    returns,
                    risk_free_rate=self.risk_free_rate,
                    max_weight_per_asset=self.max_weight_per_asset,
                    allow_short=self.allow_short,
                    seed=self.seed,
                )
        except (
            InfeasibleConstraintsError,
            RuntimeError,
            ValueError,
            np.linalg.LinAlgError,
        ) as exc:
            logger.warning("Optimiser failed on %s: %s", as_of, exc)
            self._cache[key] = None
            return self._hold_previous(as_of, tickers)

        weights = {t: float(point.weights.get(t, 0.0)) for t in tickers}

        # Defence in depth: the engine rejects weights that do not sum to 1,
        # and that rejection aborts the run. An optimiser that converges to
        # something unusable is a failure like any other, so hold the previous
        # allocation rather than let it propagate.
        total = sum(weights.values())
        if not np.isfinite(total) or abs(total - 1.0) > 1e-6:
            logger.warning(
                "Optimiser returned weights summing to %.6f on %s; holding previous",
                total, as_of,
            )
            self._cache[key] = None
            return self._hold_previous(as_of, tickers)

        self._cache[key] = weights
        self._previous_weights = weights
        self.telemetry.weights_by_date[as_of] = weights
        return dict(weights)

    # -- internals ---------------------------------------------------------

    def _trailing_window(self, history: pd.DataFrame) -> pd.DataFrame:
        """Warmup plus engine history, trimmed to the trailing lookback.

        ``lookback_days`` returns need ``lookback_days + 1`` prices.
        """
        combined = pd.concat([self.warmup_prices, history])
        combined = combined[~combined.index.duplicated(keep="last")]
        return combined.tail(self.lookback_days + 1)

    def _hold_previous(
        self, as_of: dt.date, tickers: list[str]
    ) -> dict[str, float]:
        """Optimiser unusable: keep the existing allocation, record the miss."""
        self.telemetry.n_optimizer_failures += 1
        self.telemetry.failure_dates.append(as_of)

        if self._previous_weights is not None:
            weights = dict(self._previous_weights)
        else:
            # Nothing to hold on the very first rebalance; equal weight is the
            # neutral fallback, and it is recorded as a failure either way.
            weights = {t: 1.0 / len(tickers) for t in tickers}
            self._previous_weights = weights

        self.telemetry.weights_by_date[as_of] = weights
        return dict(weights)


def walk_forward_optimized_strategy(
    warmup_prices: pd.DataFrame,
    lookback_days: int = DEFAULT_WALK_FORWARD_LOOKBACK,
    max_weight_per_asset: float = DEFAULT_MAX_WEIGHT_PER_ASSET,
    objective: str = "max_sharpe",
    risk_free_rate: float = DEFAULT_RISK_FREE_RATE,
    allow_short: bool = False,
    seed: int | None = 0,
    cache: dict | None = None,
) -> WalkForwardStrategy:
    """Build a walk-forward re-optimising strategy. See :class:`WalkForwardStrategy`."""
    return WalkForwardStrategy(
        warmup_prices=warmup_prices,
        lookback_days=lookback_days,
        max_weight_per_asset=max_weight_per_asset,
        objective=objective,
        risk_free_rate=risk_free_rate,
        allow_short=allow_short,
        seed=seed,
        cache=cache,
    )


# ---------------------------------------------------------------------------
# Turnover, measured after the fact
# ---------------------------------------------------------------------------


def rebalance_turnover(run: BacktestRun, prices: pd.DataFrame) -> pd.Series:
    """Fraction of portfolio value traded on each rebalance date.

    Computed from the run rather than recorded by the engine, so the engine
    needs no changes. Turnover is the traded notional over the pre-trade value:
    a full switch from one book to a disjoint one is 2.0 (everything sold,
    everything rebought).
    """
    if run.shares.empty:
        return pd.Series(dtype=float)

    aligned = prices.reindex(columns=run.shares.columns)
    previous = run.shares.shift(1).fillna(0.0)
    traded_notional = (run.shares - previous).abs().mul(aligned).sum(axis=1)
    # value = pre_value - cost, so pre_value = value + cost.
    pre_value = run.values + run.costs

    scheduled = {pd.Timestamp(d) for d in run.rebalance_dates}
    mask = run.shares.index.isin(scheduled)
    turnover = (traded_notional / pre_value.replace(0.0, np.nan))[mask]
    return turnover.dropna()


def average_turnover(run: BacktestRun, prices: pd.DataFrame) -> float:
    turnover = rebalance_turnover(run, prices)
    return float(turnover.mean()) if len(turnover) else 0.0


def constant_mix_config_weights(
    target_weights: dict[str, float], tickers: list[str]
) -> dict[str, float]:
    """Project a weight map onto a ticker list, defaulting missing names to 0."""
    return {t: float(target_weights.get(t, 0.0)) for t in tickers}
