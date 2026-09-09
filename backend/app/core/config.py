"""Application settings, loaded from environment variables (or a local .env)."""

from functools import lru_cache

from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        case_sensitive=False,
        extra="ignore",
    )

    # --- App ---------------------------------------------------------------
    app_name: str = "Portfolio Risk Tool API"
    environment: str = "development"
    debug: bool = True
    api_v1_prefix: str = "/api/v1"

    # --- Postgres ----------------------------------------------------------
    postgres_user: str = "postgres"
    postgres_password: str = "postgres"
    postgres_host: str = "localhost"
    postgres_port: int = 5432
    postgres_db: str = "portfolio_risk"

    # Set DATABASE_URL to override the individual POSTGRES_* values above.
    database_url: str | None = None

    # --- CORS --------------------------------------------------------------
    # Comma-separated list, e.g. "http://localhost:5173,http://127.0.0.1:5173"
    cors_origins: str = "http://localhost:5173,http://127.0.0.1:5173"

    # Regex alternative, for hosts whose exact origin is not known ahead of
    # time — Vercel preview deployments get a new subdomain per commit.
    # e.g. CORS_ORIGIN_REGEX=https://.*\\.vercel\\.app
    cors_origin_regex: str = ""

    # --- Market data -------------------------------------------------------
    # NSE tickers on Yahoo Finance carry a ".NS" suffix (e.g. RELIANCE.NS).
    default_exchange: str = "NSE"
    yfinance_suffix: str = ".NS"

    @property
    def sqlalchemy_database_uri(self) -> str:
        """Full SQLAlchemy connection string."""
        if self.database_url:
            return self.database_url
        return (
            f"postgresql+psycopg2://{self.postgres_user}:{self.postgres_password}"
            f"@{self.postgres_host}:{self.postgres_port}/{self.postgres_db}"
        )

    @property
    def cors_origin_list(self) -> list[str]:
        return [origin.strip() for origin in self.cors_origins.split(",") if origin.strip()]


@lru_cache
def get_settings() -> Settings:
    """Cached settings accessor so the env is only parsed once per process."""
    return Settings()


settings = get_settings()
