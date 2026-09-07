# Portfolio Risk & Backtesting Tool (NSE)

A portfolio risk and backtesting workbench for **NSE (India) listed equities**.

> **Status: scaffold.** This repository currently contains project structure,
> configuration, database schema and a working health check only. None of the
> three analytical components below are implemented yet.

---

## What this project will do

### 1. Monte Carlo VaR / CVaR

Estimate the downside risk of a portfolio by simulating many thousands of
forward price paths from the historical return distribution of its holdings.

- **VaR (Value at Risk)** — the loss threshold that is not expected to be
  exceeded at a given confidence level over a given horizon. "With 95%
  confidence, this portfolio will not lose more than ₹X over 10 trading days."
- **CVaR (Conditional VaR, a.k.a. Expected Shortfall)** — the *average* loss in
  the tail beyond VaR. This answers the question VaR does not: when the bad case
  happens, how bad is it on average?

Simulation draws on the covariance structure between holdings, so
diversification (or its absence) is reflected in the result.

### 2. Markowitz optimisation

Mean-variance portfolio optimisation. Given a candidate universe of NSE tickers
and their historical returns and covariances, compute:

- the **efficient frontier** — the set of portfolios with the highest expected
  return for each level of risk,
- the **minimum-variance portfolio**,
- the **maximum-Sharpe (tangency) portfolio**, relative to a configurable
  risk-free rate.

Output is a set of target weights per ticker, subject to constraints
(long-only, position caps, and so on).

### 3. Backtesting vs. buy-and-hold

Replay a strategy over historical NSE data and compare it against the honest
benchmark: buying the same basket on day one and doing nothing.

Reported side by side — cumulative and annualised return, volatility, Sharpe
ratio, maximum drawdown, and turnover — so it is clear whether the strategy
actually beat holding, net of rebalancing.

---

## Architecture

```
backend/    FastAPI + SQLAlchemy 2.0 + Alembic, talking to Postgres 16
frontend/   Vite + React + TypeScript + Tailwind, react-router
```

Price history is sourced from Yahoo Finance via `yfinance` (NSE tickers carry a
`.NS` suffix, e.g. `RELIANCE.NS`) and cached in Postgres so that repeated
simulations do not re-hit the network.

### Database schema

| `securities` | |
|---|---|
| `id` | PK |
| `ticker` | unique, indexed (e.g. `RELIANCE`) |
| `name` | company name |
| `exchange` | defaults to `NSE` |
| `sector` | nullable |
| `is_active` | defaults to `true` |
| `is_benchmark` | defaults to `false`; `true` for `^NSEI` |

| `daily_prices` | |
|---|---|
| `id` | PK |
| `security_id` | FK → `securities.id`, cascade delete |
| `date` | trading date |
| `open` / `high` / `low` / `close` / `adj_close` | `NUMERIC(18,4)` |
| `volume` | `BIGINT` |

`daily_prices` has a **unique constraint on `(security_id, date)`** so that
re-running an ingest is idempotent.

| `price_anomalies` | |
|---|---|
| `id` | PK |
| `security_id` | FK → `securities.id`, cascade delete |
| `date` | the affected trading date |
| `description` | why this move is not a real return |
| `adjustment` | enum — `exclude_return` |

