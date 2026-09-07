"""The shared cleaning layer: anomalies neutralised, real moves preserved.

These run against the ingested database inside a rolled-back transaction, so
they assert on the actual TMPV/TRENT series rather than on synthetic data.
"""

from __future__ import annotations

import datetime as dt

import pandas as pd
import pytest
from sqlalchemy import func, select

from app.models import AnomalyAdjustment, DailyPrice, PriceAnomaly, Security
from app.services.anomalies import scan_for_large_moves, seed_price_anomalies
from app.services.returns import UnknownTickerError, get_daily_returns

# The two artefacts, and the raw moves they must suppress.
ANOMALIES = [
    ("TMPV", dt.date(2025, 10, 14), -0.402),
    ("TRENT", dt.date(2026, 1, 1), -0.330),
]

# Confirmed genuine market events. These must survive untouched.
GENUINE_MOVES = [
    ("ADANIENT", dt.date(2023, 2, 1), -0.282),   # Hindenburg report
    ("ADANIENT", dt.date(2023, 2, 2), -0.267),   # Hindenburg, day two
    ("ADANIENT", dt.date(2019, 5, 20), 0.274),   # election result rally
    ("AXISBANK", dt.date(2020, 3, 23), -0.279),  # COVID low
    ("BAJAJFINSV", dt.date(2020, 3, 23), -0.259),  # COVID low
    ("SBIN", dt.date(2017, 10, 25), 0.277),      # bank recapitalisation
]


def _has_data(db, ticker: str) -> bool:
    security = db.scalar(select(Security).where(Security.ticker == ticker))
    if security is None:
        return False
    count = db.scalar(
        select(func.count(DailyPrice.id)).where(DailyPrice.security_id == security.id)
    )
    return bool(count)


# --- anomalies are neutralised --------------------------------------------


@pytest.mark.parametrize(("ticker", "anomaly_date", "raw_move"), ANOMALIES)
def test_registered_anomaly_returns_zero(ingested_db, ticker, anomaly_date, raw_move):
    if not _has_data(ingested_db, ticker):
        pytest.skip(f"{ticker} not ingested")

    series = get_daily_returns(
        ingested_db,
        ticker,
        anomaly_date - dt.timedelta(days=10),
        anomaly_date + dt.timedelta(days=10),
    )
    stamp = pd.Timestamp(anomaly_date)

    assert stamp in series.index, f"{anomaly_date} missing from the return series"
    assert series.loc[stamp] == 0.0

    # And confirm we actually suppressed something large, not a no-op.
    security = ingested_db.scalar(select(Security).where(Security.ticker == ticker))
    prices = ingested_db.execute(
        select(DailyPrice.date, DailyPrice.adj_close)
        .where(
            DailyPrice.security_id == security.id,
            DailyPrice.date <= anomaly_date,
        )
        .order_by(DailyPrice.date.desc())
        .limit(2)
    ).all()
    raw = float(prices[0][1]) / float(prices[1][1]) - 1
    assert raw == pytest.approx(raw_move, abs=0.01)


def test_anomaly_is_zero_not_dropped(ingested_db):
    """Zero keeps the index dense so series stay aligned across securities."""
    if not _has_data(ingested_db, "TMPV"):
        pytest.skip("TMPV not ingested")

    start, end = dt.date(2025, 10, 6), dt.date(2025, 10, 20)
    series = get_daily_returns(ingested_db, "TMPV", start, end)
    trading_days = ingested_db.scalar(
        select(func.count(DailyPrice.id))
        .join(Security, Security.id == DailyPrice.security_id)
        .where(
            Security.ticker == "TMPV",
            DailyPrice.date >= start,
            DailyPrice.date <= end,
        )
    )
    # N bars -> N-1 returns, none dropped.
    assert len(series) == trading_days - 1
    assert not series.isna().any()


def test_anomaly_does_not_leak_into_neighbouring_days(ingested_db):
    """Only the anomaly date is touched; the days around it are untouched."""
    if not _has_data(ingested_db, "TMPV"):
        pytest.skip("TMPV not ingested")

    series = get_daily_returns(
        ingested_db, "TMPV", dt.date(2025, 10, 1), dt.date(2025, 10, 20)
    )
    zeros = series[series == 0.0]
    assert list(zeros.index) == [pd.Timestamp(2025, 10, 14)]


# --- genuine moves are preserved ------------------------------------------


@pytest.mark.parametrize(("ticker", "move_date", "expected"), GENUINE_MOVES)
def test_genuine_large_moves_are_not_excluded(ingested_db, ticker, move_date, expected):
    if not _has_data(ingested_db, ticker):
        pytest.skip(f"{ticker} not ingested")

    series = get_daily_returns(
        ingested_db,
        ticker,
        move_date - dt.timedelta(days=10),
        move_date + dt.timedelta(days=10),
    )
    stamp = pd.Timestamp(move_date)

    assert stamp in series.index
    assert series.loc[stamp] != 0.0
    assert float(series.loc[stamp]) == pytest.approx(expected, abs=0.01)


