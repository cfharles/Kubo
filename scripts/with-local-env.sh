#!/bin/bash
# The generated .env uses simple shell/Compose-compatible assignments.
set -euo pipefail
cd "$(dirname "$0")/.."
if [[ ! -f .env ]]; then
  echo 'First run: python3 scripts/init-local-env.py' >&2
  exit 1
fi
source .env
: "${KUBO_DB_PASSWORD:?Missing database password in .env}"
local_server="postgresql://kubo:${KUBO_DB_PASSWORD}@127.0.0.1:${KUBO_DB_PORT:-55432}"
export DATABASE_URL="${DATABASE_URL:-$local_server/kubo}"
export TEST_DATABASE_URL="${TEST_DATABASE_URL:-$local_server/kubo_test}"
export PLAYWRIGHT_BROWSERS_PATH="${PLAYWRIGHT_BROWSERS_PATH:-$PWD/.playwright-browsers}"
exec "$@"
