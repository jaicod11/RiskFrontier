"""Market data ingestion: seed the Nifty 50 universe and pull OHLCV history.

Ticker convention
-----------------
``securities.ticker`` holds the bare NSE symbol (``RELIANCE``, ``M&M``,
``BAJAJ-AUTO``). The Yahoo Finance symbol is that plus ``settings.yfinance_suffix``
(``.NS``). The seed file stores the Yahoo form under ``yf_ticker``; everything
downstream of :func:`seed_securities` speaks the bare NSE symbol.
"""

from __future__ import annotations

import json
import logging
import time
from collections.abc import Sequence
from dataclasses import dataclass, field
from datetime import date, datetime, timedelta
from decimal import Decimal
from pathlib import Path
from typing import Any

import pandas as pd
import yfinance as yf
from sqlalchemy import func, select
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.orm import Session

from app.core.config import settings
# Re-exported so existing imports from this module keep working.
from app.core.tickers import to_nse_symbol, to_yf_symbol
from app.models import DailyPrice, Security
from app.services.anomalies import (
    LARGE_MOVE_THRESHOLD,
    LargeMove,
    scan_for_large_moves,
    seed_price_anomalies,
)

logger = logging.getLogger(__name__)

SEED_PATH = Path(__file__).resolve().parent.parent / "data" / "nifty50_seed.json"

#: NSE trades ~252 days a year; used to size the coverage expectation.
TRADING_DAYS_PER_YEAR = 252

#: Retry policy for the yfinance call. Module-level so tests can shrink them.
MAX_FETCH_ATTEMPTS = 3
INITIAL_BACKOFF_SECONDS = 2.0

#: Rows are upserted in batches to keep the bound-parameter count well under
#: Postgres' 65535 limit (9 columns x 500 = 4500 parameters).
UPSERT_CHUNK_SIZE = 500

#: The index itself, stored as a security so it shares the ingestion, anomaly
#: and returns pipeline with every constituent. "^NSEI" is Yahoo's symbol for
#: the Nifty 50 and needs no ".NS" suffix.
BENCHMARK_SECURITY: dict[str, str] = {
    "ticker": "^NSEI",
    "name": "Nifty 50",
    "sector": "Index",
    "exchange": "NSE",
}


#: Constituents whose price history is legitimately shorter than the rest.
#: Short history for these is expected and must not be read as a fetch failure.
KNOWN_SHORT_HISTORY: dict[str, str] = {
    "TMPV": "listed under this identity Oct 2025 after the Tata Motors demerger",
    "ETERNAL": "recent Nifty 50 constituent; may list later than older members",
    "INDIGO": "recent Nifty 50 constituent; may list later than older members",
    "JIOFIN": "recent Nifty 50 constituent; may list later than older members",
    "MAXHEALTH": "recent Nifty 50 constituent; may list later than older members",
    "TRENT": "recent Nifty 50 constituent; may list later than older members",
    # Observed from the ingested data: both series begin at their 2017 listing.
    "SBILIFE": "history begins at its October 2017 listing",
    "HDFCLIFE": "history begins at its November 2017 listing",
    "^NSEI": "Yahoo's Nifty 50 index history begins in September 2007",
}

#: A series is called short when it holds less than this share of the requested
#: window. Members of KNOWN_SHORT_HISTORY that came back with full history are
#: not flagged -- a recent index inclusion can still be a long-listed company.
SHORT_HISTORY_THRESHOLD_PCT = 95.0


class PriceDownloadError(RuntimeError):
    """Raised when yfinance could not be coaxed into returning data."""


@dataclass
class IngestResult:
    """Outcome of ingesting a single ticker."""

    ticker: str
    rows_written: int = 0
    first_date: date | None = None
    last_date: date | None = None
    error: str | None = None

    @property
    def ok(self) -> bool:
        return self.error is None


@dataclass
class CoverageRow:
    """One line of the post-ingest coverage report."""

    ticker: str
    first_date: date | None
    last_date: date | None
    row_count: int
    pct_of_span: float | None
    pct_of_requested: float | None
    expected_short: bool = False
    note: str = ""


@dataclass
class IngestSummary:
    """Everything :func:`ingest_all` learned, for callers and for printing."""

    securities_seeded: int = 0
    anomalies_seeded: int = 0
    results: list[IngestResult] = field(default_factory=list)
    coverage: list[CoverageRow] = field(default_factory=list)
    large_moves: list[LargeMove] = field(default_factory=list)
    years_back: int = 10

    @property
    def failures(self) -> list[IngestResult]:
        return [r for r in self.results if not r.ok]

    @property
    def total_rows_written(self) -> int:
        return sum(r.rows_written for r in self.results)


