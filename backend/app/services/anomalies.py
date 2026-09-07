"""The price-anomaly registry: what it holds, how it is seeded, how it is found.

The registry is an *overlay*. Raw ``daily_prices`` bars are never modified --
they stay exactly as fetched so the source remains inspectable and every
correction remains auditable. Consumers apply the overlay when deriving
returns; see :func:`app.services.returns.get_daily_returns`.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass
from datetime import date

from sqlalchemy import func, select
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.orm import Session

from app.models import AnomalyAdjustment, DailyPrice, PriceAnomaly, Security

logger = logging.getLogger(__name__)

#: A daily move larger than this is worth a human look. Chosen because the
#: Nifty 50's genuine extremes (the 2020-03-23 COVID low, the Adani/Hindenburg
#: selloff) sit just under 30%, so the threshold catches real tail events as
#: well as corporate-action artefacts -- the scan classifies, it does not judge.
LARGE_MOVE_THRESHOLD = 0.25

#: Confirmed anomalies, seeded into ``price_anomalies``. Each was verified
#: against the ingested series and against Yahoo's own splits feed, which
#: records neither event -- which is exactly why ``auto_adjust`` cannot fix them.
KNOWN_PRICE_ANOMALIES: list[dict] = [
    {
        "ticker": "TMPV",
        "date": date(2025, 10, 14),
        "description": (
            "Tata Motors demerger — OHLC reflects entity split, "
            "not a real price move"
        ),
        "adjustment": AnomalyAdjustment.EXCLUDE_RETURN,
    },
    {
        "ticker": "TRENT",
        "date": date(2026, 1, 1),
        "description": (
            "Unadjusted corporate action (~3:2 ratio reset, below-average "
            "volume) — not a real price move"
        ),
        "adjustment": AnomalyAdjustment.EXCLUDE_RETURN,
    },
]


#: Large moves reviewed and confirmed as GENUINE market events. They stay in the
#: return series untouched -- this list only stops the scan re-flagging them for
#: review every run. Volume is measured against the trailing 30-day average;
#: "breadth" is how many of the 50 constituents moved with it that day.
#:
#: Note that volume alone does not separate an artefact from an event: TMPV's
#: demerger printed 3.78x volume. What identifies a genuine move is
#: corroboration -- the market or the sector moving with it.
REVIEWED_GENUINE_MOVES: dict[tuple[str, date], str] = {
    ("ADANIENT", date(2023, 2, 1)): (
        "Hindenburg report: 4.0x volume, and the selloff spans the group and its "
        "lenders (ADANIPORTS -19.2%, HDFCLIFE -10.9%, SBILIFE -9.0%)"
    ),
    ("ADANIENT", date(2023, 2, 2)): (
        "Hindenburg report, day two: 9.3x volume, group contagion continues"
    ),
    ("ADANIENT", date(2019, 5, 20)): (
        "Market-wide rally: 9.3x volume, index avg +3.8% with 13 of 47 up >5%"
    ),
    ("AXISBANK", date(2020, 3, 23)): (
        "COVID crash: 2.8x volume on the day the index avg fell 13.0% and 42 of "
        "47 constituents dropped more than 5%"
    ),
    ("BAJAJFINSV", date(2020, 3, 23)): (
        "COVID crash: 2.0x volume, same market-wide selloff"
    ),
    ("SBIN", date(2017, 10, 25)): (
        "PSU bank recapitalisation: 20.6x volume, with public-sector lenders up "
        "(ICICIBANK +14.7%) and private lenders down (KOTAKBANK -5.2%, "
        "BAJFINANCE -5.5%) -- a sector rotation, not a single-name repricing"
    ),
}


@dataclass(frozen=True)
class LargeMove:
    """One daily move above the scan threshold, classified against the registry."""

    ticker: str
    date: date
    pct_move: float
    handled: bool
    description: str = ""
    #: Reviewed and confirmed a genuine market event -- deliberately NOT
    #: excluded from returns, and no longer flagged for review.
    reviewed_genuine: bool = False


def seed_price_anomalies(db: Session) -> int:
    """Upsert :data:`KNOWN_PRICE_ANOMALIES` into ``price_anomalies``.

    Idempotent, matched on ``(security_id, date)``. Entries whose ticker is not
    in ``securities`` are skipped with a warning rather than failing the run.
    """
    ticker_to_id = {
        ticker: security_id
        for ticker, security_id in db.execute(select(Security.ticker, Security.id))
    }

    rows = []
    for entry in KNOWN_PRICE_ANOMALIES:
        security_id = ticker_to_id.get(entry["ticker"])
        if security_id is None:
            logger.warning(
                "Skipping anomaly for unknown ticker %s (seed securities first)",
                entry["ticker"],
            )
            continue
        rows.append(
            {
                "security_id": security_id,
                "date": entry["date"],
                "description": entry["description"],
                "adjustment": entry["adjustment"],
            }
        )

    if not rows:
        return 0

    stmt = pg_insert(PriceAnomaly).values(rows)
    stmt = stmt.on_conflict_do_update(
        constraint="uq_price_anomalies_security_id_date",
        set_={
            "description": stmt.excluded.description,
            "adjustment": stmt.excluded.adjustment,
        },
    )
    db.execute(stmt)
    db.commit()

    logger.info("Seeded %d price anomalies", len(rows))
    return len(rows)


def load_anomaly_index(db: Session) -> dict[tuple[str, date], PriceAnomaly]:
    """Every registered anomaly, keyed by ``(ticker, date)``."""
    stmt = select(Security.ticker, PriceAnomaly).join(
        Security, Security.id == PriceAnomaly.security_id
    )
    return {(ticker, anomaly.date): anomaly for ticker, anomaly in db.execute(stmt)}


def get_excluded_dates(db: Session, security_id: int) -> dict[date, PriceAnomaly]:
    """Dates for one security whose return must be excluded, keyed by date."""
    stmt = select(PriceAnomaly).where(
        PriceAnomaly.security_id == security_id,
        PriceAnomaly.adjustment == AnomalyAdjustment.EXCLUDE_RETURN,
    )
    return {anomaly.date: anomaly for anomaly in db.scalars(stmt)}


def scan_for_large_moves(
    db: Session, threshold: float = LARGE_MOVE_THRESHOLD
) -> list[LargeMove]:
    """Find every daily move above ``threshold``, flagged handled or not.

    A hit is ``handled`` when the registry already documents it. Everything
    else is genuinely new and needs a human decision: a real tail event to keep,
    or an artefact to register.
    """
    previous_close = func.lag(DailyPrice.adj_close).over(
        partition_by=DailyPrice.security_id, order_by=DailyPrice.date
    )
    moves = (
        select(
            Security.ticker.label("ticker"),
            DailyPrice.date.label("date"),
            (DailyPrice.adj_close / func.nullif(previous_close, 0) - 1).label("ret"),
        )
        .join(Security, Security.id == DailyPrice.security_id)
        .where(DailyPrice.adj_close.is_not(None))
        .subquery()
    )
    stmt = (
        select(moves.c.ticker, moves.c.date, moves.c.ret)
        .where(func.abs(moves.c.ret) > threshold)
        .order_by(func.abs(moves.c.ret).desc())
    )

    registry = load_anomaly_index(db)
    hits: list[LargeMove] = []
    for ticker, move_date, ret in db.execute(stmt):
        anomaly = registry.get((ticker, move_date))
        verdict = REVIEWED_GENUINE_MOVES.get((ticker, move_date))
        hits.append(
            LargeMove(
                ticker=ticker,
                date=move_date,
                pct_move=float(ret),
                handled=anomaly is not None,
                description=anomaly.description if anomaly else (verdict or ""),
                reviewed_genuine=verdict is not None,
            )
        )
    return hits
