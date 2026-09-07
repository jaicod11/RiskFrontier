"""Ingestion pipeline behaviour. yfinance is always mocked -- no network."""

from __future__ import annotations

import datetime as dt

import pandas as pd
import pytest
from sqlalchemy import func, select

from app.models import DailyPrice, Security
from app.services import data_ingestion
from app.services.anomalies import (
    KNOWN_PRICE_ANOMALIES,
    LargeMove,
    load_anomaly_index,
)
from app.services.data_ingestion import (
    PriceDownloadError,
    fetch_and_store_prices,
    ingest_all,
    load_seed,
    seed_securities,
    to_nse_symbol,
    to_yf_symbol,
)
from tests.conftest import make_ohlcv_frame


# --- ticker normalisation --------------------------------------------------


@pytest.mark.parametrize(
    ("raw", "nse", "yahoo"),
    [
        ("RELIANCE.NS", "RELIANCE", "RELIANCE.NS"),
        ("RELIANCE", "RELIANCE", "RELIANCE.NS"),
        ("M&M.NS", "M&M", "M&M.NS"),
        ("BAJAJ-AUTO.NS", "BAJAJ-AUTO", "BAJAJ-AUTO.NS"),
        ("  tcs.ns  ", "TCS", "TCS.NS"),
    ],
)
def test_ticker_forms_round_trip(raw, nse, yahoo):
    assert to_nse_symbol(raw) == nse
    assert to_yf_symbol(raw) == yahoo


def test_seed_file_is_complete_and_unique():
    entries = load_seed()
    assert len(entries) == 50
    assert len({e["yf_ticker"] for e in entries}) == 50
    assert all(e["yf_ticker"].endswith(".NS") for e in entries)


# --- seed_securities -------------------------------------------------------


def test_seed_securities_is_idempotent(db_session):
    expected = len(load_seed())

    first = seed_securities(db_session)
    second = seed_securities(db_session)

    assert first == second == expected

    total = db_session.scalar(select(func.count(Security.id)))
    assert total == expected

    distinct = db_session.scalar(select(func.count(func.distinct(Security.ticker))))
    assert distinct == expected


def test_seed_securities_sets_exchange_and_active_flag(db_session):
    seed_securities(db_session)
    reliance = db_session.scalar(select(Security).where(Security.ticker == "RELIANCE"))

    assert reliance is not None
    assert reliance.exchange == "NSE"
    assert reliance.is_active is True
    assert reliance.name == "Reliance Industries"
    # The ".NS" suffix belongs to Yahoo, not to the stored NSE symbol.
    assert not reliance.ticker.endswith(".NS")


def test_seed_securities_updates_changed_metadata_in_place(db_session):
    seed_securities(db_session)
    security = db_session.scalar(select(Security).where(Security.ticker == "INFY"))
    security.sector = "WRONG"
    db_session.commit()

    seed_securities(db_session)
    db_session.expire_all()

    refreshed = db_session.scalar(select(Security).where(Security.ticker == "INFY"))
    assert refreshed.sector == "Information Technology"
    assert db_session.scalar(select(func.count(Security.id))) == len(load_seed())


# --- fetch_and_store_prices ------------------------------------------------


def _price_rows(db, ticker: str) -> list[DailyPrice]:
    security = db.scalar(select(Security).where(Security.ticker == ticker))
    return list(
        db.scalars(
            select(DailyPrice)
            .where(DailyPrice.security_id == security.id)
            .order_by(DailyPrice.date)
        ).all()
    )


def test_fetch_and_store_prices_inserts_rows(db_session, monkeypatch):
    seed_securities(db_session)
    frame = make_ohlcv_frame(dt.date(2024, 1, 1), days=5)
    monkeypatch.setattr(data_ingestion.yf, "download", lambda *a, **k: frame)

    result = fetch_and_store_prices(db_session, "RELIANCE", years_back=10)

    assert result.ok
    assert result.rows_written == 5
    rows = _price_rows(db_session, "RELIANCE")
    assert len(rows) == 5
    assert rows[0].date == dt.date(2024, 1, 1)
    assert float(rows[0].close) == 100.0
    # auto_adjust=True -> Close is already the adjusted close.
    assert rows[0].adj_close == rows[0].close
    assert rows[0].volume == 1_000_000


