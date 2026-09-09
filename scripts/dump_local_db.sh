#!/usr/bin/env bash
# Dump the ingested market data from the local docker-compose Postgres.
#
# Price data is seeded into production by restoring this dump, NOT by
# re-running the ingestion. yfinance rate-limits datacenter IPs: in Phase 1 it
# returned HTTP 429 for all 50 tickers, which surfaced as empty frames rather
# than an error. A dump/restore is deterministic and takes seconds.
#
# Only the data tables are dumped. The schema is owned by Alembic and must be
# created with `alembic upgrade head` on the target first.
#
#   ./scripts/dump_local_db.sh [output.sql]

set -euo pipefail

OUT="${1:-dumps/riskfrontier-data.sql}"
CONTAINER="${LOCAL_DB_CONTAINER:-prt-db}"
DB="${POSTGRES_DB:-portfolio_risk}"
USER="${POSTGRES_USER:-postgres}"

mkdir -p "$(dirname "${OUT}")"

if ! docker ps --format '{{.Names}}' | grep -qx "${CONTAINER}"; then
  echo "error: container '$CONTAINER' is not running. Start it with 'docker compose up -d db'." >&2
  exit 1
fi

echo "Dumping securities, daily_prices and price_anomalies from ${CONTAINER}..."

# --data-only: Alembic owns the schema on the target.
# --no-owner/--no-privileges: the Neon role differs from the local one.
#
# The default COPY format is used rather than --column-inserts. Both restore
# correctly, but COPY streams one statement per table instead of ~124,000
# INSERTs: 3 s versus 44 s locally, and the difference is far larger over a
# network to a serverless database. Set DUMP_INSERTS=true if a target ever
# needs the more conservative statement-per-row form.
docker exec "${CONTAINER}" pg_dump \
  --username="${USER}" \
  --dbname="${DB}" \
  --data-only \
  --no-owner \
  --no-privileges \
  ${DUMP_INSERTS:+--column-inserts} \
  --table=securities \
  --table=daily_prices \
  --table=price_anomalies \
  > "${OUT}"

SIZE=$(du -h "${OUT}" | cut -f1)
LINES=$(wc -l < "${OUT}" | tr -d ' ')
echo "Wrote $OUT  ($SIZE, $ROWS INSERT statements)"
echo
echo "Row counts in the source database:"
docker exec "${CONTAINER}" psql -U "${USER}" -d "${DB}" -t -c "
  select '  securities       ' || count(*) from securities
  union all select '  daily_prices     ' || count(*) from daily_prices
  union all select '  price_anomalies  ' || count(*) from price_anomalies;"
