"""The one place return series come from.

**Every phase that needs returns -- Monte Carlo VaR/CVaR, Markowitz
optimisation, the backtester -- must call :func:`get_daily_returns`. Do not read
``daily_prices`` and compute percentage changes directly.**

Why this rule exists
--------------------
Some raw price changes are not returns. A demerger or an unadjusted corporate
action moves the printed price without moving the value of a held position: the
ingested series shows TMPV falling 40.2% on 2025-10-14 and TRENT falling 33.0%
on 2026-01-01, and neither cost a holder anything. Fed into a covariance matrix
those artefacts inflate volatility, distort correlations, and hand a backtester
a crash that never happened.

The corrections live in the ``price_anomalies`` registry rather than in the
price rows, so the raw data stays inspectable. That only helps if the overlay is
actually applied -- which is what this module guarantees. Code that bypasses it
silently reintroduces every artefact the registry was built to remove.
"""

from __future__ import annotations

import logging
from collections.abc import Sequence
from dataclasses import dataclass, field
from datetime import date, timedelta

import pandas as pd
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.models import DailyPrice, Security
from app.services.anomalies import get_excluded_dates
from app.core.errors import (
    InsufficientCoverageError,
    InvalidParameterError,
    UnknownTickerError,
)
from app.core.tickers import to_nse_symbol

logger = logging.getLogger(__name__)


def get_daily_returns(
    db: Session,
    ticker: str,
    start: date | None = None,
    end: date | None = None,
) -> pd.Series:
    """Simple daily returns for one security, with anomalies neutralised.

    Parameters
    ----------
    ticker:
        Either form is accepted (``RELIANCE`` or ``RELIANCE.NS``).
    start, end:
        Inclusive bounds. ``None`` means unbounded on that side.

    Returns
    -------
    pd.Series
        ``float64`` returns indexed by a ``DatetimeIndex`` named ``date``, in
        ascending order, named after the security's NSE ticker. Returns are
        computed from ``adj_close``.

        The first bar in the window has no predecessor, so it yields no return
        and is dropped -- a series over N bars has N-1 points.

        Any date registered in ``price_anomalies`` with ``exclude_return`` is
        forced to ``0.0`` and logged. Zero is used rather than NaN so the series
        stays dense and aligned across securities; the day contributes nothing
        to a cumulative product and nothing to a mean move.

    Raises
    ------
    UnknownTickerError
        If the ticker is not in ``securities``.
    """
    symbol = to_nse_symbol(ticker)
    security = db.scalar(select(Security).where(Security.ticker == symbol))
    if security is None:
        raise UnknownTickerError(f"Unknown ticker: {ticker}")

    stmt = (
        select(DailyPrice.date, DailyPrice.adj_close)
        .where(
            DailyPrice.security_id == security.id,
            DailyPrice.adj_close.is_not(None),
        )
        .order_by(DailyPrice.date)
    )
    if start is not None:
        stmt = stmt.where(DailyPrice.date >= start)
    if end is not None:
        stmt = stmt.where(DailyPrice.date <= end)

    rows = db.execute(stmt).all()
    if len(rows) < 2:
        logger.info(
            "[%s] %d price bar(s) in range -- not enough to derive a return",
            symbol, len(rows),
        )
        empty = pd.Series(dtype="float64", index=pd.DatetimeIndex([], name="date"))
        empty.name = symbol
        return empty

    prices = pd.Series(
        [float(adj_close) for _, adj_close in rows],
        index=pd.DatetimeIndex([d for d, _ in rows], name="date"),
        dtype="float64",
    )

    returns = prices.pct_change().dropna()

    for anomaly_date, anomaly in get_excluded_dates(db, security.id).items():
        stamp = pd.Timestamp(anomaly_date)
        if stamp not in returns.index:
            continue
        raw = float(returns.loc[stamp])
        returns.loc[stamp] = 0.0
        logger.warning(
            "[%s] %s: excluded raw return of %+.2f%% — %s",
            symbol, anomaly_date.isoformat(), raw * 100, anomaly.description,
        )

    returns.name = symbol
    returns.index.name = "date"
    return returns


# ---------------------------------------------------------------------------
# Multi-asset returns
# ---------------------------------------------------------------------------

#: ~2 years of NSE trading days.
DEFAULT_LOOKBACK_DAYS = 504


@dataclass
class ReturnsWindow:
    """Which data window a calculation actually got, and why.

    Surfaced in API responses rather than logged: a caller asking for two years
    and silently receiving eight months would otherwise have no way to know.
    """

    start: date
    end: date
    trading_days: int
    requested_lookback_days: int
    shrunk: bool
    constrained_by: str | None = None
    constraint_reason: str | None = None
    #: First date of *available price history* per ticker -- the true start of
    #: each series, not the start of the fetched window. This is what makes the
    #: `constrained_by` attribution meaningful.
    first_available_date_by_ticker: dict[str, date] = field(default_factory=dict)


