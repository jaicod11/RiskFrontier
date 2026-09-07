"""End-of-day OHLCV bar for a single security."""

from __future__ import annotations

import datetime as dt
from decimal import Decimal
from typing import TYPE_CHECKING

from sqlalchemy import (
    BigInteger,
    Date,
    ForeignKey,
    Integer,
    Numeric,
    UniqueConstraint,
)
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.models.base import Base

if TYPE_CHECKING:
    from app.models.security import Security

# Prices are stored as NUMERIC rather than float so that ingest/round-trip is
# exact; conversion to float happens once, at the point of analysis.
_PRICE = Numeric(18, 4)


class DailyPrice(Base):
    __tablename__ = "daily_prices"
    __table_args__ = (
        UniqueConstraint("security_id", "date", name="uq_daily_prices_security_id_date"),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    security_id: Mapped[int] = mapped_column(
        ForeignKey("securities.id", ondelete="CASCADE"), nullable=False, index=True
    )
    date: Mapped[dt.date] = mapped_column(Date, nullable=False, index=True)

    open: Mapped[Decimal | None] = mapped_column(_PRICE, nullable=True)
    high: Mapped[Decimal | None] = mapped_column(_PRICE, nullable=True)
    low: Mapped[Decimal | None] = mapped_column(_PRICE, nullable=True)
    close: Mapped[Decimal | None] = mapped_column(_PRICE, nullable=True)
    adj_close: Mapped[Decimal | None] = mapped_column(_PRICE, nullable=True)
    volume: Mapped[int | None] = mapped_column(BigInteger, nullable=True)

    security: Mapped["Security"] = relationship(back_populates="daily_prices")

    def __repr__(self) -> str:
        return f"<DailyPrice security_id={self.security_id} date={self.date} close={self.close}>"
