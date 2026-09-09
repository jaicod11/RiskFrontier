# Limitations

Every caveat this project has accumulated, in one place, with the number that
demonstrates it where one exists.

This is not a disclaimer. Each entry below changes how a result should be read,
and several of them are large enough to reverse a conclusion. The API returns
the relevant subset on every analytical response (see
[`backend/app/core/limitations.py`](../backend/app/core/limitations.py)); this
document is the long form.

---

## The short version

| # | Limitation | Demonstrated by | Direction of bias |
|---|---|---|---|
| 1 | Survivorship bias in universe selection | 100% win rate vs index across 61 and 37 windows | **Favours the strategy** |
| 2 | Benchmark excludes dividends, strategy includes them | ITC 2024: +5.37% adjusted vs +1.91% unadjusted | **Favours the strategy** |
| 3 | Multiple comparisons | 2 strategies × 2 objectives × 2 methods tested | **Favours the strategy** |
| 4 | Overlapping windows aren't independent | 61 windows; adjacent pairs share 59/60 months | Understates uncertainty |
| 5 | Normality understates tails | 99% CVaR: ₹87,416 parametric vs ₹114,708 bootstrap | Understates risk |
| 6 | Correlations tighten under stress | 2020-03-23: 42 of 47 constituents fell >5% | Understates risk |
| 7 | Expected returns are noisy estimates | Walk-forward: +0.2pp CAGR, better Sharpe in 36% of windows | Overstates optimiser value |
| 8 | Transaction costs are a flat 15 bps/leg | ₹9,984 vs ₹1,498 over 8 years (1.00% vs 0.15%) | **Favours the strategy** |
| 9 | Corporate actions zeroed, not reconstructed | TMPV −40.15%, TRENT −33.05% set to 0 | Small, direction unclear |
| 10 | Dividends modelled only implicitly | ITC 2024: 3.46pp of return arrives via adjustment | Slightly favours the strategy |
| 11 | Fractional shares assumed | Day-one holding of 390.1327 shares | Slightly favours the strategy |
| 12 | Single data vendor, no cross-check | 98.1% of expected trading days present | Unknown |
| 13 | Close-only execution | — | Slightly favours the strategy |
| 14 | Single historical window per backtest | — | Understates uncertainty |

**Five of these push in the same direction.** Items 1, 2, 3, 8, 10, 11 and 13 all
flatter the strategy relative to the benchmark. They are not independent
nuisances to be mentally netted out — they compound.

---

## 1. Survivorship bias in universe selection

**What it is.** The ticker universe is chosen from the Nifty 50 constituents as
of today. A backtest starting in 2016 therefore runs on companies selected in
2026 for having survived to be in the index now.

**Why it exists.** `nifty50_seed.json` is a snapshot of current membership. The
project does not ingest historical index composition, so there is no way to
reconstruct what the index held in 2016, or which names were dropped along the
way after sustained underperformance.

**What it means.** The headline result — *both strategies beat the Nifty 50 in
100% of windows* (61 windows for constant-mix, 37 for walk-forward) — is very
largely an artefact of this. A basket picked for having survived a decade will
beat the index that dropped its failures, regardless of what the strategy does
on top. A 100% win rate is a red flag, not a result: genuine edges do not work
in every single period. **Nothing in this project demonstrates that either
strategy beats the index.**

Note that the bias attaches to the *universe*, not the strategy. Comparisons
between the strategies (constant-mix vs walk-forward vs hold) are run on the
same survivor basket and are much less affected.

**What would fix it.** Ingest historical Nifty 50 membership with effective
dates, and rebuild the universe as of each backtest's start date rather than
today. NSE publishes index reconstitution announcements; a maintained
point-in-time constituent table is the real requirement. Delisted and removed
tickers also need their price history retained, which yfinance often will not
serve.

---

## 2. The benchmark excludes dividends; the strategy includes them

**What it is.** Constituent prices are fetched with `auto_adjust=True`, so they
are total-return series: holding a stock earns its dividends. `^NSEI` is the
Nifty 50 **price** index, which excludes dividends entirely.

**Why it exists.** Yahoo Finance serves `^NSEI` as the price index. The
dividend-inclusive Nifty 50 TRI is a separate series this project does not
ingest.

**What it means.** Every strategy-vs-benchmark comparison is tilted before any
strategy decision is made. The size is concrete: ITC returned **+5.37% in 2024
on the adjusted series but +1.91% unadjusted** — a 3.46pp gap in one year for
one high-yield name. At an index dividend yield of roughly 1.2–1.5%, the
benchmark is understated by that much per year, compounding. Over a five-year
window that is several percentage points of cumulative return handed to the
strategy for free.

Combined with item 1, the observed median gap of 22.15% vs 14.79% CAGR should
not be read as a 7.4pp edge. A meaningful share of it is these two artefacts.

