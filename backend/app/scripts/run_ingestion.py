"""Populate securities + daily_prices from the Nifty 50 seed and yfinance.

    docker compose exec backend python -m app.scripts.run_ingestion
    docker compose exec backend python -m app.scripts.run_ingestion --years 5
    docker compose exec backend python -m app.scripts.run_ingestion --skip-prices
    docker compose exec backend python -m app.scripts.run_ingestion --tickers '^NSEI'
"""

from __future__ import annotations

import argparse
import logging
import sys

from app.core.db import SessionLocal
from app.services.data_ingestion import ingest_all


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        prog="run_ingestion",
        description="Seed the Nifty 50 universe and ingest daily OHLCV history.",
    )
    parser.add_argument(
        "--years",
        type=int,
        default=10,
        metavar="N",
        help="years of history to request per ticker (default: 10)",
    )
    parser.add_argument(
        "--skip-prices",
        action="store_true",
        help="re-seed securities and anomalies and rebuild the report, "
             "without fetching any prices",
    )
    parser.add_argument(
        "--tickers",
        nargs="+",
        metavar="TICKER",
        help="only fetch prices for these tickers (seeding still covers all)",
    )
    parser.add_argument(
        "--log-level",
        default="INFO",
        choices=["DEBUG", "INFO", "WARNING", "ERROR"],
        help="logging verbosity (default: INFO)",
    )
    return parser.parse_args(argv)


def main(argv: list[str] | None = None) -> int:
    args = parse_args(argv)

    if args.years < 1:
        print("--years must be at least 1", file=sys.stderr)
        return 2

    logging.basicConfig(
        level=getattr(logging, args.log_level),
        format="%(levelname)-7s %(name)s: %(message)s",
        stream=sys.stderr,
    )
    # yfinance chatters on every download; we do our own reporting.
    logging.getLogger("yfinance").setLevel(logging.ERROR)
    # The dev engine is created with echo=True, which would bury the coverage
    # report under one INFO line per statement. Quieten it unless --log-level
    # DEBUG was asked for explicitly.
    if args.log_level != "DEBUG":
        logging.getLogger("sqlalchemy.engine").setLevel(logging.WARNING)

    db = SessionLocal()
    try:
        summary = ingest_all(
            db,
            years_back=args.years,
            fetch_prices=not args.skip_prices,
            only_tickers=args.tickers,
        )
    finally:
        db.close()

    # Non-zero exit if nothing landed at all, so CI / scripts can react.
    if not args.skip_prices and summary.total_rows_written == 0:
        print("No price rows were written.", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