def test_fetch_and_store_prices_upserts_without_duplicating(db_session, monkeypatch):
    seed_securities(db_session)

    first = make_ohlcv_frame(dt.date(2024, 1, 1), days=5)
    monkeypatch.setattr(data_ingestion.yf, "download", lambda *a, **k: first)
    fetch_and_store_prices(db_session, "RELIANCE", years_back=10)

    # Same dates, different closes, plus two new dates.
    revised = make_ohlcv_frame(dt.date(2024, 1, 1), days=7, close_start=500.0)
    monkeypatch.setattr(data_ingestion.yf, "download", lambda *a, **k: revised)
    result = fetch_and_store_prices(db_session, "RELIANCE", years_back=10)

    assert result.rows_written == 7
    rows = _price_rows(db_session, "RELIANCE")

    # 5 overlapping + 2 new = 7 rows, not 12: the unique constraint held.
    assert len(rows) == 7
    assert len({r.date for r in rows}) == 7
    # Overlapping rows were updated in place, not left stale.
    assert float(rows[0].close) == 500.0
    assert float(rows[0].adj_close) == 500.0


def test_fetch_accepts_yahoo_ticker_form(db_session, monkeypatch):
    seed_securities(db_session)
    frame = make_ohlcv_frame(dt.date(2024, 3, 1), days=3)
    monkeypatch.setattr(data_ingestion.yf, "download", lambda *a, **k: frame)

    result = fetch_and_store_prices(db_session, "RELIANCE.NS", years_back=1)

    assert result.ok
    assert result.ticker == "RELIANCE"
    assert len(_price_rows(db_session, "RELIANCE")) == 3


def test_fetch_handles_multiindex_columns(db_session, monkeypatch):
    """Recent yfinance returns (field, ticker) columns even for one symbol."""
    seed_securities(db_session)
    frame = make_ohlcv_frame(dt.date(2024, 1, 1), days=4)
    frame.columns = pd.MultiIndex.from_product([frame.columns, ["TCS.NS"]])
    monkeypatch.setattr(data_ingestion.yf, "download", lambda *a, **k: frame)

    result = fetch_and_store_prices(db_session, "TCS", years_back=10)

    assert result.rows_written == 4
    assert float(_price_rows(db_session, "TCS")[0].close) == 100.0


def test_short_history_is_stored_not_raised(db_session, monkeypatch):
    """TMPV listed Oct 2025 -- a few months of data is correct, not a failure."""
    seed_securities(db_session)
    frame = make_ohlcv_frame(dt.date(2025, 10, 13), days=40)
    monkeypatch.setattr(data_ingestion.yf, "download", lambda *a, **k: frame)

    result = fetch_and_store_prices(db_session, "TMPV", years_back=10)

    assert result.ok
    assert result.error is None
    assert result.rows_written == 40


def test_fetch_retries_then_succeeds(db_session, monkeypatch):
    seed_securities(db_session)
    frame = make_ohlcv_frame(dt.date(2024, 1, 1), days=3)
    calls = {"n": 0}

    def flaky(*args, **kwargs):
        calls["n"] += 1
        if calls["n"] < 3:
            raise ConnectionError("transient yfinance error")
        return frame

    monkeypatch.setattr(data_ingestion.yf, "download", flaky)
    result = fetch_and_store_prices(db_session, "INFY", years_back=10)

    assert calls["n"] == 3
    assert result.ok
    assert result.rows_written == 3


def test_fetch_gives_up_after_max_attempts(db_session, monkeypatch):
    seed_securities(db_session)
    calls = {"n": 0}

    def always_fails(*args, **kwargs):
        calls["n"] += 1
        raise ConnectionError("nope")

    monkeypatch.setattr(data_ingestion.yf, "download", always_fails)
    result = fetch_and_store_prices(db_session, "INFY", years_back=10)

    assert calls["n"] == data_ingestion.MAX_FETCH_ATTEMPTS == 3
    assert not result.ok
    assert "INFY.NS" in result.error
    assert _price_rows(db_session, "INFY") == []


def test_empty_frame_is_retried_then_reported(db_session, monkeypatch):
    """yfinance answers rate limits with an empty frame rather than an error."""
    seed_securities(db_session)
    calls = {"n": 0}

    def empty(*args, **kwargs):
        calls["n"] += 1
        return pd.DataFrame()

    monkeypatch.setattr(data_ingestion.yf, "download", empty)
    result = fetch_and_store_prices(db_session, "WIPRO", years_back=10)

    assert calls["n"] == 3
    assert not result.ok
    assert "no rows" in result.error