# ---------------------------------------------------------------------------
# Ticker helpers
# ---------------------------------------------------------------------------


def seed_benchmark(db: Session) -> int:
    """Upsert the benchmark index into ``securities``. Idempotent."""
    stmt = pg_insert(Security).values(
        [
            {
                "ticker": BENCHMARK_SECURITY["ticker"],
                "name": BENCHMARK_SECURITY["name"],
                "sector": BENCHMARK_SECURITY["sector"],
                "exchange": BENCHMARK_SECURITY["exchange"],
                "is_active": True,
                "is_benchmark": True,
            }
        ]
    )
    stmt = stmt.on_conflict_do_update(
        index_elements=[Security.ticker],
        set_={
            "name": stmt.excluded.name,
            "sector": stmt.excluded.sector,
            "exchange": stmt.excluded.exchange,
            "is_active": stmt.excluded.is_active,
            "is_benchmark": stmt.excluded.is_benchmark,
        },
    )
    db.execute(stmt)
    db.commit()
    logger.info("Seeded benchmark %s", BENCHMARK_SECURITY["ticker"])
    return 1


def load_seed(path: Path | None = None) -> list[dict[str, str]]:
    """Read the Nifty 50 seed file."""
    with (path or SEED_PATH).open(encoding="utf-8") as fh:
        return json.load(fh)


# ---------------------------------------------------------------------------
# Securities
# ---------------------------------------------------------------------------


def seed_securities(db: Session, path: Path | None = None) -> int:
    """Upsert every seed entry into ``securities``, matched on ticker.

    Idempotent: re-running updates name/sector/exchange in place and never
    inserts a second row for a ticker.
    """
    entries = load_seed(path)
    rows = [
        {
            "ticker": to_nse_symbol(entry["yf_ticker"]),
            "name": entry["name"],
            "sector": entry["sector"],
            "exchange": settings.default_exchange,
            "is_active": True,
            "is_benchmark": False,
        }
        for entry in entries
    ]

    stmt = pg_insert(Security).values(rows)
    stmt = stmt.on_conflict_do_update(
        index_elements=[Security.ticker],
        set_={
            "name": stmt.excluded.name,
            "sector": stmt.excluded.sector,
            "exchange": stmt.excluded.exchange,
            "is_active": stmt.excluded.is_active,
            "is_benchmark": stmt.excluded.is_benchmark,
        },
    )
    db.execute(stmt)
    db.commit()

    logger.info("Seeded %d securities from %s", len(rows), (path or SEED_PATH).name)
    return len(rows)


# ---------------------------------------------------------------------------
# Prices
# ---------------------------------------------------------------------------


def _download_with_retry(yf_symbol: str, start: date, end: date) -> pd.DataFrame:
    """Call yfinance with exponential backoff.

    An empty frame is treated as retryable: yfinance commonly answers a rate
    limit with no rows rather than an exception.
    """
    backoff = INITIAL_BACKOFF_SECONDS
    last_problem = "unknown error"

    for attempt in range(1, MAX_FETCH_ATTEMPTS + 1):
        try:
            frame = yf.download(
                yf_symbol,
                start=start,
                end=end,
                auto_adjust=True,
                progress=False,
                threads=False,
                actions=False,
            )
            if frame is not None and not frame.empty:
                return frame
            last_problem = "yfinance returned no rows"
        except Exception as exc:  # noqa: BLE001 - yfinance raises a wide variety
            last_problem = f"{type(exc).__name__}: {exc}"

        if attempt < MAX_FETCH_ATTEMPTS:
            logger.warning(
                "[%s] fetch attempt %d/%d failed (%s); retrying in %.0fs",
                yf_symbol, attempt, MAX_FETCH_ATTEMPTS, last_problem, backoff,
            )
            time.sleep(backoff)
            backoff *= 2

    raise PriceDownloadError(
        f"{yf_symbol}: all {MAX_FETCH_ATTEMPTS} attempts failed ({last_problem})"
    )


