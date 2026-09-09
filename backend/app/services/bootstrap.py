"""Distribution of backtest outcomes, rather than a single point estimate.

A backtest over one window answers "what happened between these two dates".
That is one draw. Both methods here re-run the *same* engine over many windows
or resamples so the spread of outcomes is visible.

Everything is **paired**: within each window or resample the strategy and both
baselines are run over identical data. A strategy distribution compared against
a single-window baseline number would be meaningless -- most of the spread would
be the period, not the strategy.
"""

from __future__ import annotations

import datetime as dt
import logging
from dataclasses import dataclass, field
from typing import Literal

import numpy as np
import pandas as pd
from dateutil.relativedelta import relativedelta
from sqlalchemy.orm import Session

from app.core.errors import (
    InsufficientCoverageError,
    InsufficientLookbackError,
    InvalidParameterError,
)
from app.core.tickers import to_nse_symbol
from app.services.backtest import (
    BENCHMARK_TICKER,
    DEFAULT_TRANSACTION_COST_BPS,
    BacktestMetrics,
    build_price_matrix,
    compute_metrics,
    constant_mix_strategy,
    rebalance_calendar,
    run_backtest,
)
from app.services.markowitz import (
    DEFAULT_MAX_WEIGHT_PER_ASSET,
    DEFAULT_RISK_FREE_RATE,
)
from app.services.strategies import (
    DEFAULT_WALK_FORWARD_LOOKBACK,
    WalkForwardStrategy,
    average_turnover,
)

logger = logging.getLogger(__name__)

Method = Literal["rolling_windows", "block_bootstrap"]

#: Expected block length for the stationary bootstrap, in trading days (~1 month).
DEFAULT_EXPECTED_BLOCK_DAYS = 21

METRIC_FIELDS = ("cagr", "sharpe_ratio", "sortino_ratio", "max_drawdown")

SERIES_KEYS = ("strategy", "buy_and_hold_same_stocks", "buy_and_hold_nifty50")


# ---------------------------------------------------------------------------
# Configuration
# ---------------------------------------------------------------------------


@dataclass
class StrategyConfig:
    """Which strategy to bootstrap, and its parameters."""

    kind: Literal["constant_mix", "walk_forward"] = "constant_mix"
    target_weights: dict[str, float] | None = None
    lookback_days: int = DEFAULT_WALK_FORWARD_LOOKBACK
    max_weight_per_asset: float = DEFAULT_MAX_WEIGHT_PER_ASSET
    objective: str = "max_sharpe"
    allow_short: bool = False

    def warmup_days(self) -> int:
        return self.lookback_days if self.kind == "walk_forward" else 0


# ---------------------------------------------------------------------------
# Results
# ---------------------------------------------------------------------------


@dataclass
class WindowResult:
    """One window or resample, with all three series measured over it."""

    index: int
    start_date: dt.date
    end_date: dt.date
    trading_days: int
    metrics: dict[str, BacktestMetrics]
    n_rebalances: int = 0
    n_optimizer_failures: int = 0
    average_turnover: float = 0.0


@dataclass
class MetricSummary:
    median: float
    p5: float
    p95: float
    min: float
    max: float
    n_windows: int


@dataclass
class WinRates:
    """How often the strategy beat a baseline, window by window."""

    vs_nifty50_cagr: float
    vs_nifty50_sharpe: float
    vs_buy_and_hold_cagr: float
    vs_buy_and_hold_sharpe: float
    n_windows: int


@dataclass
class BootstrapResult:
    method: Method
    window_years: float
    n_windows: int
    tickers: list[str]
    strategy_kind: str
    summaries: dict[str, dict[str, MetricSummary]]
    win_rates: WinRates
    #: Full span the windows or resamples were drawn from.
    data_start: dt.date | None = None
    data_end: dt.date | None = None
    data_trading_days: int = 0
    windows: list[WindowResult] = field(default_factory=list)
    total_rebalances: int = 0
    total_optimizer_failures: int = 0
    average_turnover_per_rebalance: float = 0.0
    skipped_windows: list[str] = field(default_factory=list)


# ---------------------------------------------------------------------------
# Resampling index generators
# ---------------------------------------------------------------------------


