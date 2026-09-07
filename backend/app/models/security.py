"""A tradable instrument (an NSE-listed equity, to start with)."""

from __future__ import annotations

from typing import TYPE_CHECKING

from sqlalchemy import Boolean, Integer, String, text
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.models.base import Base

if TYPE_CHECKING:
    from app.models.daily_price import DailyPrice
    from app.models.price_anomaly import PriceAnomaly


class Security(Base):
    __tablename__ = "securities"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    ticker: Mapped[str] = mapped_column(String(32), nullable=False, unique=True, index=True)
    name: Mapped[str] = mapped_column(String(255), nullable=False)
    exchange: Mapped[str] = mapped_column(
        String(16), nullable=False, server_default=text("'NSE'")
    )
    sector: Mapped[str | None] = mapped_column(String(128), nullable=True)
    is_active: Mapped[bool] = mapped_column(
        Boolean, nullable=False, server_default=text("true")
    )
    #: True for index series (e.g. ^NSEI) held for benchmarking rather than as
    #: investable constituents. Kept in the same table so benchmarks flow
    #: through the same ingestion, anomaly and returns pipeline as any stock.
    is_benchmark: Mapped[bool] = mapped_column(
        Boolean, nullable=False, server_default=text("false")
    )

    daily_prices: Mapped[list["DailyPrice"]] = relationship(
        back_populates="security",
        cascade="all, delete-orphan",
        passive_deletes=True,
    )
    anomalies: Mapped[list["PriceAnomaly"]] = relationship(
        back_populates="security",
        cascade="all, delete-orphan",
        passive_deletes=True,
    )

    def __repr__(self) -> str:
        return f"<Security id={self.id} ticker={self.ticker!r} exchange={self.exchange!r}>"