def test_fetch_unknown_ticker_reports_rather_than_raises(db_session, monkeypatch):
    seed_securities(db_session)
    monkeypatch.setattr(
        data_ingestion.yf, "download",
        lambda *a, **k: pytest.fail("should not reach yfinance"),
    )

    result = fetch_and_store_prices(db_session, "NOTREAL", years_back=10)

    assert not result.ok
    assert "seed_securities" in result.error


# --- ingest_all ------------------------------------------------------------


def test_ingest_all_continues_after_one_ticker_fails(db_session, monkeypatch):
    """A single bad ticker must not abort the other 49."""
    failing = "INFY.NS"
    frame = make_ohlcv_frame(dt.date(2024, 1, 1), days=6)

    def selective(symbol, *args, **kwargs):
        if symbol == failing:
            raise ConnectionError("simulated outage")
        return frame

    monkeypatch.setattr(data_ingestion.yf, "download", selective)

    summary = ingest_all(db_session, years_back=10)

    # The universe is the seeded constituents plus the benchmark index.
    total = len(load_seed()) + 1
    assert summary.securities_seeded == total
    assert len(summary.results) == total

    assert [r.ticker for r in summary.failures] == ["INFY"]
    assert len(summary.results) - len(summary.failures) == total - 1

    # Every other ticker still landed its rows.
    assert summary.total_rows_written == (total - 1) * 6
    assert _price_rows(db_session, "INFY") == []
    assert len(_price_rows(db_session, "RELIANCE")) == 6


def test_ingest_all_reports_coverage_for_every_security(db_session, monkeypatch):
    frame = make_ohlcv_frame(dt.date(2024, 1, 1), days=10)
    monkeypatch.setattr(data_ingestion.yf, "download", lambda *a, **k: frame)

    summary = ingest_all(db_session, years_back=10)
    coverage = {row.ticker: row for row in summary.coverage}

    assert len(coverage) == len(load_seed()) + 1  # constituents + benchmark
    assert data_ingestion.BENCHMARK_SECURITY["ticker"] in coverage
    reliance = coverage["RELIANCE"]
    assert reliance.row_count == 10
    assert reliance.first_date == dt.date(2024, 1, 1)
    assert reliance.pct_of_requested is not None

    # The known recent listings are flagged so short history reads as expected.
    assert coverage["TMPV"].expected_short is True
    assert "demerger" in coverage["TMPV"].note
    assert coverage["RELIANCE"].expected_short is False


def test_coverage_report_renders_table_and_footnotes(db_session, monkeypatch):
    frame = make_ohlcv_frame(dt.date(2024, 1, 1), days=10)
    monkeypatch.setattr(data_ingestion.yf, "download", lambda *a, **k: frame)

    summary = ingest_all(db_session, years_back=10)
    text = data_ingestion.format_coverage_report(summary)

    assert "COVERAGE REPORT" in text
    assert "pct_of_expected_days" in text
    assert "RELIANCE" in text
    assert "expected short history" in text
    assert "Rows written" in text


# --- registry integration --------------------------------------------------


def test_ingest_all_seeds_the_anomaly_registry(db_session, monkeypatch):
    frame = make_ohlcv_frame(dt.date(2024, 1, 1), days=10)
    monkeypatch.setattr(data_ingestion.yf, "download", lambda *a, **k: frame)

    summary = ingest_all(db_session, years_back=10)

    assert summary.anomalies_seeded == len(KNOWN_PRICE_ANOMALIES)
    registered = load_anomaly_index(db_session)
    assert ("TMPV", dt.date(2025, 10, 14)) in registered
    assert ("TRENT", dt.date(2026, 1, 1)) in registered


def test_coverage_report_separates_handled_from_unhandled(db_session, monkeypatch):
    """A registered move reads as handled; an unregistered one needs review."""
    frame = make_ohlcv_frame(dt.date(2024, 1, 1), days=10)
    monkeypatch.setattr(data_ingestion.yf, "download", lambda *a, **k: frame)
    summary = ingest_all(db_session, years_back=10)

    summary.large_moves = [
        LargeMove("TMPV", dt.date(2025, 10, 14), -0.402, True, "demerger"),
        LargeMove("MYSTERY", dt.date(2026, 5, 4), -0.31, False),
    ]
    text = data_ingestion.format_coverage_report(summary)

    assert "known, handled" in text
    assert "Unhandled large moves" in text
    assert "MYSTERY" in text
    assert "Anomalies seeded" in text
