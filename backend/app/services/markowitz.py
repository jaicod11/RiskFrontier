"""Markowitz mean-variance optimisation.

Estimates annualised expected returns and covariance from a daily return
window, then solves for portfolios on the efficient frontier with SLSQP.

Every optimisation is run from several starting points and the best converged
result is kept: SLSQP is a local method, and a mean-variance problem with box
constraints can present it with flat regions where a single start lands short of
the optimum.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field

import numpy as np
import pandas as pd
from scipy.optimize import minimize

logger = logging.getLogger(__name__)

TRADING_DAYS_PER_YEAR = 252

# India's 10-year G-Sec yields roughly 6.95% as of September 2026. A Sharpe
# ratio wants the return on a *short-tenor* risk-free asset, not a 10-year bond
# -- the 10-year carries duration risk that the Sharpe denominator does not
# account for. 6.5% is a rounder, slightly conservative stand-in for the
# short end. Override per request when a better rate is available.
DEFAULT_RISK_FREE_RATE = 0.065

#: No single holding above 35% unless the caller relaxes it. Concentration is
#: the failure mode of naive mean-variance optimisation: it happily puts
#: everything in whichever asset had the best realised mean.
DEFAULT_MAX_WEIGHT_PER_ASSET = 0.35

DEFAULT_FRONTIER_POINTS = 30

#: Starting points per optimisation: equal weight plus this many random ones.
N_RANDOM_STARTS = 2

_SLSQP_OPTIONS = {"maxiter": 400, "ftol": 1e-12}
_WEIGHT_SUM_TOLERANCE = 1e-6


class InfeasibleConstraintsError(ValueError):
    """The requested constraints admit no portfolio at all."""


@dataclass
class OptimizedPoint:
    """One portfolio: its weights and the statistics they imply."""

    expected_return: float
    volatility: float
    sharpe_ratio: float
    weights: dict[str, float]


@dataclass
class FrontierTrace:
    """The frontier plus a record of any targets that could not be solved."""

    points: list[OptimizedPoint] = field(default_factory=list)
    requested_points: int = 0
    skipped_target_returns: list[float] = field(default_factory=list)


# ---------------------------------------------------------------------------
# Estimation
# ---------------------------------------------------------------------------


def annualised_moments(
    returns_df: pd.DataFrame, trading_days: int = TRADING_DAYS_PER_YEAR
) -> tuple[np.ndarray, np.ndarray]:
    """Annualised mean vector and covariance matrix from daily returns.

    Means scale linearly with time and covariances scale linearly with time, so
    both are simply multiplied by the number of trading days in a year.
    """
    if returns_df.empty:
        raise ValueError("returns_df is empty — nothing to estimate from")
    if len(returns_df) < 2:
        raise ValueError(
            f"Need at least 2 days of returns to estimate covariance, "
            f"got {len(returns_df)}"
        )

    mu = returns_df.mean().to_numpy(dtype=np.float64) * trading_days
    cov = returns_df.cov().to_numpy(dtype=np.float64) * trading_days
    return mu, cov


def portfolio_return(weights: np.ndarray, mu: np.ndarray) -> float:
    return float(weights @ mu)


def portfolio_volatility(weights: np.ndarray, cov: np.ndarray) -> float:
    variance = float(weights @ cov @ weights)
    # Tiny negatives can appear through floating-point error near zero variance.
    return float(np.sqrt(max(variance, 0.0)))


def sharpe_ratio(
    weights: np.ndarray, mu: np.ndarray, cov: np.ndarray, risk_free_rate: float
) -> float:
    volatility = portfolio_volatility(weights, cov)
    if volatility <= 0:
        return 0.0
    return (portfolio_return(weights, mu) - risk_free_rate) / volatility


# ---------------------------------------------------------------------------
# Constraint plumbing
# ---------------------------------------------------------------------------


def _bounds(
    n_assets: int, max_weight: float, allow_short: bool
) -> list[tuple[float, float]]:
    lower = -max_weight if allow_short else 0.0
    return [(lower, max_weight)] * n_assets


def _check_feasible(n_assets: int, max_weight: float, allow_short: bool) -> None:
    """A cap below 1/n makes summing to 1 impossible."""
    if max_weight * n_assets < 1.0 - _WEIGHT_SUM_TOLERANCE:
        minimum = 1.0 / n_assets
        raise InfeasibleConstraintsError(
            f"max_weight_per_asset={max_weight:g} cannot be satisfied by "
            f"{n_assets} assets: their weights could sum to at most "
            f"{max_weight * n_assets:.3f}, never 1.0. Raise it to at least "
            f"{minimum:.4f} (1/{n_assets}) or include more tickers."
        )
    if allow_short and max_weight <= 0:
        raise InfeasibleConstraintsError("max_weight_per_asset must be positive")


def _random_start(
    rng: np.random.Generator, n_assets: int, bounds: list[tuple[float, float]]
) -> np.ndarray:
    """A feasible-ish random weight vector to seed the solver.

    SLSQP enforces the bounds itself, so a start that is merely close is fine;
    the point is to probe a different basin than equal weight.
    """
    low = np.array([b[0] for b in bounds])
    high = np.array([b[1] for b in bounds])

    weights = rng.dirichlet(np.ones(n_assets))
    for _ in range(10):
        weights = np.clip(weights, low, high)
        total = weights.sum()
        if abs(total - 1.0) < 1e-9:
            break
        if total == 0:
            weights = np.full(n_assets, 1.0 / n_assets)
            break
        weights = weights / total
    return np.clip(weights, low, high)


def _solve(
    objective,
    n_assets: int,
    bounds: list[tuple[float, float]],
    constraints: list[dict],
    seed: int | None = None,
) -> np.ndarray | None:
    """Run SLSQP from several starts; return the best converged weights.

    Returns ``None`` when no start converged, so callers can skip rather than
    propagate a failure.
    """
    rng = np.random.default_rng(seed)
    starts = [np.full(n_assets, 1.0 / n_assets)]
    starts += [_random_start(rng, n_assets, bounds) for _ in range(N_RANDOM_STARTS)]

    best_weights: np.ndarray | None = None
    best_value = np.inf

    for start in starts:
        try:
            result = minimize(
                objective,
                start,
                method="SLSQP",
                bounds=bounds,
                constraints=constraints,
                options=_SLSQP_OPTIONS,
            )
        except Exception as exc:  # noqa: BLE001 - a bad start must not abort
            logger.debug("SLSQP raised from one start: %s", exc)
            continue

        if not result.success or not np.all(np.isfinite(result.x)):
            continue
        if result.fun < best_value:
            best_value = float(result.fun)
            best_weights = np.asarray(result.x, dtype=np.float64)

    if best_weights is None:
        return None

    low = np.array([b[0] for b in bounds])
    high = np.array([b[1] for b in bounds])
    return _project_to_constraints(best_weights, low, high)


def _project_to_constraints(
    weights: np.ndarray,
    low: np.ndarray,
    high: np.ndarray,
    tolerance: float = 1e-12,
    max_iterations: int = 100,
) -> np.ndarray:
    """Nudge float dust back onto {sum == 1, low <= w <= high}.

    Naive cleanup cannot do both at once: normalising can push a weight past
    its cap, and clipping afterwards then drops the sum below 1. Instead the
    remaining gap is distributed in proportion to each weight's *remaining
    room*, and the result re-clipped, until both constraints hold.
    """
    projected = np.clip(weights, low, high)

    for _ in range(max_iterations):
        gap = 1.0 - float(projected.sum())
        if abs(gap) <= tolerance:
            return projected

        # Only weights with headroom in the needed direction can absorb the gap.
        room = (high - projected) if gap > 0 else (projected - low)
        capacity = float(room.sum())
        if capacity <= tolerance:
            break  # Constraints cannot be satisfied; caller validates.

        projected = np.clip(projected + gap * (room / capacity), low, high)

    return projected


def _sum_to_one_constraint() -> dict:
    return {"type": "eq", "fun": lambda w: float(np.sum(w) - 1.0)}


def _as_point(
    weights: np.ndarray,
    tickers: list[str],
    mu: np.ndarray,
    cov: np.ndarray,
    risk_free_rate: float,
) -> OptimizedPoint:
    return OptimizedPoint(
        expected_return=portfolio_return(weights, mu),
        volatility=portfolio_volatility(weights, cov),
        sharpe_ratio=sharpe_ratio(weights, mu, cov, risk_free_rate),
        weights={t: float(w) for t, w in zip(tickers, weights)},
    )


def max_achievable_return(
    mu: np.ndarray, bounds: list[tuple[float, float]]
) -> float:
    """Highest expected return reachable under the box and budget constraints.

    Exact: with only box bounds and a sum-to-one budget this is a linear
    program whose solution is greedy — start everything at its lower bound and
    pour the remaining budget into the highest-mean assets in turn.

    Used as the frontier's upper endpoint. When ``max_weight`` is 1.0 this
    equals the best single asset's return, which is the uncapped intuition;
    with a binding cap that return is simply not reachable, and targeting it
    would only generate points the solver must reject.
    """
    low = np.array([b[0] for b in bounds], dtype=np.float64)
    high = np.array([b[1] for b in bounds], dtype=np.float64)

    weights = low.copy()
    remaining = 1.0 - weights.sum()
    for index in np.argsort(mu)[::-1]:
        if remaining <= 1e-12:
            break
        room = min(high[index] - weights[index], remaining)
        weights[index] += room
        remaining -= room
    return float(weights @ mu)


# ---------------------------------------------------------------------------
# The three optimisations
# ---------------------------------------------------------------------------


def min_variance_portfolio(
    returns_df: pd.DataFrame,
    risk_free_rate: float = DEFAULT_RISK_FREE_RATE,
    max_weight_per_asset: float = DEFAULT_MAX_WEIGHT_PER_ASSET,
    allow_short: bool = False,
    seed: int | None = 0,
) -> OptimizedPoint:
    """The lowest-variance portfolio satisfying the constraints."""
    tickers = list(returns_df.columns)
    mu, cov = annualised_moments(returns_df)
    _check_feasible(len(tickers), max_weight_per_asset, allow_short)

    bounds = _bounds(len(tickers), max_weight_per_asset, allow_short)
    weights = _solve(
        lambda w: float(w @ cov @ w),
        len(tickers),
        bounds,
        [_sum_to_one_constraint()],
        seed=seed,
    )
    if weights is None:
        raise RuntimeError(
            "Minimum-variance optimisation did not converge from any start"
        )
    return _as_point(weights, tickers, mu, cov, risk_free_rate)


def max_sharpe_portfolio(
    returns_df: pd.DataFrame,
    risk_free_rate: float = DEFAULT_RISK_FREE_RATE,
    max_weight_per_asset: float = DEFAULT_MAX_WEIGHT_PER_ASSET,
    allow_short: bool = False,
    seed: int | None = 0,
) -> OptimizedPoint:
    """The portfolio maximising (return - risk_free_rate) / volatility."""
    tickers = list(returns_df.columns)
    mu, cov = annualised_moments(returns_df)
    _check_feasible(len(tickers), max_weight_per_asset, allow_short)

    def negative_sharpe(w: np.ndarray) -> float:
        volatility = portfolio_volatility(w, cov)
        if volatility < 1e-12:
            # Degenerate: no risk to divide by. Penalise so the solver moves on.
            return 1e6
        return -float((w @ mu - risk_free_rate) / volatility)

    bounds = _bounds(len(tickers), max_weight_per_asset, allow_short)
    weights = _solve(
        negative_sharpe,
        len(tickers),
        bounds,
        [_sum_to_one_constraint()],
        seed=seed,
    )
    if weights is None:
        raise RuntimeError("Max-Sharpe optimisation did not converge from any start")
    return _as_point(weights, tickers, mu, cov, risk_free_rate)


def efficient_frontier(
    returns_df: pd.DataFrame,
    n_points: int = DEFAULT_FRONTIER_POINTS,
    risk_free_rate: float = DEFAULT_RISK_FREE_RATE,
    max_weight_per_asset: float = DEFAULT_MAX_WEIGHT_PER_ASSET,
    allow_short: bool = False,
    seed: int | None = 0,
    target_returns: list[float] | None = None,
) -> FrontierTrace:
    """Trace the frontier by minimising variance at a series of target returns.

    Targets span the minimum-variance portfolio's return up to the highest
    return the constraints allow. A target the solver cannot reach is logged
    and skipped -- one unreachable point must not cost the caller the whole
    frontier.

    ``target_returns`` overrides the spanned targets, mainly so tests can probe
    infeasible ones.
    """
    tickers = list(returns_df.columns)
    n_assets = len(tickers)
    mu, cov = annualised_moments(returns_df)
    _check_feasible(n_assets, max_weight_per_asset, allow_short)

    bounds = _bounds(n_assets, max_weight_per_asset, allow_short)

    if target_returns is None:
        floor = min_variance_portfolio(
            returns_df, risk_free_rate, max_weight_per_asset, allow_short, seed
        ).expected_return
        ceiling = max_achievable_return(mu, bounds)
        if ceiling <= floor:
            targets = [floor]
        else:
            targets = list(np.linspace(floor, ceiling, max(n_points, 2)))
    else:
        targets = list(target_returns)

    trace = FrontierTrace(requested_points=len(targets))

    for target in targets:
        constraints = [
            _sum_to_one_constraint(),
            # Return at least the target; the frontier is the lower envelope.
            {"type": "ineq", "fun": (lambda w, t=target: float(w @ mu - t))},
        ]
        weights = _solve(
            lambda w: float(w @ cov @ w),
            n_assets,
            bounds,
            constraints,
            seed=seed,
        )
        if weights is None:
            logger.warning(
                "Frontier point skipped: no start converged for target return "
                "%.6f (%.2f%% annualised)", target, target * 100,
            )
            trace.skipped_target_returns.append(float(target))
            continue

        achieved = portfolio_return(weights, mu)
        if achieved < target - 1e-6:
            # Converged, but not to a point that meets the constraint.
            logger.warning(
                "Frontier point skipped: target %.6f unreachable "
                "(best achieved %.6f)", target, achieved,
            )
            trace.skipped_target_returns.append(float(target))
            continue

        trace.points.append(_as_point(weights, tickers, mu, cov, risk_free_rate))

    trace.points.sort(key=lambda p: p.volatility)
    return trace