**What would fix it.** Ingest the Nifty 50 TRI and use it as the benchmark, or
strip dividends from constituent returns so both sides are price-only.
Total-return on both sides is the better fix, since that is what an investor
actually experiences.

---

## 3. Multiple comparisons

**What it is.** Several strategies and configurations were run, compared, and
the results reported together. Two strategies (constant-mix, walk-forward), two
optimiser objectives (max-Sharpe, min-variance), and two bootstrap methods were
each tried.

**Why it exists.** It is inherent to the exercise. Exploring alternatives is the
point; the bias comes from reporting the winner without accounting for the
search.

**What it means.** Any "best" result carries selection bias. If ten strategies
are tested on the same data, the best of them looks good partly because it is
the best of ten. The percentile ranges reported by the bootstrap endpoint
describe the spread *for one strategy across windows* — they contain no
correction for how many strategies were tried before that one was chosen.

**What would fix it.** Hold out data never used during exploration, and test the
finally-chosen strategy on it once. Alternatively apply an explicit correction
(White's Reality Check, Hansen's SPA test) that accounts for the number of
candidates. Neither is implemented here.

---

## 4. Overlapping windows are not independent samples

**What it is.** The rolling-window bootstrap steps the start date forward one
month at a time. Adjacent five-year windows share 59 of their 60 months.

**Why it exists.** Ten years of data contains only two non-overlapping five-year
windows. Overlapping them is the only way to get a usable number of
observations — 61 instead of 2.

**What it means.** The 5th–95th percentile range is **not a confidence
interval**. With 61 windows drawn from ~10 years, the effective sample size is
closer to 2 than to 61. The interval understates true uncertainty, possibly by a
lot. Read it as a sensitivity range — *how much does the answer move when the
start date shifts* — and not as a statistical statement about the population of
possible outcomes.

**What would fix it.** More history (the constituent series start in 2016, the
binding constraint), or non-overlapping windows accepting the tiny sample, or an
inference method that accounts for the overlap explicitly. The stationary block
bootstrap is a partial answer and is implemented, but it resamples from the same
decade and cannot manufacture independent history.

---

## 5. The normality assumption understates tail risk

**What it is.** `run_parametric_var` fits a multivariate normal to the return
window. Real equity returns have fatter tails than a normal distribution.

**Why it exists.** The normal is analytically convenient and needs less data,
which is why it is the textbook default. It is offered alongside the historical
bootstrap precisely so the difference is visible.

**What it means.** Over a window including the COVID crash, for the same
portfolio and horizon:

| | 99% VaR | 99% CVaR | Worst simulated path |
|---|---|---|---|
| Parametric | ₹75,951 | ₹87,416 | −₹153,751 |
| Historical bootstrap | ₹91,217 | **₹114,708** | −₹224,514 |
| Difference | +20.1% | **+31.2%** | +46.0% |

The parametric method understates the 99% expected shortfall by nearly a third
in a period that actually contained a crash. Over the calm 2024–26 window the
two agree to within 1% — which is exactly the problem: the assumption looks
harmless right up until it matters.

**What it means in practice:** treat the bootstrap figure as the operative one
for tail risk, and the parametric figure as a smooth reference.

**What would fix it.** Fit a fat-tailed distribution (Student-t, or a skewed-t),
or use extreme value theory for the tail specifically, or rely on the
historical bootstrap and accept that it can only replay observed history.

---

## 6. Correlations tighten during stress

**What it is.** Both VaR methods and the optimiser estimate the covariance
matrix from a historical window. Correlations between assets are not stable —
they rise sharply in crashes, exactly when the estimate matters most.

**Why it exists.** There is no way to estimate a forward-looking correlation
matrix from historical data without assuming some stability. Diversification
benefit measured in calm conditions is measured under the wrong regime.

**What it means.** On **2020-03-23, 42 of 47 index constituents fell more than
5% on the same day, with the average constituent down 13.0%.** Whatever
diversification the covariance matrix showed in January 2020 was not available
in March. A portfolio optimised for the calm regime concentrated its risk into a
single factor — "Indian equities" — that revealed itself all at once.

Both the VaR estimates and the Markowitz frontier inherit this. The efficient
frontier's shape is a statement about the estimation window's correlation
structure, not about the correlation structure that will hold during the next
drawdown.

**What would fix it.** Stress-test with correlations forced toward 1, use a
regime-switching or DCC-GARCH model, or shrink the covariance matrix toward a
structured target (Ledoit–Wolf). None are implemented.

---

## 7. Expected returns are far noisier than covariances

**What it is.** Markowitz optimisation needs both an expected-return vector and
a covariance matrix. The returns are estimated from historical means, which are
dramatically worse estimators — means need decades of data to pin down, while
volatility converges in months.

**Why it exists.** Historical mean is the simplest available estimator, and this
project does not incorporate forward-looking views.

