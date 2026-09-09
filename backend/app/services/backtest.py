"""Day-by-day portfolio backtesting.

Design commitments, because this is the part where a subtle mistake produces a
believable-looking wrong answer:

**Shares are the state, weights are derived.** The simulation tracks share
counts, not weights. Between rebalances a portfolio's weights drift because its
holdings move at different rates -- tracking shares makes that drift fall out
naturally, instead of being a rule someone has to remember to apply. It also
makes "no phantom trading" structural: if no trade is executed, the share
vector is simply not reassigned.

**The strategy is a seam, not a branch.** The loop calls a ``rebalance_fn`` on
rebalance dates and hands it only the price history dated on or before that day.
It never inspects what the strategy is. A new strategy plugs in without this
file changing.

**Prices are rebuilt from cleaned returns.** The engine does not read
``adj_close`` directly. Each series is anchored at its first ``adj_close`` in
the window and compounded forward using :func:`get_daily_returns`, so the
``price_anomalies`` overlay applies here exactly as it does everywhere else.
Without this a TMPV backtest would book a 40% loss on the demerger date that no
holder actually suffered. For a security with no registered anomalies the
reconstruction reproduces ``adj_close`` exactly.
"""

from __future__ import annotations

import datetime as dt
import logging
from collections.abc import Callable, Sequence
from dataclasses import dataclass, field

import numpy as np
import pandas as pd
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.core.errors import (
    InsufficientCoverageError,
    InvalidParameterError,
    InvalidWeightsError,
    StrategyContractError,
    UnknownTickerError,
)
from app.core.tickers import to_nse_symbol
from app.models import DailyPrice, Security
from app.services.markowitz import (  # single source of truth for the rate
    DEFAULT_RISK_FREE_RATE,
    TRADING_DAYS_PER_YEAR,
)
from app.services.returns import get_daily_returns

logger = logging.getLogger(__name__)

#: 15 bps per trade leg approximates, for a delivery trade through an Indian
#: discount broker: STT (~10 bps on the sell leg), exchange transaction charges,
#: SEBI turnover fees, stamp duty and GST, plus a small allowance for slippage.
#: It does NOT include brokerage beyond that, nor capital gains tax, nor market
#: impact for large orders. A real book will pay more than this, not less.
DEFAULT_TRANSACTION_COST_BPS = 15.0

BENCHMARK_TICKER = "^NSEI"

_WEIGHT_TOLERANCE = 1e-6

#: ``(as_of_date, price_history_through_as_of_date) -> {ticker: weight}``
RebalanceFn = Callable[[dt.date, pd.DataFrame], dict[str, float]]


# ---------------------------------------------------------------------------
# Results
# ---------------------------------------------------------------------------


@dataclass
class BacktestRun:
    """The full simulated path. ``values`` is what the caller usually wants."""

    values: pd.Series                 # date -> portfolio value
    shares: pd.DataFrame              # date x ticker -> share count held
    cash: pd.Series                   # date -> uninvested residual
    costs: pd.Series                  # date -> transaction cost paid that day
    rebalance_dates: list[dt.date] = field(default_factory=list)

    @property
    def total_cost(self) -> float:
        return float(self.costs.sum())

    @property
    def n_rebalances(self) -> int:
        """Rebalance events executed, including the day-one purchase."""
        return len(self.rebalance_dates)


@dataclass
class BacktestMetrics:
    cagr: float
    annualised_return: float
    annualised_volatility: float
    sharpe_ratio: float
    sortino_ratio: float
    max_drawdown: float
    max_drawdown_peak_date: dt.date | None
    max_drawdown_trough_date: dt.date | None
    total_transaction_costs_inr: float
    total_transaction_costs_pct: float
    n_rebalances: int
    start_value: float
    end_value: float
    total_return: float


# ---------------------------------------------------------------------------
# Price data
# ---------------------------------------------------------------------------


def _coverage_bounds(
    db: Session, symbols: Sequence[str]
) -> dict[str, tuple[dt.date, dt.date]]:
    stmt = (
        select(Security.ticker, func.min(DailyPrice.date), func.max(DailyPrice.date))
        .select_from(Security)
        .join(DailyPrice, DailyPrice.security_id == Security.id)
        .where(Security.ticker.in_(list(symbols)))
        .group_by(Security.ticker)
    )
    return {ticker: (first, last) for ticker, first, last in db.execute(stmt)}