def _normalize_frame(frame: pd.DataFrame, yf_symbol: str) -> pd.DataFrame:
    """Flatten yfinance's column index and keep just the OHLCV fields."""
    if isinstance(frame.columns, pd.MultiIndex):
        # Recent yfinance returns (field, ticker) columns even for one symbol.
        try:
            frame = frame.xs(yf_symbol, axis=1, level=-1)
        except KeyError:
            frame = frame.droplevel(-1, axis=1)

    frame = frame.rename(columns=lambda c: str(c).strip().title())
    wanted = [c for c in ("Open", "High", "Low", "Close", "Volume") if c in frame.columns]
    return frame[wanted]


def _to_decimal(value: Any) -> Decimal | None:
    """Coerce a pandas cell to NUMERIC(18,4), or None if absent."""
    if value is None or pd.isna(value):
        return None
    return Decimal(f"{float(value):.4f}")


def _to_int(value: Any) -> int | None:
    if value is None or pd.isna(value):
        return None
    return int(value)


def _rows_from_frame(frame: pd.DataFrame, security_id: int) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    for index, row in frame.iterrows():
        bar_date = index.date() if hasattr(index, "date") else index
        close = _to_decimal(row.get("Close"))
        rows.append(
            {
                "security_id": security_id,
                "date": bar_date,
                "open": _to_decimal(row.get("Open")),
                "high": _to_decimal(row.get("High")),
                "low": _to_decimal(row.get("Low")),
                "close": close,
                # auto_adjust=True means yfinance already applied split and
                # dividend adjustments to OHLC, so Close *is* the adjusted
                # close. Both columns are written so downstream code can read
                # adj_close uniformly without knowing how the data was fetched.
                "adj_close": close,
                "volume": _to_int(row.get("Volume")),
            }
        )
    return rows


def _upsert_prices(db: Session, rows: list[dict[str, Any]]) -> None:
    """Insert-or-update on the (security_id, date) unique constraint."""
    for start in range(0, len(rows), UPSERT_CHUNK_SIZE):
        chunk = rows[start : start + UPSERT_CHUNK_SIZE]
        stmt = pg_insert(DailyPrice).values(chunk)
        stmt = stmt.on_conflict_do_update(
            constraint="uq_daily_prices_security_id_date",
            set_={
                "open": stmt.excluded.open,
                "high": stmt.excluded.high,
                "low": stmt.excluded.low,
                "close": stmt.excluded.close,
                "adj_close": stmt.excluded.adj_close,
                "volume": stmt.excluded.volume,
            },
        )
        db.execute(stmt)
    db.commit()


def fetch_and_store_prices(
    db: Session, ticker: str, years_back: int = 10
) -> IngestResult:
    """Pull daily OHLCV for one ticker and upsert it into ``daily_prices``.

    Accepts either ticker form (``RELIANCE`` or ``RELIANCE.NS``). A shorter
    history than ``years_back`` is stored as-is and is not an error -- several
    Nifty 50 members listed recently (see :data:`KNOWN_SHORT_HISTORY`).
    Download failures are returned on the result rather than raised, so a
    caller looping over the universe can continue.
    """
    symbol = to_nse_symbol(ticker)
    result = IngestResult(ticker=symbol)

    security = db.scalar(select(Security).where(Security.ticker == symbol))
    if security is None:
        result.error = "not present in securities (run seed_securities first)"
        logger.warning("[%s] %s", symbol, result.error)
        return result

    end = datetime.now().date() + timedelta(days=1)  # yfinance `end` is exclusive
    start = end - timedelta(days=round(years_back * 365.25))

    try:
        raw = _download_with_retry(to_yf_symbol(symbol), start, end)
    except PriceDownloadError as exc:
        result.error = str(exc)
        logger.warning("[%s] giving up and moving on: %s", symbol, exc)
        return result

    frame = _normalize_frame(raw, to_yf_symbol(symbol))
    rows = _rows_from_frame(frame, security.id)
    if not rows:
        logger.warning("[%s] no usable rows after normalisation", symbol)
        return result

    _upsert_prices(db, rows)

    result.rows_written = len(rows)
    result.first_date = min(r["date"] for r in rows)
    result.last_date = max(r["date"] for r in rows)

    expected_days = years_back * TRADING_DAYS_PER_YEAR
    if len(rows) < expected_days * 0.5:
        reason = KNOWN_SHORT_HISTORY.get(symbol)
        logger.info(
            "[%s] short history: %d rows from %s (%s)",
            symbol, len(rows), result.first_date,
            reason or "shorter than requested window; stored what was available",
        )

    return result


# ---------------------------------------------------------------------------
# Coverage reporting
# ---------------------------------------------------------------------------


