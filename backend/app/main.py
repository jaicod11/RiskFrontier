"""FastAPI application entrypoint: app assembly, CORS, and the error contract."""

from __future__ import annotations

import logging

from fastapi import FastAPI, Request
from fastapi.exceptions import RequestValidationError
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse
from pydantic import BaseModel, Field
from starlette.exceptions import HTTPException as StarletteHTTPException

from app.core.config import settings
from app.core.errors import DomainError, ErrorResponse
from app.routers import (
    backtest_router,
    health_router,
    portfolio_router,
    risk_router,
    securities_router,
)

logger = logging.getLogger(__name__)

TAGS_METADATA = [
    {"name": "meta", "description": "Service metadata."},
    {"name": "health", "description": "Liveness and database connectivity."},
    {
        "name": "securities",
        "description": "The ingested universe and its price history.",
    },
    {
        "name": "risk",
        "description": "Monte Carlo VaR and CVaR by two independent methods.",
    },
    {
        "name": "portfolio",
        "description": "Markowitz mean-variance optimisation and the efficient frontier.",
    },
    {
        "name": "backtest",
        "description": (
            "Day-by-day strategy simulation against two baselines, and the "
            "bootstrapped distribution of outcomes across many windows."
        ),
    },
]

DESCRIPTION = """
Portfolio risk and backtesting for NSE-listed equities.

### Error contract

Every error — validation, domain, or unexpected — returns the same shape:

```json
{"error_code": "INSUFFICIENT_COVERAGE", "message": "...", "details": {}}
```

Branch on `error_code`; `message` is written to be shown to a user.

### Limitations

Every analytical response carries a `limitations` array. These are returned by
the backend so a client cannot drop them. Several are large enough to reverse a
conclusion — see `docs/LIMITATIONS.md`.
"""

app = FastAPI(
    title=settings.app_name,
    version="0.7.0",
    description=DESCRIPTION,
    openapi_tags=TAGS_METADATA,
)

app.add_middleware(
    CORSMiddleware,
    allow_origins=settings.cors_origin_list,
    allow_origin_regex=settings.cors_origin_regex or None,
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)


# ---------------------------------------------------------------------------
# The error contract
# ---------------------------------------------------------------------------


def _error(status: int, code: str, message: str, details: dict | None = None):
    return JSONResponse(
        status_code=status,
        content=ErrorResponse(
            error_code=code, message=message, details=details or {}
        ).model_dump(),
    )


@app.exception_handler(DomainError)
async def handle_domain_error(request: Request, exc: DomainError) -> JSONResponse:
    """Every service-raised domain error, using the code it declares itself."""
    logger.info(
        "%s on %s: %s", exc.error_code, request.url.path, exc.message
    )
    return _error(exc.http_status, exc.error_code, exc.message, exc.details)


@app.exception_handler(RequestValidationError)
async def handle_validation_error(
    request: Request, exc: RequestValidationError
) -> JSONResponse:
    """Reshape Pydantic's errors into the same contract.

    Pydantic keeps the exception a validator raised under ``ctx["error"]``, so a
    domain error raised inside a model validator (weights not summing to 1, a
    request cap exceeded) keeps its specific ``error_code`` instead of being
    flattened into a generic validation failure.
    """
    raw = exc.errors()

    error_code = "VALIDATION_ERROR"
    details: dict = {}
    for item in raw:
        inner = item.get("ctx", {}).get("error")
        if isinstance(inner, DomainError):
            error_code = inner.error_code
            details = dict(inner.details)
            break

    fields = [
        {
            "field": ".".join(str(part) for part in item.get("loc", ())[1:]) or "body",
            "message": item.get("msg", ""),
        }
        for item in raw
    ]
    details.setdefault("fields", fields)

    message = fields[0]["message"] if fields else "Request validation failed"
    message = message.removeprefix("Value error, ")
    if len(fields) > 1:
        message = f"{message} (and {len(fields) - 1} more field error(s))"

    logger.info("%s on %s: %s", error_code, request.url.path, message)
    return _error(422, error_code, message, details)


@app.exception_handler(StarletteHTTPException)
async def handle_http_exception(
    request: Request, exc: StarletteHTTPException
) -> JSONResponse:
    """Route FastAPI's own HTTPExceptions through the same shape."""
    code = "NOT_FOUND" if exc.status_code == 404 else "HTTP_ERROR"
    detail = exc.detail
    message = detail if isinstance(detail, str) else str(detail)
    return _error(exc.status_code, code, message)


@app.exception_handler(Exception)
async def handle_unexpected(request: Request, exc: Exception) -> JSONResponse:
    """Anything unplanned: generic message out, full traceback in the log."""
    logger.exception(
        "Unhandled %s on %s %s", type(exc).__name__, request.method, request.url.path
    )
    return _error(
        500,
        "INTERNAL_ERROR",
        "An internal error occurred. The failure has been logged; if it "
        "persists, report the time of the request.",
    )


# ---------------------------------------------------------------------------
# Routers
# ---------------------------------------------------------------------------

app.include_router(health_router)
app.include_router(securities_router)
app.include_router(risk_router)
app.include_router(portfolio_router)
app.include_router(backtest_router)


class ServiceInfo(BaseModel):
    service: str
    version: str
    docs: str = Field(description="Path to the interactive API documentation")


@app.get(
    "/",
    response_model=ServiceInfo,
    tags=["meta"],
    summary="Service metadata",
    description="Name, version and where to find the interactive documentation.",
)
def root() -> ServiceInfo:
    return ServiceInfo(service=settings.app_name, version=app.version, docs="/docs")
