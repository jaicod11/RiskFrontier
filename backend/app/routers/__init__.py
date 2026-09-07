"""API routers. Feature routers (portfolios, risk, backtests) land here."""

from app.routers.health import router as health_router

__all__ = ["health_router"]
