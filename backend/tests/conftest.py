"""Shared pytest fixtures."""

from __future__ import annotations

import datetime as dt
import logging
from contextlib import contextmanager

import pandas as pd
import pytest
from fastapi.testclient import TestClient
from sqlalchemy import delete
from sqlalchemy.orm import Session

from app.core.db import engine
from app.models import DailyPrice, PriceAnomaly, Security
from app.services.anomalies import seed_price_anomalies
from app.main import app


# The dev engine is built with echo=True, which buries assertion failures
# under one INFO line per statement.
logging.getLogger("sqlalchemy.engine").setLevel(logging.WARNING)


@pytest.fixture
def client() -> TestClient:
    return TestClient(app)


@contextmanager
def _rolled_back_session():
    """A session on a transaction that is always rolled back at teardown."""
    connection = engine.connect()
    transaction = connection.begin()
    session = Session(bind=connection, join_transaction_mode="create_savepoint")
    try:
        yield session
    finally:
        session.close()
        transaction.rollback()
        connection.close()


@pytest.fixture
def db_session():
    """A session over an EMPTY schema; all writes are rolled back.

    ``join_transaction_mode="create_savepoint"`` turns the application code's
    own ``commit()`` calls into savepoint releases, so committed work is visible
    within the test and vanishes afterwards.

    The tables are cleared first so assertions on absolute row counts hold
    whether or not the database has been ingested. Both deletes are inside the
    outer transaction, so real data comes back on rollback.
    """
    with _rolled_back_session() as session:
        session.execute(delete(PriceAnomaly))
        session.execute(delete(DailyPrice))
        session.execute(delete(Security))
        session.flush()
        yield session


@pytest.fixture
def ingested_db():
    """A session over the REAL ingested data; all writes are rolled back.

    For tests that need to assert against the actual market series (the TMPV
    and TRENT breaks, the confirmed genuine moves) rather than synthetic bars.

    The anomaly registry is seeded here so these tests are deterministic
    regardless of whether the live database has been seeded yet.
    """
    with _rolled_back_session() as session:
        seed_price_anomalies(session)
        yield session


@pytest.fixture(autouse=True)
def no_retry_sleep(monkeypatch):
    """Keep the exponential backoff from actually sleeping during tests."""
    monkeypatch.setattr("app.services.data_ingestion.time.sleep", lambda _s: None)


def make_ohlcv_frame(
    start: dt.date, days: int, close_start: float = 100.0
) -> pd.DataFrame:
    """A yfinance-shaped OHLCV frame: business-day DatetimeIndex, Title columns."""
    index = pd.bdate_range(start=start, periods=days, name="Date")
    closes = [close_start + i for i in range(days)]
    return pd.DataFrame(
        {
            "Open": [c - 1 for c in closes],
            "High": [c + 2 for c in closes],
            "Low": [c - 2 for c in closes],
            "Close": closes,
            "Volume": [1_000_000 + i for i in range(days)],
        },
        index=index,
    )