**What it means.** This is measurable here. Walk-forward re-optimisation
re-estimates expected returns monthly and rebalances to the new max-Sharpe
portfolio. Against a fixed equal-weight mix over the 36 windows both ran:

| | CAGR | Sharpe | Sortino | Max drawdown | Turnover/rebalance |
|---|---|---|---|---|---|
| constant-mix | 22.15% | **0.930** | **1.282** | **−22.14%** | 6.1% |
| walk-forward | **22.35%** | 0.901 | 1.282 | −25.14% | 27.1% |

Re-optimising bought **+0.20pp of CAGR** while delivering a **better Sharpe in
only 36% of windows**, **3pp deeper median drawdowns**, and **4.4× the
turnover**. The optimiser chased estimation noise and paid for the privilege.

**What it means for the max-Sharpe portfolio specifically:** treat its exact
weights with real scepticism. Small changes in estimated returns move them a
great deal. The *shape* of the efficient frontier is considerably more
informative than any single point on it.

**What would fix it.** Shrink expected returns toward a prior (James–Stein,
Black–Litterman), optimise for minimum variance only (which needs no return
estimates and, on this evidence, did better), or impose tighter weight
constraints. The 35% cap is already a crude version of this.

---

## 8. Transaction costs are a flat 15 bps per leg

**What it is.** The backtester charges 15 basis points on the traded notional of
each rebalance. This approximates STT, exchange transaction charges, SEBI
turnover fees, stamp duty and GST for a delivery trade through an Indian
discount broker.

**What it excludes:** brokerage beyond that flat rate, **capital gains tax
entirely**, market impact, and any slippage beyond the flat rate. Cost does not
scale with order size, so the model flatters large portfolios and less liquid
names.

**Why it exists.** A realistic cost model needs order-book depth and a
participation-rate assumption, neither of which this project has.

**What it means.** The drag is real and visible: over an eight-year backtest,
monthly rebalancing paid **₹9,984 against buy-and-hold's ₹1,498** — 1.00% of
starting capital versus 0.15%. And that is the *understated* figure. Capital
gains tax alone would be material for a strategy realising gains monthly: Indian
STCG on equities applies to positions held under a year, which describes most of
what a monthly rebalancer does.

The higher a strategy's turnover, the more this understatement flatters it.
Walk-forward, at 27.1% turnover per rebalance, is understated roughly 4.4× more
than constant-mix.

Note that this applies to the **backtester**, which does simulate trading. The
Markowitz optimiser models no transaction costs at all — it produces target
weights without simulating the trades that would reach them, so the cost of
moving from a current book to a proposed one is entirely absent from the
efficient frontier. Two portfolios with identical frontier positions can differ
enormously in what it costs to get to them.

**What would fix it.** Model brokerage explicitly, add a capital-gains ledger
tracking holding periods per lot, and make slippage a function of order size
relative to average daily volume (which is already ingested and unused). For the
optimiser, add a turnover penalty relative to a stated current portfolio.

---

## 9. Corporate actions are zeroed, not economically reconstructed

**What it is.** Two dates carry price breaks that are not returns: TMPV on
2025-10-14 (raw **−40.15%**, the Tata Motors demerger) and TRENT on 2026-01-01
(raw **−33.05%**, an unadjusted corporate action). Both are registered in
`price_anomalies` and their returns are forced to **0.0**.

**Why it exists.** The economically correct treatment of a demerger is to credit
the holder with shares in the spun-off entity and track both. The spun-off
entity is not in this dataset, so there is nothing to credit. Zero is the
closest available approximation: it says "the holder's wealth did not change by
−40% that day", which is true, rather than leaving a fabricated crash in place.

**What it means.** A small approximation error remains. The holder's wealth on
the demerger date did not change by −40%, but neither did it change by exactly
0% — the two entities' combined opening value differed slightly from the
predecessor's close. The residual is a fraction of a percent against a 40%
error, so the correction is overwhelmingly the right call, but it is an
approximation and not a reconstruction.

Zero was chosen over `NaN` deliberately, so the return series stays dense and
aligned across securities: the day contributes nothing to a cumulative product
and nothing to a mean, without punching a hole in the index that would silently
drop other tickers from a covariance calculation.

**What would fix it.** Ingest the spun-off entity (TMLCV for the Tata Motors
demerger), and on the action date compute the holder's true combined value
across both lines. This requires a corporate-actions feed with entitlement
ratios, which yfinance does not provide.

---

## 10. Dividends are modelled only implicitly

**What it is.** Dividends enter the simulation solely through `auto_adjust=True`
on the price fetch, which back-adjusts historical prices so that a dividend
appears as a small uplift in the adjusted series. No dividend is ever tracked as
a cash flow.

