#!/usr/bin/env bash
# Creates the Payment Provider Simulator role and databases (ADR-0005).
#
# The simulator plays an external bank: it has its own role and its own databases and never
# sees VieWallet tables. Idempotent; runs on first container start and can be re-run by hand
# on an existing volume:
#   docker compose exec postgres bash /docker-entrypoint-initdb.d/02-simulator.sh
set -euo pipefail

: "${VW_DB_SIM_PASSWORD:?VW_DB_SIM_PASSWORD is required}"
DATABASES="${VW_SIM_DB_NAMES:-viewallet_sim viewallet_sim_test}"
SUPERUSER="${POSTGRES_USER:-${PGUSER:-postgres}}"

psql -v ON_ERROR_STOP=1 --username "$SUPERUSER" --dbname postgres \
  -v sim_password="$VW_DB_SIM_PASSWORD" <<'SQL'
SELECT 'CREATE ROLE viewallet_sim LOGIN'
WHERE NOT EXISTS (SELECT 1 FROM pg_roles WHERE rolname = 'viewallet_sim') \gexec
ALTER ROLE viewallet_sim WITH LOGIN PASSWORD :'sim_password';
SQL

for db in $DATABASES; do
  psql -v ON_ERROR_STOP=1 --username "$SUPERUSER" --dbname postgres -v db="$db" <<'SQL'
SELECT format('CREATE DATABASE %I OWNER viewallet_sim', :'db')
WHERE NOT EXISTS (SELECT 1 FROM pg_database WHERE datname = :'db') \gexec
SELECT format('REVOKE ALL ON DATABASE %I FROM PUBLIC', :'db') \gexec
SQL
done
echo "Simulator role and databases are ready: $DATABASES"
