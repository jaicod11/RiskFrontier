"""Liveness / readiness endpoint."""

import logging

from fastapi import APIRouter, Depends
from pydantic import BaseModel, Field
from fastapi.responses import JSONResponse
from sqlalchemy import text
from sqlalchemy.exc import SQLAlchemyError
from sqlalchemy.orm import Session

from app.core.db import get_db

logger = logging.getLogger(__name__)

router = APIRouter(tags=["health"])


class HealthStatus(BaseModel):
    status: str = Field(description='"ok" when the database round-trips')
    db: str = Field(description='"connected" or "disconnected"')


@router.get(
    "/health",
    response_model=HealthStatus,
    summary="Service and database health",
    description=(
        "Returns 200 with `db: connected` only if a trivial query round-trips "
        "to Postgres. Returns 503 with `db: disconnected` otherwise, in the "
        "same shape, so a client can parse either outcome identically."
    ),
    responses={503: {"model": HealthStatus, "description": "Database unreachable"}},
)
def health(db: Session = Depends(get_db)) -> JSONResponse:
    """Return 200 with db="connected" only if a trivial query round-trips."""
    try:
        db.execute(text("SELECT 1"))
    except SQLAlchemyError as exc:
        logger.warning("Health check failed to reach the database: %s", exc)
        return JSONResponse(
            status_code=503,
            content={"status": "error", "db": "disconnected"},
        )

    return JSONResponse(status_code=200, content={"status": "ok", "db": "connected"})
