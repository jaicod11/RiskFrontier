# RiskFrontier

A portfolio risk and backtesting platform for Indian equities. Build a portfolio from NSE stocks and get two questions answered honestly:

**How bad could a bad day be?** — Monte Carlo simulation of thousands of possible tomorrows, giving Value at Risk and Conditional VaR.

**Would this strategy have actually made money?** — A day-by-day backtest that never sees future data, benchmarked against buy-and-hold and the Nifty 50.

**[Live demo](https://riskfrontier.vercel.app)** · **[API docs](https://riskfrontier.onrender.com/docs)**

> The backend runs on a free tier and sleeps when idle. A cold visit takes ~27s to wake; the UI shows a progress indicator while it does.

---

## What it found

Running a 10-stock large-cap portfolio through the full pipeline:

| | CAGR | Sharpe | Max drawdown |
|---|---|---|---|
| Monthly rebalancing | 7.08% | 0.116 | −36.63% |
| Buy and hold same stocks | 6.14% | 0.067 | −37.06% |
| **Buy and hold Nifty 50** | **9.59%** | **0.253** | −38.44% |

Rebalancing beat holding the same stocks by 0.93pp — and cost ₹8,487 in transaction fees to do it. Both lost to simply buying the index by roughly 2.5pp a year.

Across 61 bootstrapped historical windows, the picture is more nuanced: the strategy beat buy-and-hold on CAGR in 64% of windows and on Sharpe in 67%. But walk-forward re-optimization — refitting Markowitz weights on trailing data at each rebalance — bought only +0.2pp of CAGR for 3pp deeper drawdowns and 4.4× the turnover, beating the simpler strategy on Sharpe in just 36% of windows. Estimation error in expected returns is real and it compounds.

**The most important result is a caveat.** The strategy appears to beat the Nifty 50 in 100% of windows. That number is survivorship bias, not skill: the universe is ten *current* index constituents backtested from 2016, so companies that dropped out of the index over that decade are excluded by construction. The basket was selected on the outcome being measured. No claim about beating the index is supported by this project.

---

## How it works

### 1. Risk measurement — Monte Carlo VaR / CVaR

Estimates volatility and the correlation matrix from historical returns, then generates thousands of simulated future price paths. The worst 5% (or 1%) of those outcomes is your Value at Risk; the average of everything beyond that threshold is your Conditional VaR.

Two independent methods run side by side:

- **Parametric** — Cholesky-decomposes the covariance matrix and draws correlated normal shocks.
- **Historical bootstrap** — resamples *whole historical days* with replacement, so the real cross-sectional correlation structure of each day is preserved intact.

They agree in the middle of the distribution and diverge sharply in the tail. Over a window including the COVID crash, 99% CVaR came out at ₹87,416 parametric versus ₹1,14,708 bootstrap — a 31% gap. That gap is the normality assumption's cost, measured rather than asserted.

Simulation runs vectorized in NumPy: 10,000 paths over 10 days for 8 tickers in 85ms.

### 2. Portfolio optimization — Markowitz efficient frontier

Traces the set of portfolios offering the best return for each level of risk, using `scipy.optimize` (SLSQP) with multiple starting points for robustness. Returns the minimum-variance and maximum-Sharpe portfolios alongside the full frontier.

A per-asset weight cap (default 35%) is applied by design. Unconstrained mean-variance optimization is a well-known error maximizer — historical mean returns are far noisier estimators than the covariance matrix, so an uncapped optimizer piles weight into whichever stock happened to have the best trailing return. Uncapped, the dominant asset takes 80%+ of the portfolio in testing; the cap binds exactly where it should.

### 3. Backtesting — strict no-lookahead simulation

The engine tracks **share counts** as state, not weights. Weights are derived. This makes portfolio drift between rebalances naturally correct instead of something that has to be remembered.

On rebalance dates, the strategy function is called with price history up to and including that day and nothing beyond it, and trades execute at that day's close. Transaction costs (15 bps per leg) are charged only on rebalance days.

Strategies plug in through a `rebalance_fn` seam, so the walk-forward optimizer uses the same engine as the fixed-weight strategy with no special-casing.

Every run reports two baselines: buy-and-hold of the same stocks, and buy-and-hold of the Nifty 50 itself.

---

## Rigor

The parts of this project that took the most work are the parts that make the results trustworthy.

**Lookahead bias is proven absent, not assumed.** The critical test runs a backtest through date *T*, then runs it again with 30 more days of data appended, and asserts every value for every day ≤ *T* is byte-identical between the two runs — share counts, portfolio value, costs, everything. If any future data leaked backward, those runs would differ. A separate spy assertion confirms the optimizer itself never receives a row dated after the rebalance date it's being called for.

**Corporate actions are neutralized before they can corrupt anything.** Scanning all 50 tickers for large single-day moves surfaced two data artifacts masquerading as crashes: TMPV's −40.2% on the 2025 Tata Motors demerger, and TRENT's −33.0% on an unadjusted ~3:2 ratio reset. Neither is a real loss. They're recorded in a `price_anomalies` table as a documented overlay — raw price rows are never modified — and a single mandatory `get_daily_returns()` function applies the correction, so no downstream module can accidentally read raw prices. The four other large moves found by the same scan were verified as genuine (Hindenburg, the COVID bottom, SBIN's recapitalization) by cross-checking market breadth and sector peers, and were left untouched.

