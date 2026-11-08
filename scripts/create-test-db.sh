#!/bin/bash
# Create only the dedicated test database, and leave existing data alone.
set -euo pipefail
cd "$(dirname "$0")/.."
docker compose exec -T db psql -U kubo -d postgres -v ON_ERROR_STOP=1 <<'SQL'
SELECT 'CREATE DATABASE kubo_test' WHERE NOT EXISTS
  (SELECT FROM pg_database WHERE datname = 'kubo_test')
\gexec
SQL