def build_coverage_report(db: Session, years_back: int = 10) -> list[CoverageRow]:
    """Per-security first/last date, row count and coverage percentages."""
    stmt = (
        select(
            Security.ticker,
            func.min(DailyPrice.date),
            func.max(DailyPrice.date),
            func.count(DailyPrice.id),
        )
        .select_from(Security)
        .outerjoin(DailyPrice, DailyPrice.security_id == Security.id)
        .group_by(Security.id, Security.ticker)
        .order_by(Security.ticker)
    )

    report: list[CoverageRow] = []
    for ticker, first_date, last_date, row_count in db.execute(stmt):
        pct_span: float | None = None
        pct_requested: float | None = None

        if row_count and first_date and last_date:
            span_years = ((last_date - first_date).days + 1) / 365.25
            expected_span = max(span_years * TRADING_DAYS_PER_YEAR, 1.0)
            pct_span = round(row_count / expected_span * 100, 1)
            pct_requested = round(
                row_count / (years_back * TRADING_DAYS_PER_YEAR) * 100, 1
            )

        is_short = (
            pct_requested is not None and pct_requested < SHORT_HISTORY_THRESHOLD_PCT
        )
        report.append(
            CoverageRow(
                ticker=ticker,
                first_date=first_date,
                last_date=last_date,
                row_count=row_count,
                pct_of_span=pct_span,
                pct_of_requested=pct_requested,
                # Flagged only when the history is both short AND known to be
                # legitimately so, which keeps the marker meaningful.
                expected_short=is_short and ticker in KNOWN_SHORT_HISTORY,
                note=KNOWN_SHORT_HISTORY.get(ticker, ""),
            )
        )
    return report


def format_coverage_report(summary: IngestSummary) -> str:
    """Render the coverage report as a fixed-width table plus footnotes."""
    headers = [
        "ticker", "first_date", "last_date", "row_count",
        "pct_of_expected_days", f"pct_of_{summary.years_back}y", "",
    ]

    def cell(row: CoverageRow) -> list[str]:
        return [
            row.ticker,
            row.first_date.isoformat() if row.first_date else "-",
            row.last_date.isoformat() if row.last_date else "-",
            str(row.row_count),
            f"{row.pct_of_span:.1f}%" if row.pct_of_span is not None else "-",
            f"{row.pct_of_requested:.1f}%" if row.pct_of_requested is not None else "-",
            "* expected short history" if row.expected_short else "",
        ]

    body = [cell(r) for r in summary.coverage]
    widths = [
        max(len(headers[i]), *(len(r[i]) for r in body)) if body else len(headers[i])
        for i in range(len(headers))
    ]

    def line(values: list[str]) -> str:
        return "  ".join(v.ljust(widths[i]) for i, v in enumerate(values)).rstrip()

    out = [
        "",
        f"COVERAGE REPORT  ({len(summary.coverage)} securities, "
        f"{summary.years_back}y requested, {TRADING_DAYS_PER_YEAR} trading days/year)",
        "",
        line(headers),
        "  ".join("-" * w for w in widths).rstrip(),
    ]
    out.extend(line(r) for r in body)

    short = [r for r in summary.coverage if r.expected_short]
    if short:
        out += ["", "* Short history is expected for these -- not a fetch failure:"]
        out += [f"    {r.ticker:<12} {r.note}" for r in short]

    unexpected = [
        r for r in summary.coverage
        if r.ticker not in KNOWN_SHORT_HISTORY
        and r.pct_of_requested is not None
        and r.pct_of_requested < SHORT_HISTORY_THRESHOLD_PCT
    ]
    if unexpected:
        out += ["", "! Unexpectedly short history (worth investigating):"]
        out += [
            f"    {r.ticker:<12} {r.row_count} rows from {r.first_date}"
            for r in unexpected
        ]

    handled = [m for m in summary.large_moves if m.handled]
    if handled:
        out += [
            "",
            "Known large moves -- registered in price_anomalies and neutralised by",
            "get_daily_returns(). No action needed:",
        ]
        for move in handled:
            out += [
                f"    {move.ticker:<12} {move.date}  {move.pct_move * 100:+.1f}%"
                f"  [known, handled]",
                f"    {'':<12} {move.description}",
            ]

    reviewed = [
        m for m in summary.large_moves if not m.handled and m.reviewed_genuine
    ]
    if reviewed:
        out += [
            "",
            "Reviewed genuine market moves -- kept in the return series on purpose:",
        ]
        for move in reviewed:
            out += [
                f"    {move.ticker:<12} {move.date}  {move.pct_move * 100:+.1f}%"
                f"  [reviewed: genuine]",
                f"    {'':<12} {move.description}",
            ]

    unhandled = [
        m for m in summary.large_moves if not m.handled and not m.reviewed_genuine
    ]
    if unhandled:
        out += [
            "",
            f"! Unhandled large moves (>{LARGE_MOVE_THRESHOLD:.0%}) not in",
            "  price_anomalies. Each is either a genuine tail event -- keep it -- or a",
            "  corporate-action artefact that should be registered. Needs review:",
        ]
        out += [
            f"    {m.ticker:<12} {m.date}  {m.pct_move * 100:+.1f}%"
            for m in unhandled
        ]

    empty = [r for r in summary.coverage if r.row_count == 0]
    if empty:
        out += ["", "! No price rows at all:"]
        out += [f"    {r.ticker}" for r in empty]

    out += [
        "",
        f"Securities seeded : {summary.securities_seeded}",
        f"Anomalies seeded  : {summary.anomalies_seeded}",
        f"Rows written      : {summary.total_rows_written:,}",
        f"Tickers succeeded : {len(summary.results) - len(summary.failures)}"
        f"/{len(summary.results)}",
    ]
    if summary.failures:
        out += ["", "Failed tickers:"]
        out += [f"    {r.ticker:<12} {r.error}" for r in summary.failures]
    out.append("")
    return "\n".join(out)


