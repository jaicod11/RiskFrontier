"""Read endpoints used to verify ingestion.

These run against whatever is currently in the database, so they assert on
shape and invariants rather than on specific ingested values.
"""

from __future__ import annotations

import datetime as dt


def test_list_securities_returns_coverage_shape(client):
    response = client.get("/api/securities")
    assert response.status_code == 200

    payload = response.json()
    assert isinstance(payload, list)

    if not payload:  # database not yet ingested
        return

    first = payload[0]
    assert set(first) == {
        "id", "ticker", "name", "exchange", "sector",
        "is_active", "row_count", "first_date", "last_date",
    }
    assert all(s["exchange"] == "NSE" for s in payload)
    assert all(not s["ticker"].endswith(".NS") for s in payload)
    # Sorted by ticker, and each ticker appears once.
    tickers = [s["ticker"] for s in payload]
    assert tickers == sorted(tickers)
    assert len(tickers) == len(set(tickers))


def test_prices_unknown_ticker_returns_404(client):
    response = client.get("/api/securities/NOTATICKER/prices")
    assert response.status_code == 404
    payload = response.json()
    assert payload["error_code"] == "NOT_FOUND"
    assert "Unknown ticker" in payload["message"]


def test_prices_rejects_inverted_range(client):
    response = client.get(
        "/api/securities/RELIANCE/prices",
        params={"start": "2024-06-01", "end": "2024-01-01"},
    )
    assert response.status_code == 422
    assert response.json()["error_code"] == "INVALID_PARAMETER"


def test_prices_are_capped_and_ascending_without_a_range(client):
    listing = client.get("/api/securities").json()
    if not listing:
        return
    populated = [s for s in listing if s["row_count"] > 0]
    if not populated:
        return

    ticker = populated[0]["ticker"]
    payload = client.get(f"/api/securities/{ticker}/prices").json()

    assert payload["ticker"] == ticker
    assert payload["count"] <= 1000
    assert payload["limit"] == 1000

    dates = [p["date"] for p in payload["prices"]]
    assert dates == sorted(dates)
    assert len(dates) == len(set(dates))
    assert payload["count"] == len(dates)


def test_prices_respect_an_explicit_range(client):
    listing = client.get("/api/securities").json()
    populated = [s for s in listing if s["row_count"] > 0] if listing else []
    if not populated:
        return

    security = populated[0]
    start = dt.date.fromisoformat(security["first_date"])
    end = min(start + dt.timedelta(days=30), dt.date.fromisoformat(security["last_date"]))

    payload = client.get(
        f"/api/securities/{security['ticker']}/prices",
        params={"start": start.isoformat(), "end": end.isoformat()},
    ).json()

    assert payload["limit"] is None
    assert payload["capped"] is False
    for bar in payload["prices"]:
        assert start <= dt.date.fromisoformat(bar["date"]) <= end


def test_prices_accept_the_yahoo_ticker_form(client):
    listing = client.get("/api/securities").json()
    if not listing:
        return
    ticker = listing[0]["ticker"]

    bare = client.get(f"/api/securities/{ticker}/prices")
    yahoo = client.get(f"/api/securities/{ticker}.NS/prices")

    assert bare.status_code == yahoo.status_code == 200
    assert bare.json()["ticker"] == yahoo.json()["ticker"] == ticker
