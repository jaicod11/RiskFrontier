"""SQLAlchemy engine and session management.

Pool settings target serverless Postgres (Neon) behind a small instance:

* ``pool_pre_ping`` — Neon's free tier scales to zero when idle, so a pooled
  connection can be dead by the time it is reused. Pre-ping issues a cheap
  liveness check and transparently replaces a stale connection instead of
  surfacing it to the caller as a 500 on the first request after a pause.
* ``pool_recycle`` — connections are discarded well before any upstream idle
  timeout can close them underneath us.
* small ``pool_size``/``max_overflow`` — serverless Postgres charges for
  connections and a 512 MB instance has no use for many.
* ``connect_timeout`` — a resuming database should surface as a slow request,
  not a hung worker.
"""

from collections.abc import Generator

from sqlalchemy import create_engine
from sqlalchemy.engine import make_url
from sqlalchemy.orm import Session, sessionmaker

from app.core.config import settings


def _connect_args() -> dict:
    """psycopg2 connect args, adding SSL only when the URL does not set it."""
    args: dict = {"connect_timeout": settings.db_connect_timeout_seconds}

    url = make_url(settings.sqlalchemy_database_uri)
    already_specified = "sslmode" in (url.query or {})
    if settings.db_require_ssl and not already_specified:
        args["sslmode"] = "require"
    return args


engine = create_engine(
    settings.sqlalchemy_database_uri,
    pool_pre_ping=True,
    pool_size=settings.db_pool_size,
    max_overflow=settings.db_max_overflow,
    pool_recycle=settings.db_pool_recycle_seconds,
    pool_timeout=settings.db_pool_timeout_seconds,
    connect_args=_connect_args(),
    echo=settings.debug and not settings.is_production,
    future=True,
)

SessionLocal = sessionmaker(
    bind=engine,
    autocommit=False,
    autoflush=False,
    expire_on_commit=False,
    class_=Session,
)


def get_db() -> Generator[Session, None, None]:
    """FastAPI dependency that yields a request-scoped session."""
    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()
