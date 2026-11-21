#!/usr/bin/env bash
# Creates the VieWallet database roles and databases.
#
# Runs automatically on first start of the postgres container (docker-entrypoint-initdb.d)
# and is also run manually in CI against the postgres service container.
# Connection settings come from the standard libpq environment variables (PGHOST, PGUSER, ...).
#
# Roles:
#   viewallet_migrator  owns the databases and runs DDL (Alembic migrations, seed).
#   viewallet_app       used by the API and worker; DML only, granted by migrations.
set -euo pipefail

: "${VW_DB_MIGRATOR_PASSWORD:?VW_DB_MIGRATOR_PASSWORD is required}"
: "${VW_DB_APP_PASSWORD:?VW_DB_APP_PASSWORD is required}"
DATABASES="${VW_DB_NAMES:-viewallet viewallet_test}"

psql -v ON_ERROR_STOP=1 \
  --username "${POSTGRES_USER:-${PGUSER:-postgres}}" \
  --dbname postgres \
  -v migrator_password="$VW_DB_MIGRATOR_PASSWORD" \
  -v app_password="$VW_DB_APP_PASSWORD" <<'SQL'
SELECT 'CREATE ROLE viewallet_migrator LOGIN'
WHERE NOT EXISTS (SELECT 1 FROM pg_roles WHERE rolname = 'viewallet_migrator') \gexec
SELECT 'CREATE ROLE viewallet_app LOGIN'
WHERE NOT EXISTS (SELECT 1 FROM pg_roles WHERE rolname = 'viewallet_app') \gexec
ALTER ROLE viewallet_migrator WITH LOGIN PASSWORD :'migrator_password';
ALTER ROLE viewallet_app WITH LOGIN PASSWORD :'app_password';
SQL

for db in $DATABASES; do
  psql -v ON_ERROR_STOP=1 \
    --username "${POSTGRES_USER:-${PGUSER:-postgres}}" \
    --dbname postgres \
    -v db="$db" <<'SQL'
SELECT format('CREATE DATABASE %I OWNER viewallet_migrator', :'db')
WHERE NOT EXISTS (SELECT 1 FROM pg_database WHERE datname = :'db') \gexec
SELECT format('REVOKE ALL ON DATABASE %I FROM PUBLIC', :'db') \gexec
SELECT format('GRANT CONNECT, TEMPORARY ON DATABASE %I TO viewallet_app', :'db') \gexec
SQL
  psql -v ON_ERROR_STOP=1 \
    --username "${POSTGRES_USER:-${PGUSER:-postgres}}" \
    --dbname "$db" <<'SQL'
REVOKE CREATE ON SCHEMA public FROM PUBLIC;
CREATE EXTENSION IF NOT EXISTS pg_trgm;
CREATE EXTENSION IF NOT EXISTS citext;
SQL
done
echo "VieWallet roles and databases are ready: $DATABASES"
