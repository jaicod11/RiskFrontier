"""FastAPI application entrypoint."""

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from app.core.config import settings
from app.routers import health_router, securities_router

app = FastAPI(
    title=settings.app_name,
    version="0.1.0",
    description=(
        "Portfolio risk and backtesting API for NSE-listed equities. "
        "Scaffold only -- risk, optimisation and backtest endpoints are not implemented yet."
    ),
)

app.add_middleware(
    CORSMiddleware,
    allow_origins=settings.cors_origin_list,
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

app.include_router(health_router)
app.include_router(securities_router)


@app.get("/", tags=["meta"])
def root() -> dict[str, str]:
    return {"service": settings.app_name, "version": "0.1.0", "docs": "/docs"}