# ---------------------------------------------------------------------------
# Orchestration
# ---------------------------------------------------------------------------


def ingest_all(
    db: Session,
    years_back: int = 10,
    fetch_prices: bool = True,
    only_tickers: Sequence[str] | None = None,
) -> IngestSummary:
    """Seed the universe, fetch every ticker's history, print coverage.

    One ticker failing does not stop the run -- the failure is recorded and
    reported at the end.

    With ``fetch_prices=False`` the network is skipped entirely: securities and
    anomalies are re-seeded and the report is rebuilt from what is already
    stored. Useful after adding an entry to the anomaly registry.

    ``only_tickers`` restricts the price fetch to a subset (seeding and the
    report still cover everything), for topping up one security without
    re-fetching the whole universe.
    """
    summary = IngestSummary(years_back=years_back)
    summary.securities_seeded = seed_securities(db) + seed_benchmark(db)

    securities = db.scalars(select(Security).order_by(Security.ticker)).all()

    if only_tickers:
        wanted = {to_nse_symbol(t) for t in only_tickers}
        unknown = wanted - {s.ticker for s in securities}
        if unknown:
            raise ValueError(
                f"Not in securities: {', '.join(sorted(unknown))}"
            )
        securities = [s for s in securities if s.ticker in wanted]

    total = len(securities)

    if not fetch_prices:
        logger.info("Skipping price fetch; re-seeding and rebuilding the report")
        summary.anomalies_seeded = seed_price_anomalies(db)
        summary.coverage = build_coverage_report(db, years_back)
        summary.large_moves = scan_for_large_moves(db)
        print(format_coverage_report(summary), flush=True)
        return summary

    logger.info("Ingesting %d years of history for %d securities", years_back, total)

    for position, security in enumerate(securities, start=1):
        prefix = f"[{position:>2}/{total}] {security.ticker:<12}"
        try:
            result = fetch_and_store_prices(db, security.ticker, years_back)
        except Exception as exc:  # noqa: BLE001 - never let one ticker end the run
            db.rollback()
            result = IngestResult(
                ticker=security.ticker, error=f"{type(exc).__name__}: {exc}"
            )
            logger.exception("[%s] unexpected error; continuing", security.ticker)

        summary.results.append(result)

        if result.ok and result.rows_written:
            print(
                f"{prefix} {result.rows_written:>5} rows  "
                f"{result.first_date} -> {result.last_date}",
                flush=True,
            )
        elif result.ok:
            print(f"{prefix}     0 rows  (nothing returned)", flush=True)
        else:
            print(f"{prefix} FAILED    {result.error}", flush=True)

    # Anomalies reference securities, so they are seeded after the universe
    # exists; the scan then runs over everything just ingested.
    summary.anomalies_seeded = seed_price_anomalies(db)
    summary.coverage = build_coverage_report(db, years_back)
    summary.large_moves = scan_for_large_moves(db)
    print(format_coverage_report(summary), flush=True)
    return summary
