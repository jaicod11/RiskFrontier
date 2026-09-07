"""API routers. Feature routers (portfolios, risk, backtests) land here."""

from app.routers.backtest import router as backtest_router
from app.routers.health import router as health_router
from app.routers.portfolio import router as portfolio_router
from app.routers.risk import router as risk_router
from app.routers.securities import router as securities_router

__all__ = [
    "backtest_router",
    "health_router",
    "portfolio_router",
    "risk_router",
    "securities_router",
]
