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
    routers/          HTTP endpoints (health today; features later)
    services/         business logic — Monte Carlo, optimiser, backtest
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
