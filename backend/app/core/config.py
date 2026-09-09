"""Application settings, loaded from environment variables (or a local .env)."""

from functools import lru_cache
from typing import Literal

from pydantic import model_validator
from pydantic_settings import BaseSettings, SettingsConfigDict


def _normalise_driver(url: str) -> str:
    """Force the psycopg2 driver onto a connection string.

    Render and some providers hand out the legacy "postgres://" scheme, which
    SQLAlchemy 2 does not recognise.
    """
    if url.startswith("postgres://"):
        url = url.replace("postgres://", "postgresql://", 1)
    if url.startswith("postgresql://"):
        url = url.replace("postgresql://", "postgresql+psycopg2://", 1)
    return url


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        case_sensitive=False,
        extra="ignore",
    )

    # --- App ---------------------------------------------------------------
    app_name: str = "Portfolio Risk Tool API"
    environment: Literal["development", "production"] = "development"
    debug: bool = True
    api_v1_prefix: str = "/api/v1"

    # --- Postgres ----------------------------------------------------------
    # These defaults describe the docker-compose database and exist ONLY for
    # local development. In production they are rejected outright (see
    # _require_production_config): a deployment that silently fell back to
    # "postgres@localhost" would fail in a confusing way at the first query
    # instead of refusing to start.
    postgres_user: str = "postgres"
    postgres_password: str = "postgres"
    postgres_host: str = "localhost"
    postgres_port: int = 5432
    postgres_db: str = "portfolio_risk"

    #: Full connection string. REQUIRED in production; overrides POSTGRES_*.
    #: This is what the application's engine uses, so on Neon it should be the
    #: POOLED endpoint — many short-lived requests are what the pooler is for.
    database_url: str | None = None

    #: Optional direct (unpooled) connection string, used by Alembic only.
    #: Neon's pooled endpoint runs in transaction pooling mode, which does not
    #: preserve the session state Alembic relies on (advisory locks, session
    #: -level settings, and DDL spanning statements), so migrations must use
    #: the direct endpoint. Unset locally, where docker-compose has a single
    #: Postgres and no pooling distinction — the fallback covers that.
    database_url_unpooled: str | None = None

    # --- Connection pool ---------------------------------------------------
    # Sized for serverless Postgres behind a 512 MB instance: a handful of
    # connections, recycled well before any idle timeout upstream.
    db_pool_size: int = 5
    db_max_overflow: int = 2
    db_pool_recycle_seconds: int = 280
    db_pool_timeout_seconds: int = 30
    db_connect_timeout_seconds: int = 15
    #: Neon terminates non-SSL connections. Applied when the URL omits sslmode.
    db_require_ssl: bool = False

    # --- CORS --------------------------------------------------------------
    # Local dev origins only. Production must set CORS_ORIGINS or
    # CORS_ORIGIN_REGEX to the real frontend domain, or the deployed site is
    # silently blocked by the browser with no server-side error to notice.
    cors_origins: str = "http://localhost:5173,http://127.0.0.1:5173"

    # Regex alternative, for hosts whose exact origin is not known ahead of
    # time — Vercel preview deployments get a new subdomain per commit.
    # e.g. CORS_ORIGIN_REGEX=https://.*\\.vercel\\.app
    cors_origin_regex: str = ""

    # --- Request budgets ---------------------------------------------------
    # Overridable per environment: local development gets generous limits, the
    # free production instance gets ones that fit inside its request timeout.
    # See core/limits.py for the measured timings behind each default.
    max_n_sims: int = 200_000
    max_bootstrap_resamples: int = 1_000
    #: block_bootstrap x walk_forward is the one combination that cannot reuse
    #: the optimiser cache, so it is budgeted separately and much lower.
    max_walk_forward_block_resamples: int = 100

    # --- Market data -------------------------------------------------------
    # NSE tickers on Yahoo Finance carry a ".NS" suffix (e.g. RELIANCE.NS).
    default_exchange: str = "NSE"
    yfinance_suffix: str = ".NS"

    @model_validator(mode="after")
    def _require_production_config(self) -> "Settings":
        """Fail fast rather than fall back to something that works locally."""
        if self.environment != "production":
            return self

        missing: list[str] = []
        if not self.database_url:
            missing.append(
                "DATABASE_URL (the POSTGRES_* defaults point at localhost and "
                "are not usable in production)"
            )

        origins = [o for o in self.cors_origin_list if "localhost" not in o and "127.0.0.1" not in o]
        if not origins and not self.cors_origin_regex:
            missing.append(
                "CORS_ORIGINS or CORS_ORIGIN_REGEX (only localhost origins are "
                "configured, so the deployed frontend would be blocked)"
            )

        if missing:
            raise ValueError(
                "ENVIRONMENT=production but required configuration is missing: "
                + "; ".join(missing)
            )
        return self

    @property
    def is_production(self) -> bool:
        return self.environment == "production"

    @property
    def sqlalchemy_database_uri(self) -> str:
        """Connection string for the application engine."""
        if self.database_url:
            return _normalise_driver(self.database_url)
        return (
            f"postgresql+psycopg2://{self.postgres_user}:{self.postgres_password}"
            f"@{self.postgres_host}:{self.postgres_port}/{self.postgres_db}"
        )

    @property
    def cors_origin_list(self) -> list[str]:
        """Allowed origins.

        In production the localhost default is dropped unless CORS_ORIGINS was
        set explicitly. Otherwise the development default would silently carry
        into production and leave a deployed API accepting requests from any
        developer's local frontend.
        """
        if self.is_production and "cors_origins" not in self.model_fields_set:
            return []
        return [origin.strip() for origin in self.cors_origins.split(",") if origin.strip()]


@lru_cache
def get_settings() -> Settings:
    """Cached settings accessor so the env is only parsed once per process."""
    return Settings()


settings = get_settings()