**Nothing is reported as a single number.** Results are bootstrapped across every valid rolling window in the data, plus a stationary block bootstrap that resamples blocks of ~21 consecutive days rather than individual days — resampling days independently would destroy volatility clustering and produce falsely narrow confidence intervals. Strategy and both baselines run over identical windows so comparisons are paired.

**Limitations ship with the results.** Fourteen documented caveats live in [`docs/LIMITATIONS.md`](docs/LIMITATIONS.md), and the relevant ones are attached to the API response of every endpoint they qualify — rendered inline in the UI next to the numbers they undercut, never hidden behind an accordion.

Six of the fourteen push in the same direction. Survivorship bias, the dividend asymmetry (constituent prices are total-return while the Nifty 50 benchmark is a price index, handing the strategy the index dividend yield for free every year), and multiple-comparisons selection all flatter the strategy relative to the benchmark. They compound rather than cancel.

**277 tests.** Closed-form checks tie the simulation back to textbook math: single-asset parametric VaR is verified against the analytic z-score formula, CVaR against the analytic expected shortfall, and two-asset minimum-variance weights against the closed-form solution across four correlation regimes. Diversification, cap binding, metric formulas, and error contracts are all covered.

---

## Stack

**Backend** — Python 3.11, FastAPI, SQLAlchemy 2.0, Alembic, pandas, NumPy, SciPy, yfinance
**Frontend** — React, TypeScript, Vite, Tailwind, Recharts
**Data** — PostgreSQL (Neon), ~124K daily price rows across 50 NSE constituents plus the Nifty 50 index, 2016–2026
**Deploy** — Docker on Render (backend), Vercel (frontend), GitHub Actions keep-alive

TypeScript types are generated from the live OpenAPI schema rather than hand-written, so the frontend contract cannot silently drift from the API.

### API

| Endpoint | Purpose |
|---|---|
| `GET /api/securities` | Universe with coverage and date ranges |
| `POST /api/risk/var` | Monte Carlo VaR/CVaR, both methods, with P&L histogram |
| `POST /api/portfolio/optimize` | Efficient frontier, min-variance, max-Sharpe |
| `POST /api/backtest/run` | Single-window backtest vs both baselines |
| `POST /api/backtest/bootstrap` | Bootstrapped results across many windows |

Every error returns the same envelope — `{error_code, message, details}` — with actionable messages naming the offending ticker or value.

### Measured production timings

| Endpoint | Time |
|---|---|
| VaR (10k sims, 10 days) | 0.81s |
| Optimize (30 frontier points) | 12.26s |
| Backtest (8 years) | 3.17s |
| Bootstrap, rolling windows | 16–24s |

Request caps prevent any single call from monopolizing the instance; exceeding one returns a 422 naming the limit and suggesting a cheaper alternative.

---

## Running locally

```bash
git clone https://github.com/jaicod11/RiskFrontier.git
cd RiskFrontier
cp .env.example .env

docker compose up -d                                    # Postgres + backend
docker compose exec backend alembic upgrade head        # schema
docker compose exec backend python -m app.scripts.run_ingestion   # ~5 min

cd frontend && npm install && npm run dev
```

Frontend at `localhost:5173`, API docs at `localhost:8000/docs`.

```bash
docker compose exec backend pytest      # 277 tests
```

Ingestion pulls ~10 years of daily prices for 50 NSE constituents plus `^NSEI` via yfinance. Prices are stored with corporate-action anomalies recorded separately, never overwritten.

---

## Notes

Price data is seeded into production by `pg_dump` restore rather than live ingestion, since yfinance rate-limits datacenter IPs. `run_ingestion --tickers` refreshes it.

Migrations use Neon's direct connection (`DATABASE_URL_UNPOOLED`) because pooled connections don't support the session state Alembic requires; the running app uses the pooled connection.

---

## License

MIT