def build_returns_matrix(
    db: Session,
    tickers: Sequence[str],
    lookback_days: int = DEFAULT_LOOKBACK_DAYS,
    end_date: date | None = None,
) -> tuple[pd.DataFrame, ReturnsWindow]:
    """Aligned daily returns for several securities, plus the window used.

    Every column comes from :func:`get_daily_returns`, so anomaly corrections
    are applied here too.

    Dates are inner-joined: only days on which *every* requested ticker traded
    are kept, which is what a covariance matrix needs. If a ticker is younger
    than ``lookback_days``, the window shrinks to what all of them share rather
    than dropping the ticker or raising -- and ``ReturnsWindow`` records which
    ticker was the binding constraint.
    """
    symbols: list[str] = []
    for raw in tickers:
        symbol = to_nse_symbol(raw)
        if symbol not in symbols:
            symbols.append(symbol)
    if not symbols:
        raise InvalidParameterError("At least one ticker is required")
    if lookback_days < 2:
        raise InvalidParameterError("lookback_days must be at least 2")

    # Cheap per-ticker bounds first, so the fetch below can be narrowed.
    bounds_stmt = (
        select(
            Security.ticker,
            func.min(DailyPrice.date),
            func.max(DailyPrice.date),
        )
        .select_from(Security)
        .join(DailyPrice, DailyPrice.security_id == Security.id)
        .where(Security.ticker.in_(symbols))
        .group_by(Security.ticker)
    )
    bounds = {
        ticker: (first, last) for ticker, first, last in db.execute(bounds_stmt)
    }

    missing = [s for s in symbols if s not in bounds]
    if missing:
        known = db.scalars(
            select(Security.ticker).where(Security.ticker.in_(missing))
        ).all()
        unknown = [s for s in missing if s not in known]
        if unknown:
            raise UnknownTickerError(f"Unknown ticker(s): {', '.join(sorted(unknown))}")
        raise InsufficientCoverageError(
            f"No price history for: {', '.join(sorted(missing))}. Run the ingestion.",
            details={"tickers": sorted(missing)},
        )

    window_end = min(last for _, last in bounds.values())
    if end_date is not None:
        window_end = min(window_end, end_date)

    # Fetch generously past the requested window so `lookback_days` trading days
    # are actually available after the inner join; the tail is trimmed below.
    calendar_span = int(lookback_days * 365.25 / 252 * 1.25) + 30
    fetch_start = window_end - timedelta(days=calendar_span)

    # True history start per ticker, from the bounds query -- NOT the first row
    # of the fetched slice, which would just echo `fetch_start` for any ticker
    # with a long history and misattribute the constraint.
    first_dates: dict[str, date] = {s: bounds[s][0] for s in symbols}

    columns: list[pd.Series] = []
    for symbol in symbols:
        series = get_daily_returns(db, symbol, fetch_start, window_end)
        if series.empty:
            raise InsufficientCoverageError(
                f"{symbol} has no returns in the window ending {window_end}",
                details={"ticker": symbol},
            )
        columns.append(series)

    frame = pd.concat(columns, axis=1, join="inner").dropna()
    if len(frame) < 2:
        raise InsufficientCoverageError(
            "Fewer than 2 overlapping trading days across "
            f"{', '.join(symbols)} — cannot compute returns",
            details={"tickers": symbols},
        )

    if len(frame) > lookback_days:
        frame = frame.iloc[-lookback_days:]

    shrunk = len(frame) < lookback_days
    constrained_by: str | None = None
    reason: str | None = None
    if shrunk:
        # The binding constraint is whichever ticker starts latest.
        constrained_by = max(first_dates, key=lambda t: first_dates[t])
        reason = (
            f"{constrained_by} has price history only from "
            f"{first_dates[constrained_by].isoformat()}, so the overlapping "
            f"window across all {len(symbols)} tickers is {len(frame)} trading "
            f"days rather than the {lookback_days} requested"
        )
        logger.info("Lookback window shrunk: %s", reason)

    window = ReturnsWindow(
        start=frame.index[0].date(),
        end=frame.index[-1].date(),
        trading_days=len(frame),
        requested_lookback_days=lookback_days,
        shrunk=shrunk,
        constrained_by=constrained_by,
        constraint_reason=reason,
        first_available_date_by_ticker=first_dates,
    )
    return frame, window