def stationary_block_indices(
    n: int,
    length: int,
    rng: np.random.Generator,
    expected_block: int = DEFAULT_EXPECTED_BLOCK_DAYS,
) -> np.ndarray:
    """Stationary block bootstrap indices (Politis & Romano).

    Blocks of *consecutive* days are drawn, with geometric block lengths of mean
    ``expected_block``, wrapping around the end of the series.

    Why blocks and not individual days: equity volatility clusters -- big moves
    arrive next to other big moves, and a crash is a run of bad days, not one
    bad day. Resampling days independently destroys that structure. The
    resampled series then has the right unconditional variance but no
    persistence, so drawdowns are far shallower than anything real and the
    resulting confidence intervals come out falsely narrow. Keeping days in
    blocks preserves the runs, and with them the tail behaviour that matters.
    """
    if n <= 0:
        raise InvalidParameterError("Cannot resample an empty series")
    if length <= 0:
        raise InvalidParameterError("length must be positive")

    probability = 1.0 / max(expected_block, 1)
    indices = np.empty(length, dtype=np.int64)

    filled = 0
    while filled < length:
        start = int(rng.integers(0, n))
        block = min(int(rng.geometric(probability)), length - filled)
        indices[filled : filled + block] = (start + np.arange(block)) % n
        filled += block
    return indices


def iid_indices(n: int, length: int, rng: np.random.Generator) -> np.ndarray:
    """Independent day resampling. Provided for comparison, not for use."""
    return rng.integers(0, n, size=length)


# ---------------------------------------------------------------------------
# Window generation
# ---------------------------------------------------------------------------


def rolling_windows(
    trading_dates: list[dt.date], window_years: float, warmup_days: int = 0
) -> list[tuple[dt.date, dt.date]]:
    """Windows of ``window_years``, stepping the start forward one month.

    Starts are the first traded day of each calendar month. A window is kept
    only if its full span, plus any required warmup before it, fits inside the
    available data.
    """
    if not trading_dates:
        return []
    if window_years <= 0:
        raise InvalidParameterError("window_years must be positive")
    if warmup_days >= len(trading_dates):
        return []

    earliest = trading_dates[warmup_days]
    last = trading_dates[-1]

    # First traded day of each calendar month at or after `earliest`.
    month_starts: list[dt.date] = []
    seen: set[tuple[int, int]] = set()
    for day in trading_dates[warmup_days:]:
        key = (day.year, day.month)
        if key not in seen:
            seen.add(key)
            month_starts.append(day)

    whole_years = int(window_years)
    extra_months = int(round((window_years - whole_years) * 12))

    windows: list[tuple[dt.date, dt.date]] = []
    for start in month_starts:
        if start < earliest:
            continue
        end = start + relativedelta(years=whole_years, months=extra_months)
        if end > last:
            break
        windows.append((start, end))
    return windows


# ---------------------------------------------------------------------------
# Running one window
# ---------------------------------------------------------------------------


def _build_strategy(
    config: StrategyConfig,
    tickers: list[str],
    warmup: pd.DataFrame,
    risk_free_rate: float,
    seed: int | None,
    cache: dict | None,
):
    if config.kind == "constant_mix":
        weights = config.target_weights or {t: 1.0 / len(tickers) for t in tickers}
        return constant_mix_strategy(weights), None

    strategy = WalkForwardStrategy(
        warmup_prices=warmup,
        lookback_days=config.lookback_days,
        max_weight_per_asset=config.max_weight_per_asset,
        objective=config.objective,
        risk_free_rate=risk_free_rate,
        allow_short=config.allow_short,
        seed=seed,
        cache=cache,
    )
    return strategy, strategy


