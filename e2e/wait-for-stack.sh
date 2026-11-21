#!/usr/bin/env bash
# Waits until the API is ready and both frontends answer (used by CI and locally).
set -euo pipefail

API_URL="${VW_E2E_API_URL:-http://127.0.0.1:8000}"
WEB_URL="${VW_E2E_WEB_URL:-http://127.0.0.1:8080}"
ADMIN_URL="${VW_E2E_ADMIN_URL:-http://127.0.0.1:8081}"
DEADLINE=$((SECONDS + ${VW_E2E_WAIT_SECONDS:-180}))

wait_for() {
  local name="$1" url="$2"
  until curl -fsS -o /dev/null "$url"; do
    if ((SECONDS > DEADLINE)); then
      echo "Timed out waiting for $name at $url" >&2
      docker compose ps -a >&2 || true
      exit 1
    fi
    sleep 2
  done
  echo "$name is up ($url)"
}

wait_for api "$API_URL/health/ready"
wait_for web "$WEB_URL/"
wait_for admin "$ADMIN_URL/"
