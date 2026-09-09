"""Monte Carlo VaR / CVaR for a portfolio, by two independent methods.

Both simulate ``horizon_days`` forward and compound day by day inside every
path -- no square-root-of-time scaling, which would assume returns are
independent and identically distributed across the horizon.

Where they differ is the assumption about the *shape* of the return
distribution:

``run_parametric_var``
    Fits a multivariate normal to the window (mean vector + covariance matrix)
    and draws correlated normal shocks via a Cholesky factor. Smooth, but
    normal tails are thinner than real equity tails.

``run_historical_bootstrap_var``
    Makes no distributional assumption. It resamples *whole historical days*:
    each simulated day is one real date, with every ticker's realised return
    from that date taken together. That keeps the cross-sectional correlation
    that actually occurred -- including the days when everything fell at once,
    which resampling each ticker independently would destroy.

Both are fully vectorised. Work is done in blocks purely to bound peak memory;
there is no per-simulation Python loop.
"""

from __future__ import annotations

import logging
from collections.abc import Sequence

import numpy as np
import pandas as pd

from app.core.errors import InvalidParameterError
from app.core.limits import DEFAULT_DISTRIBUTION_BINS
from app.schemas.risk import MethodResult, PnlDistribution, VarEstimate

logger = logging.getLogger(__name__)

PARAMETRIC = "parametric"
HISTORICAL_BOOTSTRAP = "historical_bootstrap"

_DESCRIPTIONS = {
    PARAMETRIC: (
        "Multivariate normal fitted to the window; correlated shocks drawn via "
        "a Cholesky factor of the covariance matrix and compounded daily."
    ),
    HISTORICAL_BOOTSTRAP: (
        "Whole historical days resampled with replacement, preserving the "
        "cross-sectional correlation realised on each day; compounded daily."
    ),
}

#: Cap on elements held at once (sims x horizon x assets). ~5M float64 ≈ 40 MB,
#: so a 200k-sim, 252-day, 50-asset request stays bounded instead of asking for
#: gigabytes. Blocks are large, so this stays vectorised.
MAX_BLOCK_ELEMENTS = 5_000_000


def _validate_inputs(
    returns_df: pd.DataFrame,
    weights: Sequence[float],
    total_value: float,
    n_sims: int,
    horizon_days: int,
) -> np.ndarray:
    if returns_df.empty:
        raise InvalidParameterError("returns_df is empty — no data to simulate from")
    if len(returns_df) < 2:
        raise InvalidParameterError(
            f"Need at least 2 days of returns, got {len(returns_df)}"
        )
    if len(weights) != returns_df.shape[1]:
        raise InvalidParameterError(
            f"Got {len(weights)} weights for {returns_df.shape[1]} tickers "
            f"({', '.join(map(str, returns_df.columns))})"
        )
    if total_value <= 0:
        raise InvalidParameterError(f"total_value must be positive, got {total_value}")
    if n_sims < 1:
        raise InvalidParameterError(f"n_sims must be at least 1, got {n_sims}")
    if horizon_days < 1:
        raise InvalidParameterError(f"horizon_days must be at least 1, got {horizon_days}")

    w = np.asarray(weights, dtype=np.float64)
    if not np.all(np.isfinite(w)):
        raise InvalidParameterError("weights must all be finite")
    return w


