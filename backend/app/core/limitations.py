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

# --- Universe construction -------------------------------------------------

SURVIVORSHIP_BIAS_WARNING = (
    "The ticker universe is chosen by the user from today's Nifty 50 "
    "constituents. Any backtest starting before today therefore excludes every "
    "company that left the index during the period — the failures, the "
    "delistings, the names that fell out after sustained underperformance — "
    "while including companies selected precisely because they survived to be "
    "in the index now. The basket has been picked using knowledge of the "
    "outcome being measured. Comparisons against the Nifty 50 benchmark are "
    "biased in the strategy's favour as a direct result, and must not be read "
    "as evidence of skill. A strategy beating the index here has demonstrated "
    "nothing except that hand-picked survivors outperformed."
)


BENCHMARK_PRICE_INDEX_WARNING = (
    "The strategy's returns include dividends but the benchmark's do not. "
    "Constituent prices are fetched with dividend and split adjustment, so "
    "holding a stock earns its dividends; ^NSEI is the Nifty 50 *price* index, "
    "which excludes them entirely (the dividend-inclusive Nifty 50 TRI is a "
    "separate series this project does not ingest). The benchmark is therefore "
    "understated by roughly the index dividend yield, compounding over the "
    "whole period — a gap of a percentage point or more per year, in the "
    "strategy's favour, before any strategy decision is made."
)


# --- Inference and selection -----------------------------------------------

MULTIPLE_COMPARISONS_WARNING = (
    "When several strategies or parameter settings are tried and the best "
    "performer is the one reported, its results are biased upward by that "
    "selection alone — some of the apparent edge is the search, not the "
    "strategy. The range shown here does not account for how many variants "
    "were tested before landing on this one, and no correction for that has "
    "been applied."
)

OVERLAPPING_WINDOWS_WARNING = (
    "Rolling windows share most of their underlying data: adjacent windows "
    "differ by only one month out of several years, so they are not "
    "independent samples. The resulting interval therefore understates the "
    "true uncertainty. Read it as a sensitivity range across historical "
    "periods — how much the answer moves when the start date shifts — and not "
    "as a confidence interval in the strict statistical sense."
)


# --- Costs and frictions ---------------------------------------------------

#: For endpoints that produce target weights but simulate no trading.
TRANSACTION_COST_NOT_MODELLED_WARNING = (
    "No transaction costs are modelled here. Moving from a current portfolio to "
    "these target weights would incur brokerage, STT, exchange fees, and "
    "market impact, none of which are reflected in the figures shown. The "
    "more the target differs from what is already held, the larger the gap "
    "between these figures and a realisable result."
)

#: For endpoints that DO simulate trading, at a flat per-leg rate.
TRANSACTION_COST_MODEL_WARNING = (
    "Transaction costs are modelled as a flat 15 basis points per trade leg, "
    "which approximates STT, exchange transaction charges, SEBI turnover fees, "
    "stamp duty and GST for a delivery trade through an Indian discount broker. "
    "It excludes brokerage beyond that, capital gains tax entirely, and any "
    "market impact or slippage beyond the flat rate — a real book pays more "
    "than this, not less, and a high-turnover strategy pays disproportionately "
    "more. Costs also do not scale with order size here, so the figures "
    "flatter large portfolios trading less liquid names."
)


# --- Per-endpoint compositions ---------------------------------------------

#: Monte Carlo VaR / CVaR (`POST /api/risk/var`).
VAR_LIMITATIONS: list[str] = [
    NORMALITY_ASSUMPTION_WARNING,
    CORRELATION_BREAKDOWN_WARNING,
]

#: Backtesting (`POST /api/backtest/run`). Carries the survivorship warning
#: because the response compares the strategy against the Nifty 50.
BACKTEST_LIMITATIONS: list[str] = [
    SURVIVORSHIP_BIAS_WARNING,
    BENCHMARK_PRICE_INDEX_WARNING,
    HISTORICAL_ESTIMATE_WARNING,
    TRANSACTION_COST_MODEL_WARNING,
    CORRELATION_BREAKDOWN_WARNING,
]

#: Bootstrapped backtests (`POST /api/backtest/bootstrap`). Carries the
#: survivorship warning: the win-rate-vs-index figure is its headline number.
BOOTSTRAP_LIMITATIONS: list[str] = [
    SURVIVORSHIP_BIAS_WARNING,
    BENCHMARK_PRICE_INDEX_WARNING,
    OVERLAPPING_WINDOWS_WARNING,
    MULTIPLE_COMPARISONS_WARNING,
    TRANSACTION_COST_MODEL_WARNING,
    CORRELATION_BREAKDOWN_WARNING,
]

#: Markowitz optimisation (`POST /api/portfolio/optimize`).
OPTIMIZER_LIMITATIONS: list[str] = [
    EXPECTED_RETURN_ESTIMATION_WARNING,
    CORRELATION_BREAKDOWN_WARNING,
    TRANSACTION_COST_NOT_MODELLED_WARNING,
]
