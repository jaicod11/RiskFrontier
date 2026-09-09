"""Read-only endpoints for verifying what ingestion landed.

Temporary surface: enough to confirm the pipeline works, not the eventual
portfolio API. Response models live here rather than in a shared schemas
package for that reason.
"""

from __future__ import annotations

import datetime as dt

from fastapi import APIRouter, Depends, HTTPException, Query
from pydantic import BaseModel
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.core.db import get_db
from app.core.errors import ErrorResponse, InvalidParameterError
from app.models import DailyPrice, Security
from app.services.data_ingestion import to_nse_symbol

router = APIRouter(prefix="/api/securities", tags=["securities"])

#: Rows returned when the caller gives no date range.
DEFAULT_PRICE_LIMIT = 1000


class SecuritySummary(BaseModel):
    id: int
    ticker: str
    name: str
    exchange: str
    sector: str | None
    is_active: bool
    row_count: int
    first_date: dt.date | None
    last_date: dt.date | None


class PriceBar(BaseModel):
    date: dt.date
    open: float | None
    high: float | None
    low: float | None
    close: float | None
    adj_close: float | None
    volume: int | None


class PriceSeries(BaseModel):
    ticker: str
    name: str
    count: int
    first_date: dt.date | None
    last_date: dt.date | None
    capped: bool
    limit: int | None
    prices: list[PriceBar]


@router.get(
    "",
    response_model=list[SecuritySummary],
    summary="List all securities",
    description=(
        "Every ingested security with its price coverage: row count and first "
        "and last available date. One aggregate query, so adding tickers does "
        "not add round trips. Includes the benchmark index (`^NSEI`), "
        "distinguishable by `is_benchmark`."
    ),
    response_description="Securities ordered by ticker",
)
def list_securities(db: Session = Depends(get_db)) -> list[SecuritySummary]:
    """One aggregate query -- no per-security follow-up round trips."""
    stmt = (
        select(
            Security.id,
            Security.ticker,
            Security.name,
            Security.exchange,
            Security.sector,
            Security.is_active,
            func.count(DailyPrice.id).label("row_count"),
            func.min(DailyPrice.date).label("first_date"),
            func.max(DailyPrice.date).label("last_date"),
        )
        .select_from(Security)
        .outerjoin(DailyPrice, DailyPrice.security_id == Security.id)
        .group_by(
            Security.id,
            Security.ticker,
            Security.name,
            Security.exchange,
            Security.sector,
            Security.is_active,
        )
        .order_by(Security.ticker)
    )
    return [SecuritySummary(**row._mapping) for row in db.execute(stmt)]


@router.get(
    "/{ticker}/prices",
    response_model=PriceSeries,
    summary="Daily OHLCV for one security",
    description=(
        "Accepts either ticker form (`RELIANCE` or `RELIANCE.NS`). With no date "
        "range the most recent 1000 bars are returned, still in ascending date "
        "order. Prices are as ingested: the `price_anomalies` overlay is applied "
        "when deriving returns, not to these raw bars."
    ),
    responses={
        404: {"model": ErrorResponse, "description": "Ticker not in the database"},
        422: {"model": ErrorResponse, "description": "Invalid date range"},
    },
)
def get_prices(
    ticker: str,
    start: dt.date | None = Query(None, description="Inclusive start date (YYYY-MM-DD)"),
    end: dt.date | None = Query(None, description="Inclusive end date (YYYY-MM-DD)"),
    db: Session = Depends(get_db),
) -> PriceSeries:
    """Accepts either ticker form (``RELIANCE`` or ``RELIANCE.NS``).

    With no date range the most recent :data:`DEFAULT_PRICE_LIMIT` bars are
    returned, still in ascending date order.
    """
    symbol = to_nse_symbol(ticker)
    security = db.scalar(select(Security).where(Security.ticker == symbol))
    if security is None:
        # A path-addressed resource that does not exist is a 404. Unknown
        # tickers inside a POST body are 422 instead — the body is well-formed
        # but not satisfiable.
        raise HTTPException(status_code=404, detail=f"Unknown ticker: {ticker}")

    if start and end and start > end:
        raise InvalidParameterError(
            f"start ({start}) must not be after end ({end})",
            details={"start": str(start), "end": str(end)},
        )

    stmt = select(DailyPrice).where(DailyPrice.security_id == security.id)
    if start:
        stmt = stmt.where(DailyPrice.date >= start)
    if end:
        stmt = stmt.where(DailyPrice.date <= end)

    unbounded = start is None and end is None
    if unbounded:
        # Take the newest N, then flip back to ascending for the caller.
        stmt = stmt.order_by(DailyPrice.date.desc()).limit(DEFAULT_PRICE_LIMIT)
        rows = list(reversed(db.scalars(stmt).all()))
    else:
        rows = list(db.scalars(stmt.order_by(DailyPrice.date)).all())

    return PriceSeries(
        ticker=security.ticker,
        name=security.name,
        count=len(rows),
        first_date=rows[0].date if rows else None,
        last_date=rows[-1].date if rows else None,
        capped=unbounded and len(rows) == DEFAULT_PRICE_LIMIT,
        limit=DEFAULT_PRICE_LIMIT if unbounded else None,
        prices=[PriceBar.model_validate(r, from_attributes=True) for r in rows],
    )
