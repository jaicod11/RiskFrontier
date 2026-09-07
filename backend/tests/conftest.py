"""Shared pytest fixtures."""

from __future__ import annotations

import datetime as dt

import pandas as pd
import pytest
from fastapi.testclient import TestClient
from sqlalchemy import delete
from sqlalchemy.orm import Session

from app.core.db import engine
from app.models import DailyPrice, Security
from app.main import app


@pytest.fixture
def client() -> TestClient:
    return TestClient(app)


@pytest.fixture
def db_session():
    """A session whose writes are always rolled back.

    The session runs inside an outer transaction on a single connection;
    ``join_transaction_mode="create_savepoint"`` turns the ingestion code's own
    ``commit()`` calls into savepoint releases, so committed work is visible
    within the test and vanishes afterwards. Tests therefore run against the
    real Postgres schema without polluting ingested data.
    """
    connection = engine.connect()
    transaction = connection.begin()
    session = Session(bind=connection, join_transaction_mode="create_savepoint")

    # Start from an empty schema so assertions on absolute row counts hold
    # whether or not the database has been ingested. Both deletes are inside
    # the outer transaction, so real data comes back on rollback.
    session.execute(delete(DailyPrice))
    session.execute(delete(Security))
    session.flush()

    try:
        yield session
    finally:
        session.close()
        transaction.rollback()
        connection.close()


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