def build_price_matrix(
    db: Session,
    tickers: Sequence[str],
    start_date: dt.date,
    end_date: dt.date,
) -> pd.DataFrame:
    """Aligned price series for a backtest, anomaly-corrected.

    Coverage is validated strictly: every ticker must have data spanning the
    entire ``[start_date, end_date]`` range or this raises.

    This is deliberately unlike Phases 3 and 4, which shrink their window to
    the shortest available history. There, the window is an estimation detail
    and a shorter one is merely less precise. Here the window is the *question*
    -- "how would this have performed from A to B" -- and quietly answering a
    different question, over a shorter period, would make the strategy and its
    baselines incomparable and the headline numbers wrong. A backtest that
    cannot cover the requested period must fail loudly.
    """
    if start_date >= end_date:
        raise InvalidParameterError(
            f"start_date ({start_date}) must be before end_date ({end_date})"
        )

    symbols: list[str] = []
    for raw in tickers:
        symbol = to_nse_symbol(raw)
        if symbol not in symbols:
            symbols.append(symbol)
    if not symbols:
        raise InvalidParameterError("At least one ticker is required")

    bounds = _coverage_bounds(db, symbols)

    missing = [s for s in symbols if s not in bounds]
    if missing:
        known = set(
            db.scalars(select(Security.ticker).where(Security.ticker.in_(missing))).all()
        )
        unknown = [s for s in missing if s not in known]
        if unknown:
            raise UnknownTickerError(f"Unknown ticker(s): {', '.join(sorted(unknown))}")
        raise InsufficientCoverageError(
            f"No price history at all for: {', '.join(sorted(missing))}"
        )

    shortfalls = []
    for symbol in symbols:
        first, last = bounds[symbol]
        if first > start_date or last < end_date:
            shortfalls.append(
                f"{symbol} has data from {first} to {last}, which does not cover "
                f"the requested {start_date} to {end_date}"
            )
    if shortfalls:
        raise InsufficientCoverageError(
            "Backtest window is not fully covered by the available price data. "
            + " | ".join(shortfalls)
            + ". Narrow the window, or drop the ticker(s) — the window is not "
            "shrunk automatically because that would silently answer a "
            "different question than the one asked."
        )

    columns: list[pd.Series] = []
    for symbol in symbols:
        columns.append(_reconstruct_prices(db, symbol, start_date, end_date))

    frame = pd.concat(columns, axis=1, join="inner").dropna()
    if len(frame) < 2:
        raise InsufficientCoverageError(
            f"Only {len(frame)} common trading day(s) across "
            f"{', '.join(symbols)} between {start_date} and {end_date}"
        )
    return frame


def _reconstruct_prices(
    db: Session, symbol: str, start_date: dt.date, end_date: dt.date
) -> pd.Series:
    """Anchor at the first adj_close, then compound cleaned daily returns."""
    security = db.scalar(select(Security).where(Security.ticker == symbol))
    if security is None:
        raise UnknownTickerError(f"Unknown ticker: {symbol}")

    rows = db.execute(
        select(DailyPrice.date, DailyPrice.adj_close)
        .where(
            DailyPrice.security_id == security.id,
            DailyPrice.adj_close.is_not(None),
            DailyPrice.date >= start_date,
            DailyPrice.date <= end_date,
        )
        .order_by(DailyPrice.date)
    ).all()
    if len(rows) < 2:
        raise InsufficientCoverageError(
            f"{symbol} has {len(rows)} usable price bar(s) between "
            f"{start_date} and {end_date}"
        )

    index = pd.DatetimeIndex([d for d, _ in rows], name="date")
    anchor = float(rows[0][1])

    returns = get_daily_returns(db, symbol, start_date, end_date)
    if not returns.index.equals(index[1:]):
        # The two queries must see the same bars; if they ever diverge that is
        # an internal invariant violation, not a caller error — deliberately a
        # RuntimeError so it surfaces as a 500 and gets logged with a traceback.
        raise RuntimeError(
            f"{symbol}: return series does not align with the price bars "
            f"({len(returns)} returns for {len(index) - 1} price steps)"
        )

    values = np.empty(len(index), dtype=np.float64)
    values[0] = anchor
    values[1:] = anchor * np.cumprod(1.0 + returns.to_numpy(dtype=np.float64))

    series = pd.Series(values, index=index, name=symbol)
    return series


# ---------------------------------------------------------------------------
# Rebalance calendar
# ---------------------------------------------------------------------------


