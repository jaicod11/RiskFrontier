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

| `daily_prices` | |
|---|---|
| `id` | PK |
| `security_id` | FK → `securities.id`, cascade delete |
| `date` | trading date |
| `open` / `high` / `low` / `close` / `adj_close` | `NUMERIC(18,4)` |
| `volume` | `BIGINT` |

`daily_prices` has a **unique constraint on `(security_id, date)`** so that
re-running an ingest is idempotent.

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

### Known data quality issue: unadjusted corporate actions

`auto_adjust=True` corrects splits and dividends, but **only where Yahoo has
recorded the action**. Two ingested series contain a price discontinuity that
Yahoo does not report as a split, so nothing adjusts it away:

| Ticker | Date | Apparent move | What actually happened |
|---|---|---|---|
| `TMPV` | 2025-10-14 | −40.2% | Tata Motors demerger. Yahoo carries the pre-demerger parent's history forward under `TMPV.NS`, so spinning off the commercial-vehicles business looks like a 40% loss |
| `TRENT` | 2026-01-01 | −33.0% | Price level resets by a ~3:2 ratio on *below*-average volume with no split recorded — a corporate action, not a selloff |

Neither is a real return. Left alone they will inflate volatility, distort the
covariance matrix, and hand the backtester a fake crash. They are declared in
`UNADJUSTED_CORPORATE_ACTIONS` in `data_ingestion.py` and printed as a warning
at the end of every coverage report.

**The risk and backtest layers must neutralise these before computing returns**
— by treating the affected date as a gap in the return series rather than a
price change, or by rescaling the pre-action history by the break ratio.

Note that `TMPV` therefore has a *full* ten years of data rather than the few
months its October 2025 listing would suggest: the series is continuous, but
only the portion after 2025-10-14 describes the passenger-vehicle company.

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
      db.py           SQLAlchemy engine + session dependency
    models/           SQLAlchemy 2.0 models (Security, DailyPrice)
    routers/          HTTP endpoints (health, securities)
    services/         business logic
      data_ingestion.py   Nifty 50 seeding + yfinance OHLCV ingestion
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