Also unique on `(security_id, date)`. This table is an **overlay**: it records
corrections without touching the raw price rows. See
[Returns](#returns-get_daily_returns-is-the-mandatory-entry-point).

---

## Data ingestion

The `securities` and `daily_prices` tables are populated from a seed list of the
50 Nifty constituents (`backend/app/data/nifty50_seed.json`) plus daily OHLCV
history pulled from Yahoo Finance.

```bash
# Seed the universe and ingest 10 years of history (default)
docker compose exec backend python -m app.scripts.run_ingestion

# Shorter window
docker compose exec backend python -m app.scripts.run_ingestion --years 5
```

Both steps are **idempotent**. `securities` is upserted on `ticker`, and
`daily_prices` is upserted on the `(security_id, date)` unique constraint, so
re-running refreshes existing bars instead of duplicating them.

### Ticker convention

`securities.ticker` holds the bare NSE symbol (`RELIANCE`, `M&M`,
`BAJAJ-AUTO`). The Yahoo Finance symbol is that plus `.NS`
(`settings.yfinance_suffix`); the seed file stores the Yahoo form under
`yf_ticker`. The read endpoints accept either form.

### Adjusted prices

yfinance is called with `auto_adjust=True`, so the OHLC it returns is already
adjusted for splits and dividends — `Close` *is* the adjusted close. It is
written to both `close` and `adj_close` so downstream analytics can read
`adj_close` uniformly without knowing how a row was fetched.

### Short history is expected for some tickers

Not every constituent has ten years of data, and that is correct rather than a
fetch failure:

| Ticker | Why its history is short |
|---|---|
| `TMPV` | Tata Motors Passenger Vehicles began trading under this identity in **October 2025** following the Tata Motors demerger |
| `ETERNAL`, `INDIGO`, `JIOFIN`, `MAXHEALTH`, `TRENT` | Recent Nifty 50 inclusions that may have listed later than longer-standing members |

These are listed in `KNOWN_SHORT_HISTORY` in
`backend/app/services/data_ingestion.py` and are flagged in the coverage report
so a short series never reads as a broken fetch. Any *other* ticker coming back
with under half the requested window is reported separately, under
"Unexpectedly short history".

### Returns: `get_daily_returns` is the mandatory entry point

> **Every phase that needs returns — Monte Carlo VaR/CVaR, Markowitz
> optimisation, the backtester — must call
> `app.services.returns.get_daily_returns`. Never read `daily_prices` and
> compute percentage changes directly.**

```python
from app.services.returns import get_daily_returns

returns = get_daily_returns(db, "RELIANCE", start, end)  # pd.Series, DatetimeIndex
```

**Why the rule exists.** Some raw price changes are not returns. A demerger or
an unadjusted corporate action moves the printed price without moving the value
of a held position:

| Ticker | Date | Raw move | What actually happened |
|---|---|---|---|
| `TMPV` | 2025-10-14 | −40.2% | Tata Motors demerger. Yahoo carries the pre-demerger parent's history forward under `TMPV.NS`, so spinning off the commercial-vehicles business looks like a 40% loss |
| `TRENT` | 2026-01-01 | −33.0% | Price level resets by a ~3:2 ratio on *below*-average volume with no split recorded by Yahoo — a corporate action, not a selloff |

Neither cost a holder anything. Fed into a covariance matrix they inflate
volatility, distort correlations, and hand the backtester a crash that never
happened. `yfinance`'s `auto_adjust` cannot fix them because Yahoo records no
split for either event.

`get_daily_returns` applies the correction centrally, so no later phase has to
remember these cases. **Code that bypasses it silently reintroduces every
artefact the registry was built to remove.**

### The `price_anomalies` registry

Corrections live in a table, not in the price rows:

```
price_anomalies: id, security_id (FK), date, description, adjustment
```

`adjustment` is an enum, currently just `exclude_return` — the return on that
date is treated as zero rather than used, because the raw price change reflects
no actual gain or loss.

**Raw `daily_prices` rows are never modified.** The registry is a documented
overlay, so the original source stays inspectable and every correction stays
auditable. A test asserts the source rows are byte-identical before and after a
return calculation, and that the −40.2% break is still visible in the raw data.

Zero is used rather than `NaN` so the series stays dense and aligned across
securities: the day contributes nothing to a cumulative product and nothing to a
mean move, without punching a hole in the index.

Registering a newly discovered anomaly:

```python
# in app/services/anomalies.py
KNOWN_PRICE_ANOMALIES = [
    {"ticker": "FOO", "date": date(2026, 3, 1),
     "description": "why this is not a real move",
     "adjustment": AnomalyAdjustment.EXCLUDE_RETURN},
]
```

```bash
# Re-seed without re-fetching any prices
docker compose exec backend python -m app.scripts.run_ingestion --skip-prices
```

### Anomaly scanning

Every ingest run scans for daily moves above 25% and cross-checks them against
the registry:

- **known, handled** — already registered and neutralised by `get_daily_returns`
- **unhandled, needs review** — a genuinely new large move

An unhandled hit is not automatically wrong. The six currently flagged are all
real market events that *should* stay in the data — the Adani/Hindenburg
selloff, the 2020-03-23 COVID low, SBIN's recapitalisation. The scan asks a
human to classify; it never excludes anything on its own.

### Resilience

Each yfinance call is retried 3 times with exponential backoff. If all attempts
fail the ticker is logged with a clear warning and the run moves on to the next
one — a single bad ticker never aborts the ingest. Failures are listed at the
end of the coverage report.

### Verification endpoints

Temporary read-only endpoints, for confirming what landed:

```bash
# All 50 securities with row counts and date ranges
curl -s localhost:8000/api/securities | jq '.[0]'

# Daily bars for one ticker (most recent 1000 when no range is given)
curl -s "localhost:8000/api/securities/RELIANCE/prices?start=2024-01-01&end=2024-03-31" | jq '.count'
```

---

## Monte Carlo VaR / CVaR

`POST /api/risk/var` runs two independent simulations over the same return
window and returns them side by side, so any difference between them is the
distributional assumption rather than the data.

```bash
curl -s -X POST localhost:8000/api/risk/var -H 'Content-Type: application/json' -d '{
  "portfolio": {
    "positions": [
      {"ticker": "RELIANCE", "weight": 0.25},
      {"ticker": "HDFCBANK", "weight": 0.25},
      {"ticker": "INFY",     "weight": 0.25},
      {"ticker": "ITC",      "weight": 0.25}
    ],
    "total_value": 1000000
  },
  "n_sims": 10000, "horizon_days": 10, "seed": 42
}'
```

### The two methods

| | Assumption | Strength | Weakness |
|---|---|---|---|
| **Parametric** | Returns are multivariate normal. Mean vector + covariance matrix, correlated shocks via a Cholesky factor | Smooth, needs less data | Normal tails are thinner than real equity tails |
| **Historical bootstrap** | None. Resamples **whole historical days** with replacement | Keeps the correlation that actually occurred, including days when everything fell together | Can only replay what is in the window |

The bootstrap resamples *days*, not tickers. Each simulated day picks one real
date and takes every ticker's realised return from that date **together** —
resampling each ticker independently would destroy the cross-sectional
correlation that makes a crash a crash.

Both compound day by day inside each path. There is no √T scaling, which would
assume returns are i.i.d. across the horizon.

Why both are worth running — the same portfolio, over a window that includes
the COVID crash:

```
                 VaR 95%   CVaR 95%    VaR 99%   CVaR 99%
Parametric        53,549     67,558     75,951     87,416
Bootstrap         52,883     75,884     91,217    114,708
                            (+12.3%)   (+20.1%)   (+31.2%)
```

The two agree in the middle of the distribution and diverge sharply in the
tail. That gap *is* the normality assumption, measured.

### Returns come from the shared cleaning layer

Every ticker's series is pulled through
[`get_daily_returns`](#returns-get_daily_returns-is-the-mandatory-entry-point),
so the TMPV and TRENT artefacts never reach the covariance matrix.
`build_returns_matrix` inner-joins the columns, keeping only days on which every
requested ticker traded.

### The window is reported, not assumed

The default lookback is 504 trading days (~2 years). If any selected ticker is
younger than that, the window **shrinks to what all of them share** — the ticker
is never dropped and the request never fails. The response says so explicitly:

```json
"data_window": {
  "start": "2023-08-22", "end": "2026-09-07",
  "trading_days": 755, "requested_lookback_days": 2000,
  "shrunk": true, "constrained_by": "JIOFIN",
  "constraint_reason": "JIOFIN has price history only from 2023-08-21, so the
     overlapping window across all 3 tickers is 755 trading days rather than
     the 2000 requested"
}
```

### Limitations ship with the numbers

The response carries a `limitations` field with two fixed strings: one on the
parametric method understating extreme moves, one on correlations tightening
under stress exactly when the estimate matters most.

**This comes from the backend, by design.** Caveats held in the frontend get
dropped by the next client, the next export, the next screenshot. On the
response they travel with the numbers they qualify.

### Performance

10,000 simulations over a 10-day horizon and 8 tickers: **~85 ms**. 100,000
simulations over 60 days: ~3.2 s. Both methods are fully vectorised in numpy —
work is chunked only to bound peak memory, never per simulation.

---

## Markowitz optimisation

`POST /api/portfolio/optimize` takes a candidate universe — **no weights**, the
optimiser determines those — and traces the efficient frontier.

```bash
curl -s -X POST localhost:8000/api/portfolio/optimize \
  -H 'Content-Type: application/json' -d '{
  "tickers": ["RELIANCE","TCS","HDFCBANK","INFY","ITC","SUNPHARMA","MARUTI","NTPC"],
  "n_frontier_points": 30
}'
```

### Estimation

Expected returns are the mean daily return × 252; the covariance matrix is the
daily covariance × 252. Both scale linearly with time.

**`risk_free_rate` defaults to 0.065.** India's 10-year G-Sec yields about 6.95%
as of September 2026, but a Sharpe ratio wants the return on a *short-tenor*
risk-free asset — the 10-year carries duration risk the Sharpe denominator does
not account for. 6.5% is a rounder, slightly conservative stand-in for the short
end. Override it per request.

**`max_weight_per_asset` defaults to 0.35.** Concentration is the classic
failure mode of naive mean-variance optimisation: it will happily put everything
into whichever asset had the best realised mean. Long-only by default;
`allow_short: true` permits weights down to `-max_weight_per_asset`.

> A cap below `1/n` is infeasible — 2 tickers at the default 0.35 can sum to at
> most 0.70. The API returns 422 naming the minimum workable cap rather than
> failing inside the solver.

### The three optimisations

All use `scipy.optimize.minimize` with SLSQP, subject to weights summing to 1
and `0 ≤ w ≤ max_weight_per_asset`:

- **`min_variance_portfolio()`** — minimise variance
- **`max_sharpe_portfolio()`** — maximise `(return − rf) / volatility`
- **`efficient_frontier(n_points=30)`** — minimise variance subject to
  `return ≥ target`, for targets spanning the min-variance return upward

**Robustness.** SLSQP is a local method, so every optimisation runs from three
starting points — equal weight plus two random feasible vectors — keeping the
best converged result. A frontier target the solver cannot reach is logged and
skipped; the rest of the frontier is still returned, and the response reports
`frontier_points_requested`, `frontier_points_returned` and
`skipped_target_returns`.

The frontier's upper endpoint is the highest return the *constraints* allow,
computed exactly (box + budget constraints make this a greedy linear program).
With `max_weight_per_asset = 1.0` that equals the best single asset's return —
the uncapped intuition — but under a binding cap that return simply is not
reachable, and targeting it would only manufacture points the solver must reject.

### What a frontier looks like

Ten large caps, 2000-day window, 35% cap:

```
    vol    return   sharpe   top holdings
  15.32%   13.35%    0.447   ITC 19%, TCS 19%, SUNPHARMA 15%     <- min variance
  16.34%   19.18%    0.776   SUNPHARMA 19%, TITAN 19%, BHARTIARTL 19%
  18.73%   23.55%    0.910   TITAN 33%, BHARTIARTL 32%, SUNPHARMA 20%  <- max Sharpe
  19.97%   23.92%    0.872   TITAN 35%, BHARTIARTL 35%, NTPC 30%  <- cap binding
```

Sharpe climbs to the tangency portfolio and falls past it; the cap binds at the
high-return end, which is exactly what stops the optimiser concentrating further.

### Limitations

`core/limitations.py` defines each caveat once and composes them per endpoint,
so wording shared between VaR and the optimiser cannot drift apart. The
optimiser returns three:

- **Expected returns are noisy.** Historical means are far worse estimators than
  covariance — means need decades to pin down, volatility converges in months.
  Treat the max-Sharpe portfolio's specific weights with real scepticism; the
  frontier's *shape* is much more informative than any single point on it.
- **Correlations shift under stress** (shared wording with the VaR endpoint).
- **No transaction costs are modelled.** Moving to these targets incurs
  brokerage, STT, fees and market impact. The further the target is from what is
  already held, the larger the gap from a realisable result.

---

## Backtesting

`POST /api/backtest/run` simulates a strategy day by day and runs two baselines
over the same window, with the same metric formulas.

```bash
curl -s -X POST localhost:8000/api/backtest/run -H 'Content-Type: application/json' -d '{
  "tickers": ["RELIANCE","TCS","HDFCBANK","ITC"],
  "target_weights": {"RELIANCE":0.25,"TCS":0.25,"HDFCBANK":0.25,"ITC":0.25},
  "start_date": "2018-09-10", "end_date": "2026-09-07",
  "initial_capital": 1000000, "rebalance_frequency": "monthly"
}'
```

### Design commitments

**Shares are the state; weights are derived.** The simulation tracks share
counts, not weights. Drift between rebalances then falls out naturally rather
than being a rule someone must remember to apply, and "no phantom trading"
becomes structural: if no trade executes, the share vector is simply not
reassigned.

**The strategy is a seam.** The loop calls a
`rebalance_fn(as_of_date, price_history_through_as_of_date) -> {ticker: weight}`
on rebalance dates and never inspects what the strategy is. It is handed only
data dated on or before `as_of_date` — a test captures every call and asserts
the last row it received is exactly `as_of_date`.

**Prices are rebuilt from cleaned returns.** The engine does *not* read
`adj_close` directly. Each series is anchored at its first `adj_close` in the
window and compounded forward using
[`get_daily_returns`](#returns-get_daily_returns-is-the-mandatory-entry-point).
Without this, a TMPV backtest would book a 40% loss on the demerger date that no
holder ever suffered. For a security with no registered anomalies the
reconstruction reproduces `adj_close` exactly — there is a test asserting that.

**Coverage failures are loud.** Every ticker must have data spanning the entire
requested window or the request fails, naming the ticker and its actual range.
This is deliberately unlike Phases 3 and 4, which shrink to the shortest
available history. There the window is an estimation detail; here it *is* the
question, and quietly answering "2023–2026" when asked about "2022–2026" would
make the strategy and its baselines incomparable.

**One shared calendar.** All three series run over the inner join of the
portfolio tickers *and* the benchmark. `^NSEI` does not print on quite every day
the constituents trade (NSE holds occasional special sessions), and running the
three on different calendars would compute CAGR, volatility and drawdown over
different day sets. Including the benchmark costs a handful of days from the
strategy's calendar; that is the price of a like-for-like comparison.

### Transaction costs

`transaction_cost_bps` defaults to **15 bps per trade leg** — approximately STT,
exchange transaction charges, SEBI turnover fees, stamp duty and GST, plus a
small allowance for slippage, for a delivery trade through an Indian discount
broker. It does **not** include brokerage beyond that, capital gains tax, or
market impact on large orders. A real book pays more than this, not less.

Costs are paid out of the same pot being invested, so the engine solves the
target in two passes: size the trades, price the cost, then resize against what
is actually left. A day-one purchase of ₹10,00,000 at 15 bps therefore costs
₹1,497.75 (`C × rate × (1 − rate)`), not a naive ₹1,500.

### Baselines and what they show

```
                              CAGR     Vol  Sharpe  Sortino   MaxDD   Costs ₹  Cost%  Rebal
strategy (monthly)           7.08%  17.41%   0.116    0.159 -36.63%     9,984  1.00%     97
buy_and_hold_same_stocks     6.14%  17.75%   0.067    0.092 -37.06%     1,498  0.15%      1
buy_and_hold_nifty50         9.59%  17.35%   0.253    0.343 -38.44%     1,498  0.15%      1
```

Monthly rebalancing beat holding the same four stocks by 0.93pp of CAGR, at
₹8,487 more in costs — and **both lost to simply buying the index** by 2.5pp a
year. That second baseline exists precisely so this question cannot be avoided.

Reporting cost in rupees *and* as a percentage of capital makes the drag
concrete: 1.00% of starting capital went to friction over eight years.

### Metrics

Identical formulas for all three series. `cagr` is geometric — what the
portfolio actually compounded at, and the figure to quote. `annualised_return`
is the arithmetic mean daily return × 252, matching
`app/services/markowitz.py`'s convention so a Sharpe ratio here is directly
comparable with one from the optimiser; both import the same
`DEFAULT_RISK_FREE_RATE`. Sortino uses that same rate as the minimum acceptable
return and takes the mean of squared shortfalls over *all* observations, which
is the standard definition.

### The benchmark

The Nifty 50 index is stored as an ordinary security (`^NSEI`, `is_benchmark =
true`) so it flows through the same ingestion, anomaly and returns pipeline as
any stock — no second data path. Yahoo names indices with a `^` prefix and no
exchange suffix, which the ticker helpers pass through untouched.

```bash
docker compose exec backend python -m app.scripts.run_ingestion --tickers '^NSEI'
```

---

## Running locally

### Prerequisites

- Docker + Docker Compose
- Node.js 20+ (for the frontend dev server)
- Python 3.11+ (only if you want to run the backend outside Docker)

### 1. Environment

```bash
cp .env.example .env
cp frontend/.env.example frontend/.env
```

The defaults work as-is for local development.

### 2. Backend + database

```bash
docker compose up --build
```

This starts:

- **`db`** — Postgres 16 on `localhost:5432`
- **`backend`** — FastAPI on `localhost:8000`, which runs `alembic upgrade head`
  on startup and then serves with `--reload`

Verify:

```bash
curl http://localhost:8000/health
# {"status":"ok","db":"connected"}
```

Interactive API docs: <http://localhost:8000/docs>

### 3. Frontend

```bash
cd frontend
npm install
npm run dev
```

Open <http://localhost:5173>. The home page calls `GET /health` on load and
shows whether the backend and Postgres are both reachable.

---

## Common tasks

```bash
# Run the backend test suite (inside the container, against the real DB)
docker compose exec backend pytest

# Create a migration after changing a model
docker compose exec backend alembic revision --autogenerate -m "add foo"
docker compose exec backend alembic upgrade head

# Open a psql shell
docker compose exec db psql -U postgres -d portfolio_risk

# Tear down, keeping data
docker compose down

# Tear down and delete the Postgres volume
docker compose down -v
```

### Running the backend outside Docker

```bash
cd backend
python -m venv .venv && source .venv/bin/activate
pip install -r requirements.txt
alembic upgrade head
uvicorn app.main:app --reload
```

Point `POSTGRES_HOST` at `localhost` (the default) so it reaches the
Compose-managed Postgres.

---

## Layout

```
backend/
  app/
    main.py           FastAPI app, CORS, router registration
    core/
      config.py       pydantic-settings, reads env vars
      limitations.py  caveat strings, composed per endpoint
      tickers.py      NSE <-> Yahoo symbol helpers
      db.py           SQLAlchemy engine + session dependency
    models/           SQLAlchemy 2.0 models (Security, DailyPrice, PriceAnomaly)
    routers/          HTTP endpoints (health, securities, risk, portfolio, backtest)
    schemas/          Pydantic models (portfolio, risk, optimizer, backtest)
    services/         business logic
      data_ingestion.py   Nifty 50 seeding + yfinance OHLCV ingestion
      anomalies.py        price-anomaly registry, seeding and scanning
      returns.py          get_daily_returns — MANDATORY for all returns
      monte_carlo.py      parametric + historical-bootstrap VaR/CVaR
      markowitz.py        mean-variance optimisation, efficient frontier
      backtest.py         day-by-day engine, strategies, baselines, metrics
    data/             nifty50_seed.json
    scripts/          run_ingestion.py (python -m app.scripts.run_ingestion)
  alembic/            migrations
  tests/
frontend/
  src/
    api/              axios client + typed health call
    components/       Layout, HealthStatus
    pages/            PortfolioBuilder ("/"), Results ("/results")
```

## Notes

`docker-compose.yml` is for **local development only** — it ships default
credentials, publishes Postgres on the host, and bind-mounts the source tree for
hot reload. Do not deploy it as-is.