**Why it exists.** Adjusted close is the standard convention for total-return
backtesting and requires no separate corporate-actions feed. It is a good
approximation, not an exact one.

**What it means.** The adjustment assumes each dividend is **reinvested
immediately at the closing price on the ex-date, in fractional shares, tax-free**.
Reality differs on all three counts: the cash arrives days later, it sits
uninvested in the interim, it buys whole shares, and in India dividends are
taxable in the investor's hands at their slab rate. The magnitude is not
negligible — for ITC in 2024, **3.46pp of the +5.37% total return arrived via
this adjustment** rather than as tracked cash.

The direction is mildly optimistic: immediate tax-free reinvestment is the
best-case treatment.

A second consequence is that the strategy's dividend income has no counterpart
on the benchmark side, which is item 2.

**What would fix it.** Ingest the dividend schedule (yfinance exposes it), credit
cash on the ex-date, apply a reinvestment lag and dividend tax, and reinvest at
the next rebalance rather than instantly. The engine already tracks a cash
balance, so the accounting has somewhere to go.

---

## 11. Fractional shares, no lot sizes

**What it is.** The engine tracks continuous share counts. A day-one purchase
produces holdings like **390.1327 shares**.

**Why it exists.** Continuous shares make the simulation exact and keep weights
achievable. Rounding to whole shares introduces a residual that must go
somewhere.

**What it means.** Slightly optimistic. Real NSE delivery trades are in whole
shares, so a real portfolio cannot hit its target weights exactly and carries
uninvested cash. For a ₹10,00,000 portfolio across ten large caps the effect is
small — a few hundred rupees of drift — but it grows as capital shrinks, and for
a ₹50,000 portfolio it would be material.

**What would fix it.** Round target share counts down to whole shares (or to the
instrument's lot size) and carry the residual as cash. The engine already tracks
a cash balance, so this is a contained change.

---

## 12. Single data vendor, no cross-validation

**What it is.** All prices come from Yahoo Finance via `yfinance`. Nothing checks
them against a second source.

**Why it exists.** It is free and covers NSE. Licensed vendors are not.

**What it means.** Vendor errors are invisible. The ingest reports **98.1% of
expected trading days** present (2,473 bars against a 252-day/year expectation),
with the shortfall consistent with NSE holidays — but "consistent with" is not
"verified against an exchange calendar". Two corporate actions were found only
because a large-move scan flagged them; a smaller unadjusted action would pass
unnoticed. Yahoo also silently revises history.

The pipeline is defensive about this — the anomaly registry, the >25% move scan,
the coverage report — but detection is heuristic and tuned to catch large errors.

**What would fix it.** Cross-check against a second source (NSE bhavcopy files
are free and authoritative for Indian equities), validate the trading calendar
against the official NSE holiday list, and snapshot ingested data so vendor
revisions are detectable.

---

## 13. Close-only execution

**What it is.** All trades execute at the day's closing price. There is no
intraday modelling, no bid-ask spread beyond the flat cost, and no assumption
about *when* during the day an order is filled.

**Why it exists.** The data is daily OHLCV. Anything finer would be invented.

**What it means.** Mildly optimistic. Executing a full rebalance at the exact
closing print is not achievable in practice — the close is an auction, and a
large order participating in it moves it. For low-turnover strategies on liquid
large caps this is minor; for the 27%-turnover walk-forward strategy it is less
so.

**What would fix it.** Intraday data and an execution model (VWAP or
participation-rate), or at minimum a penalty proportional to order size versus
average daily volume.

---

## 14. Each backtest is a single historical window

**What it is.** `POST /api/backtest/run` reports one path through one period. It
shows what did happen over exactly those dates.

**Why it exists.** It is what a backtest is.

**What it means.** A single window says nothing about how sensitive the result
is to the dates chosen. Shifting the start by a few months can change the answer
materially. This is precisely why `POST /api/backtest/bootstrap` exists — but
that endpoint carries its own limitation (item 4), so the two should be read
together rather than either alone.

**What would fix it.** Always read the single backtest alongside the bootstrap
distribution. Never quote a single-window CAGR as the expected performance of a
strategy.

---

## What this project would need to make a real claim

Ordered by how much each would change the conclusions:

1. **Point-in-time index membership** (item 1) — without it, no comparison
   against the index means anything.
2. **The Nifty 50 TRI as benchmark** (item 2) — a like-for-like comparison.
3. **A capital gains and brokerage ledger** (item 8) — the cost gap between
   high- and low-turnover strategies is currently understated.
4. **Held-out data never used for exploration** (item 3).
5. **More history** (item 4) — the 2016 start is the binding constraint on every
   distributional claim.

Until at least the first two exist, the honest summary of this project's results
is: *constant-mix rebalancing was modestly better risk-adjusted than
walk-forward re-optimisation on a survivor-biased basket of Indian large caps,
and no claim about beating the index is supported.*
