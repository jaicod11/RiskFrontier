#!/usr/bin/env bash
# Restore a local data dump into Neon (or any Postgres) and verify it landed.
#
#   NEON_DATABASE_URL='postgresql://user:pass@host/db?sslmode=require' \
#     ./scripts/restore_to_neon.sh [dumps/riskfrontier-data.sql]
#
# The target must already have the schema:
#   DATABASE_URL="$NEON_DATABASE_URL" alembic upgrade head
#
# psql runs inside a container so no local Postgres client is required and the
# client version always matches. Neon requires SSL; keep sslmode=require in the
# connection string.

set -euo pipefail

DUMP="${1:-dumps/riskfrontier-data.sql}"
CLIENT_IMAGE="${PG_CLIENT_IMAGE:-postgres:17-alpine}"

if [[ -z "${NEON_DATABASE_URL:-}" ]]; then
  echo "error: NEON_DATABASE_URL is not set." >&2
  echo "  export NEON_DATABASE_URL='postgresql://user:pass@ep-xxx.neon.tech/db?sslmode=require'" >&2
  exit 1
fi
if [[ ! -f "${DUMP}" ]]; then
  echo "error: dump file '$DUMP' not found. Run ./scripts/dump_local_db.sh first." >&2
  exit 1
fi

run_psql() {
  docker run --rm -i -e PGCONNECT_TIMEOUT=30 "${CLIENT_IMAGE}" \
    psql "$NEON_DATABASE_URL" "$@"
}

echo "Checking the target schema exists..."
TABLES=$(run_psql -tAc "select count(*) from information_schema.tables
  where table_schema='public' and table_name in ('securities','daily_prices','price_anomalies');")
if [[ "$TABLES" -ne 3 ]]; then
  echo "error: target is missing tables (found $TABLES of 3)." >&2
  echo "  Run 'alembic upgrade head' against it first." >&2
  exit 1
fi

echo "Clearing existing data (restore is idempotent)..."
# Order matters: daily_prices and price_anomalies reference securities.
run_psql -q -c "TRUNCATE daily_prices, price_anomalies, securities RESTART IDENTITY CASCADE;"

echo "Restoring ${DUMP}..."
# ON_ERROR_STOP so a partial restore fails loudly rather than leaving a
# half-populated database that looks fine until a query returns too few rows.
docker run --rm -i -e PGCONNECT_TIMEOUT=30 "${CLIENT_IMAGE}" \
  psql "$NEON_DATABASE_URL" -v ON_ERROR_STOP=1 -q < "${DUMP}"

echo
echo "Verifying:"
run_psql -c "
  select 'securities'      as table, count(*) as rows from securities
  union all
  select 'daily_prices',   count(*) from daily_prices
  union all
  select 'price_anomalies',count(*) from price_anomalies
  order by 1;"

run_psql -c "
  select
    (select count(*) from securities where is_benchmark)      as benchmarks,
    (select count(*) from daily_prices p join securities s on s.id=p.security_id
      where s.ticker='^NSEI')                                 as nsei_rows,
    (select count(*) from daily_prices p join securities s on s.id=p.security_id
      where not s.is_benchmark)                               as constituent_rows;"

echo "Restore complete."
