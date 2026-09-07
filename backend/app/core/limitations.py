"""Caveats that must travel with the numbers they qualify.

Every analytical endpoint returns its limitations *on the response*. Caveats
kept in the frontend get dropped by the next client, the next export, the next
screenshot; on the response they cannot be separated from the figure they
qualify without deliberately stripping them.

Constants are defined once here and composed into per-endpoint tuples below, so
a caveat shared by several endpoints has exactly one wording. New endpoints
should compose from these rather than inline their own strings.
"""

from __future__ import annotations

# --- Distributional assumptions --------------------------------------------

NORMALITY_ASSUMPTION_WARNING = (
    "The parametric method assumes daily returns are normally distributed. "
    "Real equity returns have fatter tails than the normal distribution, so "
    "this method tends to understate the odds of extreme moves — the very "
    "scenarios VaR is meant to describe."
)

# --- Estimating from history -----------------------------------------------

CORRELATION_BREAKDOWN_WARNING = (
    "Both methods estimate future correlation from a historical window. "
    "Correlations between assets are well known to shift — usually to "
    "tighten — precisely during market stress, which is exactly when this "
    "estimate matters most and is least reliable. Diversification measured "
    "in calm conditions can largely disappear in a crash."
)

HISTORICAL_ESTIMATE_WARNING = (
    "Every figure here is estimated from a single finite historical window and "
    "is a statement about that window, not a forecast. One window is one draw "
    "from history: it shows what did happen over exactly these dates, not how "
    "sensitive that outcome is to the particular start and end chosen. Shifting "
    "the period by a few months can materially change the answer, and nothing "
    "here quantifies that sensitivity."
)

EXPECTED_RETURN_ESTIMATION_WARNING = (
    "Expected returns are estimated from historical means, which are far "
    "noisier estimators than the covariance matrix — mean returns need "
    "decades of data to pin down, while volatility converges in months. "
    "Treat the max-Sharpe portfolio's specific weights with real scepticism: "
    "small changes in estimated returns move them a great deal. The shape of "
    "the frontier is considerably more informative than any single point on it."
)

# --- Costs and frictions ---------------------------------------------------

TRANSACTION_COST_WARNING = (
    "No transaction costs are modelled. Moving from a current portfolio to "
    "these target weights would incur brokerage, STT, exchange fees, and "
    "market impact, none of which are reflected in the returns shown. The "
    "more the target differs from what is already held, the larger the gap "
    "between these figures and a realisable result."
)


# --- Per-endpoint compositions ---------------------------------------------

#: Monte Carlo VaR / CVaR (`POST /api/risk/var`).
VAR_LIMITATIONS: list[str] = [
    NORMALITY_ASSUMPTION_WARNING,
    CORRELATION_BREAKDOWN_WARNING,
]

#: Backtesting (`POST /api/backtest/run`).
BACKTEST_LIMITATIONS: list[str] = [
    HISTORICAL_ESTIMATE_WARNING,
    TRANSACTION_COST_WARNING,
    CORRELATION_BREAKDOWN_WARNING,
]

#: Markowitz optimisation (`POST /api/portfolio/optimize`).
OPTIMIZER_LIMITATIONS: list[str] = [
    EXPECTED_RETURN_ESTIMATION_WARNING,
    CORRELATION_BREAKDOWN_WARNING,
    TRANSACTION_COST_WARNING,
]
