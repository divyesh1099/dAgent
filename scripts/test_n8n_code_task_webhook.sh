#!/usr/bin/env bash
set -euo pipefail

cd "$(dirname "$0")/.."

if [[ $# -lt 1 ]]; then
  cat >&2 <<'EOF'
Usage:
  scripts/test_n8n_code_task_webhook.sh <webhook-url> [repo] [task]

Sends a dry-run code_task through the n8n watch router. The worker should return
approval_required. Approve the returned job in the dashboard or with the worker
API to verify code-server URL generation.
EOF
  exit 2
fi

WEBHOOK_URL="$1"
REPO="${2:-dagent}"
TASK="${3:-Dry-run from n8n: verify Apple Watch code task route.}"
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

cf_access_client_id="${CF_ACCESS_CLIENT_ID:-$(get_env CF_ACCESS_CLIENT_ID)}"
cf_access_client_secret="${CF_ACCESS_CLIENT_SECRET:-$(get_env CF_ACCESS_CLIENT_SECRET)}"

headers=(
  -H "Content-Type: application/json"
  -H "X-Dagent-Shortcut-Secret: $shortcut_secret"
)

if [[ -n "$cf_access_client_id" && -n "$cf_access_client_secret" && "$cf_access_client_id" != "replace-with-cloudflare-access-client-id" && "$cf_access_client_secret" != "replace-with-cloudflare-access-client-secret" ]]; then
  headers+=(
    -H "CF-Access-Client-Id: $cf_access_client_id"
    -H "CF-Access-Client-Secret: $cf_access_client_secret"
  )
fi

payload="$(
  python3 - "$REPO" "$TASK" <<'PY'
from __future__ import annotations

import json
import sys
import time

repo = sys.argv[1]
task = sys.argv[2]
print(json.dumps({
    "source": "apple_watch",
    "intent": "code_task",
    "repo": repo,
    "task": task,
    "input_type": "voice",
    "dry_run": True,
    "idempotency_key": f"n8n-code-task-test-{int(time.time())}",
    "metadata": {
        "flavor": "codex",
    },
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
fi
cat "$body_file" >&2
echo >&2
exit 1
