#!/usr/bin/env bash
set -euo pipefail

cd "$(dirname "$0")/.."

if [[ $# -lt 1 ]]; then
  cat >&2 <<'EOF'
Usage:
  scripts/test_n8n_watch_webhook.sh <webhook-url> [task]

For public Cloudflare Access-protected URLs, also set:
  export CF_ACCESS_CLIENT_ID='...'
  export CF_ACCESS_CLIENT_SECRET='...'
EOF
  exit 2
fi

WEBHOOK_URL="$1"
TASK="${2:-First n8n to dAgent capture idea test.}"
ENV_FILE="docker/automation-stack/.env"

get_env() {
  local key="$1"
  awk -F= -v key="$key" '$1 == key { sub(/^[^=]*=/, ""); print; exit }' "$ENV_FILE"
}

shortcut_secret="$(get_env DAGENT_SHORTCUT_SECRET)"
if [[ -z "$shortcut_secret" ]]; then
  echo "DAGENT_SHORTCUT_SECRET is missing. Run scripts/n8nctl watch-env first." >&2
  exit 1
fi

headers=(
  -H "Content-Type: application/json"
  -H "X-Dagent-Shortcut-Secret: $shortcut_secret"
)

if [[ -n "${CF_ACCESS_CLIENT_ID:-}" && -n "${CF_ACCESS_CLIENT_SECRET:-}" ]]; then
  headers+=(
    -H "CF-Access-Client-Id: $CF_ACCESS_CLIENT_ID"
    -H "CF-Access-Client-Secret: $CF_ACCESS_CLIENT_SECRET"
  )
fi

payload="$(python3 - "$TASK" <<'PY'
from __future__ import annotations

import json
import sys
import time

task = sys.argv[1]
print(json.dumps({
    "source": "apple_watch",
    "intent": "capture_idea",
    "task": task,
    "input_type": "voice",
    "idempotency_key": f"n8n-watch-test-{int(time.time())}",
}))
PY
)"

headers_file="$(mktemp)"
body_file="$(mktemp)"
trap 'rm -f "$headers_file" "$body_file"' EXIT

http_code="$(
  curl -sS -X POST "$WEBHOOK_URL" \
    -D "$headers_file" \
    -o "$body_file" \
    -w '%{http_code}' \
    "${headers[@]}" \
    --data "$payload"
)"

if [[ "$http_code" =~ ^2 ]]; then
  cat "$body_file"
  echo
  exit 0
fi

echo "Request returned HTTP $http_code." >&2
if [[ "$http_code" =~ ^3 ]]; then
  location="$(awk 'tolower($1) == "location:" { sub(/^[^ ]+ /, ""); print; exit }' "$headers_file" | tr -d '\r')"
  if [[ -n "$location" ]]; then
    echo "Redirect location: $location" >&2
  fi
  if grep -qi '/cdn-cgi/access/login' "$headers_file"; then
    echo "Cloudflare Access did not accept the service token headers." >&2
    echo "Check the n8n Access app has a Service Auth policy for this service token." >&2
  fi
else
  cat "$body_file" >&2
  echo >&2
fi
exit 1