def _block_size(n_sims: int, horizon_days: int, n_assets: int) -> int:
    per_sim = max(1, horizon_days * n_assets)
    return max(1, min(n_sims, MAX_BLOCK_ELEMENTS // per_sim))


def _safe_cholesky(cov: np.ndarray) -> np.ndarray:
    """Cholesky factor, repairing a covariance matrix that is not quite PSD.

    Sample covariance can come back marginally non-positive-definite through
    floating-point error, or genuinely singular when two tickers move
    identically over the window. Clipping the eigenvalues yields the nearest
    usable matrix instead of failing the request.
    """
    try:
        return np.linalg.cholesky(cov)
    except np.linalg.LinAlgError:
        logger.warning(
            "Covariance matrix is not positive definite; repairing via "
            "eigenvalue clipping (tickers may be collinear over this window)"
        )
        eigenvalues, eigenvectors = np.linalg.eigh(cov)
        clipped = np.clip(eigenvalues, 1e-14, None)
        repaired = eigenvectors @ np.diag(clipped) @ eigenvectors.T
        jitter = 1e-12 * np.eye(cov.shape[0])
        return np.linalg.cholesky(repaired + jitter)


def _summarise(
    path_returns: np.ndarray,
    total_value: float,
    confidence_levels: Sequence[float],
    method: str,
) -> MethodResult:
    """Turn simulated horizon returns into VaR/CVaR at each confidence level."""
    pnl = total_value * path_returns

    estimates: list[VarEstimate] = []
    for level in confidence_levels:
        # The loss threshold sits in the left tail: the (1-c) quantile of P&L.
        threshold = float(np.quantile(pnl, 1.0 - level))
        tail = pnl[pnl <= threshold]
        # Reported as positive loss magnitudes.
        var_inr = -threshold
        cvar_inr = -float(tail.mean()) if tail.size else var_inr
        estimates.append(
            VarEstimate(
                confidence_level=float(level),
                var_inr=var_inr,
                var_pct=var_inr / total_value * 100.0,
                cvar_inr=cvar_inr,
                cvar_pct=cvar_inr / total_value * 100.0,
            )
        )

    return MethodResult(
        method=method,
        description=_DESCRIPTIONS[method],
        estimates=estimates,
        mean_horizon_return_pct=float(path_returns.mean()) * 100.0,
        worst_simulated_pnl_inr=float(pnl.min()),
        best_simulated_pnl_inr=float(pnl.max()),
    )


def run_parametric_var(
    returns_df: pd.DataFrame,
    weights: Sequence[float],
    total_value: float,
    n_sims: int = 10_000,
    horizon_days: int = 1,
    confidence_levels: Sequence[float] = (0.95, 0.99),
    seed: int | None = None,
) -> MethodResult:
    """VaR/CVaR assuming returns are multivariate normal.

    The portfolio is rebalanced to ``weights`` each simulated day: the daily
    portfolio return is ``weights · daily asset returns``, and those daily
    returns are compounded across the horizon.
    """
    path_returns = parametric_path_returns(
        returns_df, weights, total_value, n_sims, horizon_days, seed
    )
    return _summarise(path_returns, total_value, confidence_levels, PARAMETRIC)


def parametric_path_returns(
    returns_df: pd.DataFrame,
    weights: Sequence[float],
    total_value: float,
    n_sims: int = 10_000,
    horizon_days: int = 1,
    seed: int | None = None,
) -> np.ndarray:
    """Simulated horizon returns under the multivariate normal fit.

    Split out so the same array can feed both the summary statistics and the
    P&L histogram without simulating twice.
    """
    w = _validate_inputs(returns_df, weights, total_value, n_sims, horizon_days)
    n_assets = returns_df.shape[1]

    mu = returns_df.mean().to_numpy(dtype=np.float64)
    cov = returns_df.cov().to_numpy(dtype=np.float64)
    cholesky_factor = _safe_cholesky(cov)

    rng = np.random.default_rng(seed)
    block = _block_size(n_sims, horizon_days, n_assets)
    path_returns = np.empty(n_sims, dtype=np.float64)

    for start in range(0, n_sims, block):
        size = min(block, n_sims - start)
        # (size, horizon, assets) standard normals -> correlated daily returns.
        shocks = rng.standard_normal((size, horizon_days, n_assets))
        daily = mu + shocks @ cholesky_factor.T
        portfolio_daily = daily @ w
        path_returns[start : start + size] = (
            np.prod(1.0 + portfolio_daily, axis=1) - 1.0
        )

    return path_returns


def run_historical_bootstrap_var(
    returns_df: pd.DataFrame,
    weights: Sequence[float],
    total_value: float,
    n_sims: int = 10_000,
    horizon_days: int = 1,
    confidence_levels: Sequence[float] = (0.95, 0.99),
    seed: int | None = None,
) -> MethodResult:
    """VaR/CVaR by resampling whole historical days with replacement.

    Each simulated day draws one real historical date and takes every ticker's
    return from that date *together*, so the correlation structure that actually
    occurred on that day is preserved.
    """
    path_returns = bootstrap_path_returns(
        returns_df, weights, total_value, n_sims, horizon_days, seed
    )
    return _summarise(
        path_returns, total_value, confidence_levels, HISTORICAL_BOOTSTRAP
    )


def bootstrap_path_returns(
    returns_df: pd.DataFrame,
    weights: Sequence[float],
    total_value: float,
    n_sims: int = 10_000,
    horizon_days: int = 1,
    seed: int | None = None,
) -> np.ndarray:
    """Simulated horizon returns from resampling whole historical days."""
    w = _validate_inputs(returns_df, weights, total_value, n_sims, horizon_days)
    n_assets = returns_df.shape[1]

    history = returns_df.to_numpy(dtype=np.float64)
    n_days = history.shape[0]

    rng = np.random.default_rng(seed)
    block = _block_size(n_sims, horizon_days, n_assets)
    path_returns = np.empty(n_sims, dtype=np.float64)

    for start in range(0, n_sims, block):
        size = min(block, n_sims - start)
        # One row index per simulated day; fancy-indexing keeps each day's
        # cross-section intact -> (size, horizon, assets).
        day_indices = rng.integers(0, n_days, size=(size, horizon_days))
        daily = history[day_indices]
        portfolio_daily = daily @ w
        path_returns[start : start + size] = (
            np.prod(1.0 + portfolio_daily, axis=1) - 1.0
        )

    return path_returns


# ---------------------------------------------------------------------------
# P&L distribution
# ---------------------------------------------------------------------------


def build_shared_distribution(
    parametric_pnl: np.ndarray,
    bootstrap_pnl: np.ndarray,
    n_bins: int = DEFAULT_DISTRIBUTION_BINS,
) -> PnlDistribution:
    """Histogram both methods over ONE set of bin edges.

    The edges span the union of both arrays so the two histograms are directly
    overlayable: a difference in the left tail is a difference in the
    distributions, not an artefact of two different binnings.

    Only the binned counts are returned. The raw per-simulation array is
    deliberately not exposed — at 200,000 simulations it is a payload problem,
    and a chart needs the bins, not the samples.
    """
    if n_bins < 1:
        raise InvalidParameterError(f"n_bins must be at least 1, got {n_bins}")

    combined = np.concatenate([parametric_pnl, bootstrap_pnl])
    edges = np.histogram_bin_edges(combined, bins=n_bins)

    # np.histogram puts the rightmost value in the last bin, and the edges span
    # the union, so every simulated value is counted exactly once per method.
    parametric_counts, _ = np.histogram(parametric_pnl, bins=edges)
    bootstrap_counts, _ = np.histogram(bootstrap_pnl, bins=edges)

    return PnlDistribution(
        n_bins=len(edges) - 1,
        bin_edges=[float(edge) for edge in edges],
        parametric_counts=[int(count) for count in parametric_counts],
        historical_bootstrap_counts=[int(count) for count in bootstrap_counts],
    )


def run_both_methods(
    returns_df: pd.DataFrame,
    weights: Sequence[float],
    total_value: float,
    n_sims: int = 10_000,
    horizon_days: int = 1,
    confidence_levels: Sequence[float] = (0.95, 0.99),
    seed: int | None = None,
    distribution_bins: int = DEFAULT_DISTRIBUTION_BINS,
) -> tuple[MethodResult, MethodResult, PnlDistribution]:
    """Both simulations plus a histogram of their P&L over shared bins.

    Each method is simulated once; the resulting array feeds both its summary
    statistics and its histogram, so the two are guaranteed consistent with
    each other rather than being two independent estimates.
    """
    parametric_returns = parametric_path_returns(
        returns_df, weights, total_value, n_sims, horizon_days, seed
    )
    bootstrap_returns = bootstrap_path_returns(
        returns_df, weights, total_value, n_sims, horizon_days, seed
    )

    parametric = _summarise(
        parametric_returns, total_value, confidence_levels, PARAMETRIC
    )
    bootstrap = _summarise(
        bootstrap_returns, total_value, confidence_levels, HISTORICAL_BOOTSTRAP
    )
    distribution = build_shared_distribution(
        total_value * parametric_returns,
        total_value * bootstrap_returns,
        distribution_bins,
    )
    return parametric, bootstrap, distribution