def test_ordinary_returns_match_a_hand_computation(ingested_db):
    if not _has_data(ingested_db, "RELIANCE"):
        pytest.skip("RELIANCE not ingested")

    start, end = dt.date(2024, 1, 1), dt.date(2024, 1, 31)
    series = get_daily_returns(ingested_db, "RELIANCE", start, end)

    security = ingested_db.scalar(select(Security).where(Security.ticker == "RELIANCE"))
    rows = ingested_db.execute(
        select(DailyPrice.date, DailyPrice.adj_close)
        .where(
            DailyPrice.security_id == security.id,
            DailyPrice.date >= start,
            DailyPrice.date <= end,
        )
        .order_by(DailyPrice.date)
    ).all()

    expected = pd.Series(
        [float(c) for _, c in rows],
        index=pd.DatetimeIndex([d for d, _ in rows]),
    ).pct_change().dropna()

    pd.testing.assert_series_equal(
        series, expected, check_names=False, check_freq=False
    )


# --- the raw data is never touched ----------------------------------------


def test_daily_prices_are_not_modified_by_return_cleaning(ingested_db):
    """The overlay must leave the source rows byte-identical."""
    if not _has_data(ingested_db, "TMPV"):
        pytest.skip("TMPV not ingested")

    security = ingested_db.scalar(select(Security).where(Security.ticker == "TMPV"))
    snapshot_stmt = (
        select(
            DailyPrice.date, DailyPrice.open, DailyPrice.high,
            DailyPrice.low, DailyPrice.close, DailyPrice.adj_close, DailyPrice.volume,
        )
        .where(
            DailyPrice.security_id == security.id,
            DailyPrice.date.between(dt.date(2025, 10, 1), dt.date(2025, 10, 20)),
        )
        .order_by(DailyPrice.date)
    )
    before = ingested_db.execute(snapshot_stmt).all()
    total_before = ingested_db.scalar(select(func.count(DailyPrice.id)))

    get_daily_returns(ingested_db, "TMPV", dt.date(2025, 10, 1), dt.date(2025, 10, 20))
    ingested_db.expire_all()

    assert ingested_db.execute(snapshot_stmt).all() == before
    assert ingested_db.scalar(select(func.count(DailyPrice.id))) == total_before

    # The -40.2% break is still there in the raw data, exactly as fetched.
    closes = {d: float(c) for d, _, _, _, _, c, _ in before}
    raw = closes[dt.date(2025, 10, 14)] / closes[dt.date(2025, 10, 13)] - 1
    assert raw == pytest.approx(-0.402, abs=0.01)


# --- contract --------------------------------------------------------------


def test_unknown_ticker_raises(ingested_db):
    with pytest.raises(UnknownTickerError):
        get_daily_returns(ingested_db, "NOTATICKER", dt.date(2024, 1, 1), dt.date(2024, 2, 1))


def test_accepts_either_ticker_form(ingested_db):
    if not _has_data(ingested_db, "RELIANCE"):
        pytest.skip("RELIANCE not ingested")

    args = (dt.date(2024, 1, 1), dt.date(2024, 3, 1))
    bare = get_daily_returns(ingested_db, "RELIANCE", *args)
    yahoo = get_daily_returns(ingested_db, "RELIANCE.NS", *args)

    pd.testing.assert_series_equal(bare, yahoo)
    assert bare.name == "RELIANCE"


def test_series_shape_and_ordering(ingested_db):
    if not _has_data(ingested_db, "INFY"):
        pytest.skip("INFY not ingested")

    series = get_daily_returns(ingested_db, "INFY", dt.date(2024, 1, 1), dt.date(2024, 6, 30))

    assert isinstance(series, pd.Series)
    assert series.dtype == "float64"
    assert isinstance(series.index, pd.DatetimeIndex)
    assert series.index.name == "date"
    assert series.index.is_monotonic_increasing
    assert series.index.is_unique


def test_single_bar_range_yields_empty_series(ingested_db):
    if not _has_data(ingested_db, "RELIANCE"):
        pytest.skip("RELIANCE not ingested")

    day = dt.date(2024, 1, 2)
    series = get_daily_returns(ingested_db, "RELIANCE", day, day)

    assert series.empty
    assert series.name == "RELIANCE"


# --- scan cross-check ------------------------------------------------------


def test_scan_marks_registered_moves_handled(ingested_db):
    if not _has_data(ingested_db, "TMPV"):
        pytest.skip("data not ingested")

    hits = scan_for_large_moves(ingested_db)
    by_key = {(h.ticker, h.date): h for h in hits}

    for ticker, anomaly_date, _ in ANOMALIES:
        hit = by_key.get((ticker, anomaly_date))
        assert hit is not None, f"scan missed {ticker} {anomaly_date}"
        assert hit.handled is True
        assert hit.description

    for ticker, move_date, _ in GENUINE_MOVES:
        hit = by_key.get((ticker, move_date))
        if hit is not None:
            assert hit.handled is False


def test_seeding_anomalies_is_idempotent(ingested_db):
    first = seed_price_anomalies(ingested_db)
    second = seed_price_anomalies(ingested_db)

    assert first == second
    total = ingested_db.scalar(select(func.count(PriceAnomaly.id)))
    assert total == first

    stored = ingested_db.scalars(select(PriceAnomaly)).all()
    assert all(a.adjustment is AnomalyAdjustment.EXCLUDE_RETURN for a in stored)
