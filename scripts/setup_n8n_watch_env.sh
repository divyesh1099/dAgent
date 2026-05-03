#!/usr/bin/env bash
set -euo pipefail

cd "$(dirname "$0")/.."

WORKER_NAME="${1:-main}"
ENV_FILE="docker/automation-stack/.env"
COMPOSE_FILE="docker/automation-stack/compose.yml"

if [[ ! -f "$ENV_FILE" ]]; then
  echo "$ENV_FILE does not exist. Run scripts/deploy_n8n.sh first." >&2
  exit 1
fi

worker_token="$(scripts/dagentctl token "$WORKER_NAME")"
if [[ -z "$worker_token" ]]; then
  echo "Could not read worker token for $WORKER_NAME." >&2
  exit 1
fi

chatgpt_worker_token="$(scripts/dagentctl token chatgpt 2>/dev/null || true)"

shortcut_secret=""
if grep -q '^DAGENT_SHORTCUT_SECRET=' "$ENV_FILE"; then
  shortcut_secret="$(grep '^DAGENT_SHORTCUT_SECRET=' "$ENV_FILE" | tail -n1 | cut -d= -f2-)"
fi
if [[ -z "$shortcut_secret" || "$shortcut_secret" == "replace-with-long-random-secret" ]]; then
  shortcut_secret="$(openssl rand -hex 32)"
fi

cf_access_client_id="${CF_ACCESS_CLIENT_ID:-}"
cf_access_client_secret="${CF_ACCESS_CLIENT_SECRET:-}"
if [[ -z "$cf_access_client_id" ]] && grep -q '^CF_ACCESS_CLIENT_ID=' "$ENV_FILE"; then
  cf_access_client_id="$(grep '^CF_ACCESS_CLIENT_ID=' "$ENV_FILE" | tail -n1 | cut -d= -f2-)"
fi
if [[ -z "$cf_access_client_secret" ]] && grep -q '^CF_ACCESS_CLIENT_SECRET=' "$ENV_FILE"; then
  cf_access_client_secret="$(grep '^CF_ACCESS_CLIENT_SECRET=' "$ENV_FILE" | tail -n1 | cut -d= -f2-)"
fi

python3 - "$ENV_FILE" "$worker_token" "$shortcut_secret" "$cf_access_client_id" "$cf_access_client_secret" "$chatgpt_worker_token" <<'PY'
from __future__ import annotations

import sys
from pathlib import Path

path = Path(sys.argv[1])
values = {
    "DAGENT_WORKER_API_TOKEN": sys.argv[2],
    "DAGENT_SHORTCUT_SECRET": sys.argv[3],
    "N8N_BLOCK_ENV_ACCESS_IN_NODE": "false",
}
if sys.argv[4]:
    values["CF_ACCESS_CLIENT_ID"] = sys.argv[4]
if sys.argv[5]:
    values["CF_ACCESS_CLIENT_SECRET"] = sys.argv[5]
if sys.argv[6]:
    values["DAGENT_CHATGPT_WORKER_URL"] = "http://host.docker.internal:8767"
    values["DAGENT_CHATGPT_WORKER_API_TOKEN"] = sys.argv[6]

seen: set[str] = set()
lines: list[str] = []
for line in path.read_text(encoding="utf-8").splitlines():
    if line and not line.startswith("#") and "=" in line:
        key = line.split("=", 1)[0]
        if key in values:
            lines.append(f"{key}={values[key]}")
            seen.add(key)
            continue
    lines.append(line)

if lines and lines[-1] != "":
    lines.append("")
for key, value in values.items():
    if key not in seen:
        lines.append(f"{key}={value}")

path.write_text("\n".join(lines).rstrip() + "\n", encoding="utf-8")
PY

chmod 600 "$ENV_FILE"

docker compose --env-file "$ENV_FILE" -f "$COMPOSE_FILE" up -d postgres redis
docker compose --env-file "$ENV_FILE" -f "$COMPOSE_FILE" up -d --force-recreate n8n n8n-worker

echo "Configured n8n watch environment."
echo "Worker token: stored in $ENV_FILE as DAGENT_WORKER_API_TOKEN"
if [[ -n "$chatgpt_worker_token" ]]; then
  echo "ChatGPT worker token: stored in $ENV_FILE as DAGENT_CHATGPT_WORKER_API_TOKEN"
fi
echo "Shortcut secret: stored in $ENV_FILE as DAGENT_SHORTCUT_SECRET"
if [[ -n "$cf_access_client_id" && -n "$cf_access_client_secret" && "$cf_access_client_id" != "replace-with-cloudflare-access-client-id" && "$cf_access_client_secret" != "replace-with-cloudflare-access-client-secret" ]]; then
  echo "Cloudflare Access service token: stored in $ENV_FILE"
else
  echo "Cloudflare Access service token: not configured"
  echo "  Store it with: scripts/n8nctl access-env '<client-id>' '<client-secret>'"
fi
echo
echo "Show the shortcut secret when you need to enter it in Apple Shortcuts:"
echo "  grep '^DAGENT_SHORTCUT_SECRET=' $ENV_FILE"
