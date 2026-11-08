#!/bin/bash
set -euo pipefail
cd "$(dirname "$0")/.."
if [[ ! -x .venv/bin/python ]]; then
  echo "First run: python3 -m venv .venv && .venv/bin/python -m pip install -r requirements.txt" >&2
  exit 1
fi
args=(app.main:app --host "${KUBO_HOST:-0.0.0.0}" --port "${KUBO_PORT:-8000}" --timeout-graceful-shutdown 3)
if [[ -n "${KUBO_TLS_CERT:-}" || -n "${KUBO_TLS_KEY:-}" ]]; then
  : "${KUBO_TLS_CERT:?Set both KUBO_TLS_CERT and KUBO_TLS_KEY}"
  : "${KUBO_TLS_KEY:?Set both KUBO_TLS_CERT and KUBO_TLS_KEY}"
  args+=(--ssl-certfile "$KUBO_TLS_CERT" --ssl-keyfile "$KUBO_TLS_KEY")
  export KUBO_COOKIE_SECURE=1
fi
exec bash scripts/with-local-env.sh .venv/bin/python -m uvicorn "${args[@]}"
