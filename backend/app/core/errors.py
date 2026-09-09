"""One error contract for the whole API.

Services raise domain exceptions; a handler in ``main.py`` turns each into the
same JSON shape. Nothing in a router formats an error by hand, so the frontend
has exactly one thing to parse:

    {"error_code": "...", "message": "...", "details": {...}}

Every domain exception carries the ``error_code`` and HTTP status it should
produce, so adding one does not mean editing a mapping table somewhere else.
"""

from __future__ import annotations

from typing import Any

from pydantic import BaseModel, Field


class ErrorResponse(BaseModel):
    """The only error shape this API returns."""

    error_code: str = Field(
        description="Stable machine-readable code; safe to branch on",
        examples=["INSUFFICIENT_COVERAGE"],
    )
    message: str = Field(
        description="Human-readable and actionable: names the offending value",
        examples=[
            "JIOFIN has data from 2023-08-21 to 2026-09-07, which does not "
            "cover the requested 2022-01-03 to 2026-06-30"
        ],
    )
    details: dict[str, Any] = Field(
        default_factory=dict,
        description="Structured context — field paths, offending tickers, limits",
    )


class DomainError(Exception):
    """Base for every error that is the caller's to fix.

    Subclasses set ``error_code``; the default status is 422 because these all
    describe a well-formed request the service cannot satisfy.
    """

    error_code = "DOMAIN_ERROR"
    http_status = 422

    def __init__(self, message: str, details: dict[str, Any] | None = None) -> None:
        super().__init__(message)
        self.message = message
        self.details = details or {}


# --- Data availability ------------------------------------------------------


class UnknownTickerError(DomainError, LookupError):
    """A ticker is not present in the securities table."""

    error_code = "UNKNOWN_TICKER"


class InsufficientCoverageError(DomainError, ValueError):
    """Price history does not span the requested range."""

    error_code = "INSUFFICIENT_COVERAGE"


class InsufficientLookbackError(InsufficientCoverageError):
    """A strategy needs more history before the start than exists."""

    error_code = "INSUFFICIENT_LOOKBACK"


# --- Optimisation and strategy ---------------------------------------------


class InfeasibleConstraintsError(DomainError, ValueError):
    """The requested constraints admit no portfolio at all."""

    error_code = "INFEASIBLE_CONSTRAINTS"


class OptimizationFailedError(DomainError, RuntimeError):
    """The solver did not converge from any starting point."""

    error_code = "OPTIMIZATION_FAILED"


class StrategyContractError(DomainError, ValueError):
    """A rebalance_fn returned weights the engine cannot act on."""

    error_code = "STRATEGY_CONTRACT_VIOLATION"


# --- Request shape ----------------------------------------------------------


class InvalidWeightsError(DomainError, ValueError):
    """Portfolio weights do not sum to 1, duplicate, or reference a stranger."""

    error_code = "INVALID_WEIGHTS"


class InvalidParameterError(DomainError, ValueError):
    """A parameter is individually valid but unusable in context."""

    error_code = "INVALID_PARAMETER"


class RequestLimitExceededError(DomainError, ValueError):
    """A request asks for more work than the server will accept."""

    error_code = "REQUEST_LIMIT_EXCEEDED"


#: Codes the API can return, for documentation and for frontend exhaustiveness.
ERROR_CODES: tuple[str, ...] = (
    "VALIDATION_ERROR",
    "UNKNOWN_TICKER",
    "INSUFFICIENT_COVERAGE",
    "INSUFFICIENT_LOOKBACK",
    "INFEASIBLE_CONSTRAINTS",
    "OPTIMIZATION_FAILED",
    "STRATEGY_CONTRACT_VIOLATION",
    "INVALID_WEIGHTS",
    "INVALID_PARAMETER",
    "REQUEST_LIMIT_EXCEEDED",
    "NOT_FOUND",
    "INTERNAL_ERROR",
)
