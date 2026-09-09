"""Shapes shared by every analytical response.

One `DataWindow` type across all endpoints so a frontend parses the provenance
of a number the same way no matter which endpoint produced it.
"""

from __future__ import annotations

import datetime as dt

from pydantic import BaseModel, Field


class DataWindow(BaseModel):
    """The data that actually backed a result.

    ``start_date``, ``end_date`` and ``trading_days`` are always present. The
    remaining fields describe *lookback shrinking*, which applies to the
    estimation endpoints (VaR, optimiser) and is null for the backtester —
    that one refuses to shrink and fails instead, so there is nothing to report.
    """

    start_date: dt.date = Field(description="First trading day actually used")
    end_date: dt.date = Field(description="Last trading day actually used")
    trading_days: int = Field(description="Number of trading days in the window")

    requested_lookback_days: int | None = Field(
        default=None, description="Window length asked for, where applicable"
    )
    shrunk: bool = Field(
        default=False,
        description="True when the window is shorter than requested because a "
                    "ticker's history did not reach back far enough",
    )
    constrained_by: str | None = Field(
        default=None, description="Ticker whose history bound the window"
    )
    constraint_reason: str | None = Field(
        default=None, description="Prose explanation of the constraint"
    )
    first_available_date_by_ticker: dict[str, dt.date] = Field(
        default_factory=dict,
        description="True start of each ticker's price history",
    )


#: Every analytical response carries this. Plain strings, one shape everywhere.
LimitationsField = Field(
    default_factory=list,
    description="Caveats that must be displayed alongside these numbers. "
                "Returned by the backend so they cannot be dropped by a client.",
)