def _run_one_window(
    index: int,
    portfolio_prices: pd.DataFrame,
    benchmark_prices: pd.DataFrame | None,
    warmup: pd.DataFrame,
    config: StrategyConfig,
    initial_capital: float,
    rebalance_frequency: str,
    transaction_cost_bps: float,
    risk_free_rate: float,
    seed: int | None,
    cache: dict | None,
) -> WindowResult:
    """Strategy and both baselines over one identical slice of data."""
    tickers = list(portfolio_prices.columns)
    trading_dates = [d.date() for d in portfolio_prices.index]
    schedule = rebalance_calendar(trading_dates, rebalance_frequency)

    strategy_fn, walk_forward = _build_strategy(
        config, tickers, warmup, risk_free_rate, seed, cache
    )

    equal = {t: 1.0 / len(tickers) for t in tickers}
    hold_weights = config.target_weights or equal

    strategy_run = run_backtest(
        portfolio_prices, strategy_fn, initial_capital, schedule, transaction_cost_bps
    )
    hold_run = run_backtest(
        portfolio_prices,
        constant_mix_strategy(hold_weights),
        initial_capital,
        [trading_dates[0]],
        transaction_cost_bps,
    )

    metrics = {
        "strategy": compute_metrics(strategy_run, initial_capital, risk_free_rate),
        "buy_and_hold_same_stocks": compute_metrics(
            hold_run, initial_capital, risk_free_rate
        ),
    }

    if benchmark_prices is not None:
        benchmark_run = run_backtest(
            benchmark_prices,
            constant_mix_strategy({BENCHMARK_TICKER: 1.0}),
            initial_capital,
            [trading_dates[0]],
            transaction_cost_bps,
        )
        metrics["buy_and_hold_nifty50"] = compute_metrics(
            benchmark_run, initial_capital, risk_free_rate
        )

    return WindowResult(
        index=index,
        start_date=trading_dates[0],
        end_date=trading_dates[-1],
        trading_days=len(trading_dates),
        metrics=metrics,
        n_rebalances=strategy_run.n_rebalances,
        n_optimizer_failures=(
            walk_forward.telemetry.n_optimizer_failures if walk_forward else 0
        ),
        average_turnover=average_turnover(strategy_run, portfolio_prices),
    )


# ---------------------------------------------------------------------------
# Aggregation
# ---------------------------------------------------------------------------


def _summarise(values: list[float]) -> MetricSummary:
    array = np.asarray(values, dtype=np.float64)
    array = array[np.isfinite(array)]
    if array.size == 0:
        return MetricSummary(0.0, 0.0, 0.0, 0.0, 0.0, 0)
    return MetricSummary(
        median=float(np.median(array)),
        p5=float(np.percentile(array, 5)),
        p95=float(np.percentile(array, 95)),
        min=float(array.min()),
        max=float(array.max()),
        n_windows=int(array.size),
    )


def _summarise_all(windows: list[WindowResult]) -> dict[str, dict[str, MetricSummary]]:
    summaries: dict[str, dict[str, MetricSummary]] = {}
    for key in SERIES_KEYS:
        present = [w for w in windows if key in w.metrics]
        if not present:
            continue
        summaries[key] = {
            field_name: _summarise(
                [getattr(w.metrics[key], field_name) for w in present]
            )
            for field_name in METRIC_FIELDS
        }
    return summaries


def _win_rates(windows: list[WindowResult]) -> WinRates:
    """Paired win rate: compared window by window, never distribution vs point."""
    def rate(baseline: str, attribute: str) -> float:
        paired = [
            w for w in windows
            if "strategy" in w.metrics and baseline in w.metrics
        ]
        if not paired:
            return 0.0
        wins = sum(
            getattr(w.metrics["strategy"], attribute)
            > getattr(w.metrics[baseline], attribute)
            for w in paired
        )
        return wins / len(paired)

    paired_count = len(
        [w for w in windows if "buy_and_hold_nifty50" in w.metrics]
    )
    return WinRates(
        vs_nifty50_cagr=rate("buy_and_hold_nifty50", "cagr"),
        vs_nifty50_sharpe=rate("buy_and_hold_nifty50", "sharpe_ratio"),
        vs_buy_and_hold_cagr=rate("buy_and_hold_same_stocks", "cagr"),
        vs_buy_and_hold_sharpe=rate("buy_and_hold_same_stocks", "sharpe_ratio"),
        n_windows=paired_count,
    )


# ---------------------------------------------------------------------------
# Entry point
# ---------------------------------------------------------------------------


