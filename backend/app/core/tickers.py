"""Ticker-form helpers, kept dependency-free so any layer can import them.

``securities.ticker`` holds the bare NSE symbol (``RELIANCE``, ``M&M``,
``BAJAJ-AUTO``); the Yahoo Finance symbol is that plus ``.NS``.

Index symbols are the exception: Yahoo names indices with a ``^`` prefix and no
exchange suffix (``^NSEI`` is the Nifty 50). They are already in Yahoo form, so
both helpers pass them through untouched.
"""

from __future__ import annotations

from app.core.config import settings


def is_index_symbol(ticker: str) -> bool:
    """Yahoo index symbols carry a ``^`` prefix and never an exchange suffix."""
    return ticker.strip().startswith("^")


def to_nse_symbol(ticker: str) -> str:
    """``RELIANCE.NS`` -> ``RELIANCE``. Idempotent. Indices pass through."""
    ticker = ticker.strip().upper()
    if is_index_symbol(ticker):
        return ticker
    suffix = settings.yfinance_suffix.upper()
    if suffix and ticker.endswith(suffix):
        return ticker[: -len(suffix)]
    return ticker


def to_yf_symbol(ticker: str) -> str:
    """``RELIANCE`` -> ``RELIANCE.NS``. Idempotent. Indices pass through.

    Appending ``.NS`` to ``^NSEI`` would produce a symbol Yahoo does not know.
    """
    ticker = ticker.strip().upper()
    if is_index_symbol(ticker):
        return ticker
    suffix = settings.yfinance_suffix
    if suffix and ticker.endswith(suffix.upper()):
        return ticker
    return f"{ticker}{suffix}"