def rebalance_calendar(
    trading_dates: Sequence[dt.date], frequency: str
) -> list[dt.date]:
    """Rebalance dates drawn from the calendar of actually-traded days.

    "monthly" is the first *traded* day of each calendar month, "quarterly" the
    first traded day of each calendar quarter. The first day of the backtest is
    always included: that is the initial purchase.
    """
    if not trading_dates:
        return []

    frequency = frequency.lower().strip()
    if frequency == "monthly":
        def period(day: dt.date) -> tuple[int, int]:
            return (day.year, day.month)
    elif frequency == "quarterly":
        def period(day: dt.date) -> tuple[int, int]:
            return (day.year, (day.month - 1) // 3)
    else:
        raise InvalidParameterError(
            f"Unsupported rebalance_frequency {frequency!r}; "
            "expected 'monthly' or 'quarterly'"
        )

    chosen = {trading_dates[0]}
    seen: set[tuple[int, int]] = set()
    for day in trading_dates:
        key = period(day)
        if key not in seen:
            seen.add(key)
            chosen.add(day)
    return sorted(chosen)


# ---------------------------------------------------------------------------
# Strategies
# ---------------------------------------------------------------------------


def constant_mix_strategy(target_weights: dict[str, float]) -> RebalanceFn:
    """Always rebalance back to the same fixed weights.

    The generalisation of "rebalance to 60/40 every month" to any fixed mix.
    Ignores both the date and the history, which is exactly what makes it a
    constant mix.
    """
    frozen = {to_nse_symbol(t): float(w) for t, w in target_weights.items()}
    total = sum(frozen.values())
    if abs(total - 1.0) > _WEIGHT_TOLERANCE:
        raise InvalidWeightsError(
            f"target_weights must sum to 1.0, got {total:.6f} ({frozen})",
            details={"weights": frozen, "sum": total},
        )

    def rebalance(as_of: dt.date, history: pd.DataFrame) -> dict[str, float]:
        return dict(frozen)

    return rebalance


# ---------------------------------------------------------------------------
# The engine
# ---------------------------------------------------------------------------


def _weights_vector(
    raw: dict[str, float], tickers: Sequence[str], as_of: dt.date
) -> np.ndarray:
    """Validate a strategy's output and project it onto the ticker order."""
    if not isinstance(raw, dict):
        raise StrategyContractError(
            f"rebalance_fn returned {type(raw).__name__} on {as_of}; expected a dict"
        )

    normalised = {to_nse_symbol(t): float(w) for t, w in raw.items()}
    unknown = set(normalised) - set(tickers)
    if unknown:
        raise StrategyContractError(
            f"rebalance_fn returned weights on {as_of} for tickers not in the "
            f"backtest: {', '.join(sorted(unknown))}"
        )

    vector = np.array([normalised.get(t, 0.0) for t in tickers], dtype=np.float64)
    if not np.all(np.isfinite(vector)):
        raise StrategyContractError(
            f"rebalance_fn returned a non-finite weight on {as_of}: {normalised}"
        )
    total = float(vector.sum())
    if abs(total - 1.0) > 1e-4:
        raise StrategyContractError(
            f"rebalance_fn weights on {as_of} sum to {total:.6f}, not 1.0: "
            f"{normalised}"
        )
    return vector


def run_backtest(
    prices: pd.DataFrame,
    rebalance_fn: RebalanceFn,
    initial_capital: float,
    rebalance_dates: Sequence[dt.date],
    transaction_cost_bps: float = DEFAULT_TRANSACTION_COST_BPS,
) -> BacktestRun:
    """Walk the price history forward one day at a time.

    The loop reads only ``prices`` up to the current row, so appending later
    data cannot change any earlier day. That is asserted directly by the
    truncation-invariance test.
    """
    if prices.empty:
        raise InvalidParameterError("prices is empty")
    if initial_capital <= 0:
        raise InvalidParameterError(f"initial_capital must be positive, got {initial_capital}")
    if transaction_cost_bps < 0:
        raise InvalidParameterError(
            f"transaction_cost_bps must not be negative, got {transaction_cost_bps}"
        )

    tickers = list(prices.columns)
    matrix = prices.to_numpy(dtype=np.float64)
    if not np.all(np.isfinite(matrix)) or np.any(matrix <= 0):
        raise InvalidParameterError("prices must be finite and strictly positive")

    index = prices.index
    as_dates = [d.date() if hasattr(d, "date") else d for d in index]
    scheduled = {d.date() if hasattr(d, "date") else d for d in rebalance_dates}
    rate = transaction_cost_bps / 10_000.0

    n_assets = len(tickers)
    shares = np.zeros(n_assets, dtype=np.float64)
    cash = float(initial_capital)

    values = np.empty(len(index), dtype=np.float64)
    costs = np.zeros(len(index), dtype=np.float64)
    cash_series = np.empty(len(index), dtype=np.float64)
    share_history = np.empty((len(index), n_assets), dtype=np.float64)
    executed: list[dt.date] = []

    for position, today in enumerate(as_dates):
        px = matrix[position]

        # Mark to market at today's close, before any trading.
        pre_value = cash + float(shares @ px)

        if today in scheduled:
            # The strategy sees only data dated on or before today. A copy, so
            # a careless callable cannot reach back into the parent frame.
            history = prices.iloc[: position + 1].copy()
            weights = _weights_vector(rebalance_fn(today, history), tickers, today)

            # Costs come out of the same pot being invested, so the target is
            # solved in two passes: size the trades, price the cost, then resize
            # against what is actually left. Bookkeeping stays exact either way.
            target_shares = (pre_value * weights) / px
            cost = float(np.abs(target_shares - shares) @ px) * rate
            investable = pre_value - cost
            target_shares = (investable * weights) / px
            cost = float(np.abs(target_shares - shares) @ px) * rate

            shares = target_shares
            cash = pre_value - float(shares @ px) - cost
            costs[position] = cost
            executed.append(today)

        values[position] = cash + float(shares @ px)
        cash_series[position] = cash
        share_history[position] = shares

    return BacktestRun(
        values=pd.Series(values, index=index, name="value"),
        shares=pd.DataFrame(share_history, index=index, columns=tickers),
        cash=pd.Series(cash_series, index=index, name="cash"),
        costs=pd.Series(costs, index=index, name="cost"),
        rebalance_dates=executed,
    )


# ---------------------------------------------------------------------------
# Metrics
# ---------------------------------------------------------------------------


def compute_metrics(
    run: BacktestRun,
    initial_capital: float,
    risk_free_rate: float = DEFAULT_RISK_FREE_RATE,
    trading_days: int = TRADING_DAYS_PER_YEAR,
) -> BacktestMetrics:
    """Performance statistics for one run.

    Applied identically to the strategy and both baselines so the three are
    directly comparable.

    ``annualised_return`` is the arithmetic mean daily return times 252, which
    is the convention :mod:`app.services.markowitz` uses, so a Sharpe ratio here
    is comparable with one from the optimiser. ``cagr`` is the geometric figure
    -- what the portfolio actually compounded at -- and is the one to quote as
    performance.
    """
    values = run.values
    if len(values) < 2:
        raise InvalidParameterError("Need at least 2 valuation points to compute metrics")

    start_value = float(values.iloc[0])
    end_value = float(values.iloc[-1])
    daily = values.pct_change().dropna()

    start_date = values.index[0]
    end_date = values.index[-1]
    years = (end_date - start_date).days / 365.25

    if years > 0 and start_value > 0 and end_value > 0:
        cagr = (end_value / start_value) ** (1.0 / years) - 1.0
    else:
        cagr = 0.0

    annualised_return = float(daily.mean()) * trading_days
    annualised_volatility = float(daily.std(ddof=1)) * np.sqrt(trading_days)

    excess = annualised_return - risk_free_rate
    sharpe = excess / annualised_volatility if annualised_volatility > 0 else 0.0

    # Sortino: penalise only shortfalls against the minimum acceptable return,
    # which is the same risk-free rate the Sharpe ratio uses, expressed daily.
    # The mean is taken over ALL observations (the standard definition), not
    # only the negative ones.
    daily_mar = risk_free_rate / trading_days
    shortfall = np.minimum(daily.to_numpy(dtype=np.float64) - daily_mar, 0.0)
    downside_deviation = float(np.sqrt(np.mean(shortfall**2))) * np.sqrt(trading_days)
    sortino = excess / downside_deviation if downside_deviation > 0 else 0.0

    running_peak = values.cummax()
    drawdown = values / running_peak - 1.0
    max_drawdown = float(drawdown.min())
    trough_date = drawdown.idxmin()
    # The peak is the high-water mark that this trough is measured against.
    peak_date = values.loc[:trough_date].idxmax()

    return BacktestMetrics(
        cagr=cagr,
        annualised_return=annualised_return,
        annualised_volatility=annualised_volatility,
        sharpe_ratio=sharpe,
        sortino_ratio=sortino,
        max_drawdown=max_drawdown,
        max_drawdown_peak_date=peak_date.date() if peak_date is not None else None,
        max_drawdown_trough_date=trough_date.date() if trough_date is not None else None,
        total_transaction_costs_inr=run.total_cost,
        total_transaction_costs_pct=run.total_cost / initial_capital * 100.0,
        n_rebalances=run.n_rebalances,
        start_value=start_value,
        end_value=end_value,
        total_return=end_value / start_value - 1.0 if start_value else 0.0,
    )


# ---------------------------------------------------------------------------
# Orchestration: strategy plus both baselines
# ---------------------------------------------------------------------------


@dataclass
class BacktestComparison:
    """One strategy run and the two baselines it is judged against."""

    strategy: BacktestRun
    strategy_metrics: BacktestMetrics
    buy_and_hold_same_stocks: BacktestRun
    buy_and_hold_same_stocks_metrics: BacktestMetrics
    buy_and_hold_nifty50: BacktestRun | None
    buy_and_hold_nifty50_metrics: BacktestMetrics | None
    start_date: dt.date
    end_date: dt.date
    trading_days: int
    rebalance_frequency: str
    transaction_cost_bps: float
    risk_free_rate: float
    benchmark_unavailable_reason: str | None = None


def run_strategy_with_baselines(
    db: Session,
    tickers: Sequence[str],
    target_weights: dict[str, float],
    start_date: dt.date,
    end_date: dt.date,
    initial_capital: float,
    rebalance_frequency: str = "monthly",
    transaction_cost_bps: float = DEFAULT_TRANSACTION_COST_BPS,
    risk_free_rate: float = DEFAULT_RISK_FREE_RATE,
    rebalance_fn: RebalanceFn | None = None,
) -> BacktestComparison:
    """Run the strategy and both baselines over one common window.

    ``rebalance_fn`` defaults to a constant mix of ``target_weights``; passing
    another callable is how a different strategy plugs in.

    All three series are run over **one shared calendar**, built by inner-joining
    the portfolio tickers with the benchmark. The index does not trade on quite
    the same days as every constituent -- NSE holds occasional special sessions
    that ^NSEI does not print -- and running the three on different calendars
    would compute their CAGR, volatility and drawdown over different day sets,
    making the comparison invalid. Including the benchmark therefore drops a
    handful of days from the strategy's calendar; that is the price of a like-
    for-like comparison, and it is stated on the response. If the benchmark is
    unavailable, the strategy falls back to the portfolio-only calendar and the
    response says why.
    """
    portfolio_symbols: list[str] = []
    for raw in tickers:
        symbol = to_nse_symbol(raw)
        if symbol not in portfolio_symbols:
            portfolio_symbols.append(symbol)

    benchmark_prices: pd.DataFrame | None = None
    reason: str | None = None
    try:
        combined = build_price_matrix(
            db, [*portfolio_symbols, BENCHMARK_TICKER], start_date, end_date
        )
        prices = combined[portfolio_symbols]
        benchmark_prices = combined[[BENCHMARK_TICKER]]
    except (InsufficientCoverageError, UnknownTickerError) as exc:
        # A missing benchmark degrades the comparison but must not fail the
        # backtest the caller actually asked for.
        reason = str(exc)
        logger.warning("Benchmark baseline unavailable: %s", exc)
        prices = build_price_matrix(db, portfolio_symbols, start_date, end_date)

    trading_dates = [d.date() for d in prices.index]
    schedule = rebalance_calendar(trading_dates, rebalance_frequency)

    strategy_fn = rebalance_fn or constant_mix_strategy(target_weights)
    strategy = run_backtest(
        prices, strategy_fn, initial_capital, schedule, transaction_cost_bps
    )

    # Same holdings, bought once and left alone: only the day-one purchase
    # trades, so weights drift with the market from then on.
    hold_same = run_backtest(
        prices,
        constant_mix_strategy(target_weights),
        initial_capital,
        [trading_dates[0]],
        transaction_cost_bps,
    )

    benchmark: BacktestRun | None = None
    benchmark_metrics: BacktestMetrics | None = None
    if benchmark_prices is not None:
        benchmark = run_backtest(
            benchmark_prices,
            constant_mix_strategy({BENCHMARK_TICKER: 1.0}),
            initial_capital,
            [trading_dates[0]],
            transaction_cost_bps,
        )
        benchmark_metrics = compute_metrics(benchmark, initial_capital, risk_free_rate)

    return BacktestComparison(
        strategy=strategy,
        strategy_metrics=compute_metrics(strategy, initial_capital, risk_free_rate),
        buy_and_hold_same_stocks=hold_same,
        buy_and_hold_same_stocks_metrics=compute_metrics(
            hold_same, initial_capital, risk_free_rate
        ),
        buy_and_hold_nifty50=benchmark,
        buy_and_hold_nifty50_metrics=benchmark_metrics,
        start_date=trading_dates[0],
        end_date=trading_dates[-1],
        trading_days=len(trading_dates),
        rebalance_frequency=rebalance_frequency,
        transaction_cost_bps=transaction_cost_bps,
        risk_free_rate=risk_free_rate,
        benchmark_unavailable_reason=reason,
    )