def bootstrap_backtest(
    db: Session,
    tickers: list[str],
    config: StrategyConfig,
    window_years: float = 5.0,
    initial_capital: float = 1_000_000.0,
    rebalance_frequency: str = "monthly",
    transaction_cost_bps: float = DEFAULT_TRANSACTION_COST_BPS,
    risk_free_rate: float = DEFAULT_RISK_FREE_RATE,
    method: Method = "rolling_windows",
    n_resamples: int = 200,
    expected_block_days: int = DEFAULT_EXPECTED_BLOCK_DAYS,
    seed: int | None = 0,
) -> BootstrapResult:
    """Run a strategy and both baselines over many windows or resamples."""
    symbols: list[str] = []
    for raw in tickers:
        symbol = to_nse_symbol(raw)
        if symbol not in symbols:
            symbols.append(symbol)
    if not symbols:
        raise InvalidParameterError("At least one ticker is required")

    # One matrix over all available history, sliced per window. The benchmark is
    # inner-joined here so every window shares one calendar, exactly as a single
    # Phase 5 backtest does.
    full_range_start = dt.date(1990, 1, 1)
    full_range_end = dt.date.today() + dt.timedelta(days=1)

    benchmark_available = True
    try:
        full = _widest_matrix(db, [*symbols, BENCHMARK_TICKER], full_range_start, full_range_end)
    except (InsufficientCoverageError, ValueError):
        benchmark_available = False
        full = _widest_matrix(db, symbols, full_range_start, full_range_end)

    portfolio_full = full[symbols]
    benchmark_full = full[[BENCHMARK_TICKER]] if benchmark_available else None

    if method == "rolling_windows":
        result = _rolling(
            portfolio_full, benchmark_full, config, window_years, initial_capital,
            rebalance_frequency, transaction_cost_bps, risk_free_rate, seed,
        )
    elif method == "block_bootstrap":
        result = _block(
            portfolio_full, benchmark_full, config, window_years, initial_capital,
            rebalance_frequency, transaction_cost_bps, risk_free_rate,
            n_resamples, expected_block_days, seed,
        )
    else:
        raise InvalidParameterError(
            f"method must be 'rolling_windows' or 'block_bootstrap', got {method!r}"
        )

    result.tickers = symbols
    result.strategy_kind = config.kind
    return result


def _widest_matrix(
    db: Session, symbols: list[str], start: dt.date, end: dt.date
) -> pd.DataFrame:
    """Build the price matrix over whatever range the data actually supports."""
    from sqlalchemy import func, select

    from app.models import DailyPrice, Security

    stmt = (
        select(Security.ticker, func.min(DailyPrice.date), func.max(DailyPrice.date))
        .select_from(Security)
        .join(DailyPrice, DailyPrice.security_id == Security.id)
        .where(Security.ticker.in_(symbols))
        .group_by(Security.ticker)
    )
    bounds = {t: (f, l) for t, f, l in db.execute(stmt)}
    missing = [s for s in symbols if s not in bounds]
    if missing:
        raise InsufficientCoverageError(
            f"No price history for: {', '.join(sorted(missing))}"
        )

    widest_start = max(f for f, _ in bounds.values())
    widest_end = min(l for _, l in bounds.values())
    return build_price_matrix(db, symbols, max(start, widest_start), min(end, widest_end))


def _rolling(
    portfolio_full: pd.DataFrame,
    benchmark_full: pd.DataFrame | None,
    config: StrategyConfig,
    window_years: float,
    initial_capital: float,
    rebalance_frequency: str,
    transaction_cost_bps: float,
    risk_free_rate: float,
    seed: int | None,
) -> BootstrapResult:
    trading_dates = [d.date() for d in portfolio_full.index]
    warmup_days = config.warmup_days()
    spans = rolling_windows(trading_dates, window_years, warmup_days)

    if not spans:
        raise InsufficientLookbackError(
            f"No {window_years:g}-year window fits in the available data "
            f"({trading_dates[0]} to {trading_dates[-1]})"
            + (
                f" after reserving {warmup_days} trading days of walk-forward "
                "lookback"
                if warmup_days
                else ""
            )
            + ". Shorten window_years, reduce lookback_days, or ingest more history."
        )

    # Shared across windows: at a given rebalance date the trailing window is
    # fixed by the data, so the optimum is the same in every window containing
    # it. This turns thousands of solves into a few hundred.
    cache: dict = {}
    windows: list[WindowResult] = []
    skipped: list[str] = []

    for index, (start, end) in enumerate(spans):
        mask = (portfolio_full.index >= pd.Timestamp(start)) & (
            portfolio_full.index <= pd.Timestamp(end)
        )
        slice_prices = portfolio_full[mask]
        if len(slice_prices) < 2:
            skipped.append(f"{start} to {end}: fewer than 2 trading days")
            continue

        warmup = portfolio_full[portfolio_full.index < slice_prices.index[0]]
        if warmup_days:
            warmup = warmup.tail(warmup_days)

        benchmark_slice = (
            benchmark_full[mask] if benchmark_full is not None else None
        )

        try:
            windows.append(
                _run_one_window(
                    index, slice_prices, benchmark_slice, warmup, config,
                    initial_capital, rebalance_frequency, transaction_cost_bps,
                    risk_free_rate, seed, cache,
                )
            )
        except InsufficientLookbackError as exc:
            skipped.append(f"{start} to {end}: {exc}")

    if not windows:
        raise InsufficientLookbackError(
            "Every candidate window was skipped: " + "; ".join(skipped[:3])
        )

    return _assemble(
        "rolling_windows", window_years, windows, skipped,
        data_start=trading_dates[0],
        data_end=trading_dates[-1],
        data_trading_days=len(trading_dates),
    )


