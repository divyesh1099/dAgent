#!/usr/bin/env bash
set -euo pipefail

WORKER_URL="${DAGENT_WORKER_URL:-http://127.0.0.1:8765}"

if [[ -z "${DAGENT_WORKER_API_TOKEN:-}" ]]; then
  echo "DAGENT_WORKER_API_TOKEN is required" >&2
  exit 1
fi

curl -fsS "$WORKER_URL/health"
echo

curl -fsS \
  -H "Authorization: Bearer $DAGENT_WORKER_API_TOKEN" \
  "$WORKER_URL/ready"
echo

