"""Documented corrections that overlay -- never rewrite -- the raw price data.

A row here says: "the raw price change on this date is not a real gain or loss
on the position." The originating ``daily_prices`` bar is left exactly as
fetched, so the source stays inspectable and every correction stays auditable.
Consumers apply the overlay at return-calculation time; see
``app.services.returns.get_daily_returns``.
"""

from __future__ import annotations

import datetime as dt
import enum
from typing import TYPE_CHECKING

from sqlalchemy import Date, Enum as SAEnum, ForeignKey, Integer, Text, UniqueConstraint
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.models.base import Base

if TYPE_CHECKING:
    from app.models.security import Security


class AnomalyAdjustment(str, enum.Enum):
    """How a consumer must treat the affected date.

    ``EXCLUDE_RETURN``
        Treat the return *on this date* as zero / unavailable. The raw price
        change reflects a corporate action (demerger, unadjusted split), not a
        move in the value of a held position.
    """

    EXCLUDE_RETURN = "exclude_return"


class PriceAnomaly(Base):
    __tablename__ = "price_anomalies"
    __table_args__ = (
        UniqueConstraint(
            "security_id", "date", name="uq_price_anomalies_security_id_date"
        ),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    security_id: Mapped[int] = mapped_column(
        ForeignKey("securities.id", ondelete="CASCADE"), nullable=False, index=True
    )
    date: Mapped[dt.date] = mapped_column(Date, nullable=False, index=True)
    description: Mapped[str] = mapped_column(Text, nullable=False)
    adjustment: Mapped[AnomalyAdjustment] = mapped_column(
        SAEnum(
            AnomalyAdjustment,
            name="anomaly_adjustment",
            # Store the lowercase values, not the Python member names.
            values_callable=lambda enum_cls: [m.value for m in enum_cls],
        ),
        nullable=False,
    )

    security: Mapped["Security"] = relationship(back_populates="anomalies")

    def __repr__(self) -> str:
        return (
            f"<PriceAnomaly security_id={self.security_id} date={self.date} "
            f"adjustment={self.adjustment.value}>"
        )