def _block(
    portfolio_full: pd.DataFrame,
    benchmark_full: pd.DataFrame | None,
    config: StrategyConfig,
    window_years: float,
    initial_capital: float,
    rebalance_frequency: str,
    transaction_cost_bps: float,
    risk_free_rate: float,
    n_resamples: int,
    expected_block_days: int,
    seed: int | None,
) -> BootstrapResult:
    """Resample blocks of days, rebuild prices, rerun the same engine."""
    combined = portfolio_full
    if benchmark_full is not None:
        combined = pd.concat([portfolio_full, benchmark_full], axis=1)

    returns = combined.pct_change().dropna()
    if len(returns) < expected_block_days * 2:
        raise InsufficientCoverageError(
            f"Only {len(returns)} return observations; too few to block bootstrap"
        )

    window_days = int(round(window_years * 252))
    warmup_days = config.warmup_days()
    total_days = window_days + warmup_days
    if total_days < 2:
        raise InvalidParameterError("window_years is too small to simulate")

    rng = np.random.default_rng(seed)
    columns = list(combined.columns)
    portfolio_columns = list(portfolio_full.columns)

    # Synthetic calendar: real business days, so the rebalance calendar and the
    # annualisation in the metrics behave as they do on real data.
    calendar = pd.bdate_range("2000-01-03", periods=total_days + 1, name="date")

    windows: list[WindowResult] = []
    skipped: list[str] = []
    matrix = returns.to_numpy(dtype=np.float64)

    for index in range(n_resamples):
        picks = stationary_block_indices(
            len(matrix), total_days, rng, expected_block_days
        )
        path = np.vstack([np.ones((1, len(columns))), 1.0 + matrix[picks]])
        prices = pd.DataFrame(
            100.0 * np.cumprod(path, axis=0), index=calendar, columns=columns
        )

        # Warmup must carry exactly the portfolio's columns: `prices` also holds
        # the benchmark, and a column the engine never supplies would be all-NaN
        # once concatenated, silently emptying the trailing window.
        portfolio_prices = prices[portfolio_columns]
        warmup = (
            portfolio_prices.iloc[:warmup_days]
            if warmup_days
            else portfolio_prices.iloc[:0]
        )
        simulated = prices.iloc[warmup_days:]

        benchmark_slice = (
            simulated[[BENCHMARK_TICKER]] if benchmark_full is not None else None
        )
        try:
            windows.append(
                _run_one_window(
                    index, simulated[portfolio_columns], benchmark_slice, warmup,
                    config, initial_capital, rebalance_frequency,
                    transaction_cost_bps, risk_free_rate, seed,
                    # Each resample is different data, so no cache sharing.
                    None,
                )
            )
        except InsufficientLookbackError as exc:
            skipped.append(f"resample {index}: {exc}")

    if not windows:
        raise InsufficientLookbackError(
            "Every resample was skipped: " + "; ".join(skipped[:3])
        )
    # For resamples the underlying span is the real history they were drawn from.
    source_dates = [d.date() for d in combined.index]
    return _assemble(
        "block_bootstrap", window_years, windows, skipped,
        data_start=source_dates[0],
        data_end=source_dates[-1],
        data_trading_days=len(source_dates),
    )


def _assemble(
    method: Method,
    window_years: float,
    windows: list[WindowResult],
    skipped: list[str],
    data_start: dt.date | None = None,
    data_end: dt.date | None = None,
    data_trading_days: int = 0,
) -> BootstrapResult:
    turnovers = [w.average_turnover for w in windows if w.average_turnover > 0]
    return BootstrapResult(
        method=method,
        window_years=window_years,
        n_windows=len(windows),
        tickers=[],
        strategy_kind="",
        summaries=_summarise_all(windows),
        win_rates=_win_rates(windows),
        windows=windows,
        total_rebalances=sum(w.n_rebalances for w in windows),
        total_optimizer_failures=sum(w.n_optimizer_failures for w in windows),
        average_turnover_per_rebalance=float(np.mean(turnovers)) if turnovers else 0.0,
        skipped_windows=skipped,
        data_start=data_start,
        data_end=data_end,
        data_trading_days=data_trading_days,
    )
