"""Liveness / readiness endpoint."""

import logging

from fastapi import APIRouter, Depends
from fastapi.responses import JSONResponse
from sqlalchemy import text
from sqlalchemy.exc import SQLAlchemyError
from sqlalchemy.orm import Session

from app.core.db import get_db

logger = logging.getLogger(__name__)

router = APIRouter(tags=["health"])


@router.get("/health", summary="Service and database health")
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
