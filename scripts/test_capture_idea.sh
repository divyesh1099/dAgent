#!/usr/bin/env bash
set -euo pipefail

WORKER_URL="${DAGENT_WORKER_URL:-http://127.0.0.1:8765}"

if [[ -z "${DAGENT_WORKER_API_TOKEN:-}" ]]; then
  echo "DAGENT_WORKER_API_TOKEN is required." >&2
  exit 1
fi

curl -fsS -X POST "$WORKER_URL/v1/jobs" \
  -H "Authorization: Bearer $DAGENT_WORKER_API_TOKEN" \
  -H "Content-Type: application/json" \
  --data "$(printf '{"source":"apple_watch","intent":"capture_idea","task":"%s","input_type":"voice","idempotency_key":"manual-watch-test-%s"}' "${1:-Manual Apple Watch style test note.}" "$(date +%s)")"

echo

