#!/usr/bin/env bash
set -euo pipefail

cd "$(dirname "$0")/.."

ENV_FILE="docker/automation-stack/.env"
EXAMPLE_FILE="docker/automation-stack/.env.example"
HOSTNAME="${N8N_PUBLIC_HOSTNAME:-n8n.divyeshvishwakarma.com}"

rand_hex() {
  openssl rand -hex "${1:-32}"
}

if [[ ! -f "$ENV_FILE" ]]; then
  cp "$EXAMPLE_FILE" "$ENV_FILE"
  chmod 600 "$ENV_FILE"

  python3 - "$ENV_FILE" "$HOSTNAME" "$(rand_hex 24)" "$(rand_hex 32)" "$(rand_hex 32)" <<'PY'
from __future__ import annotations

import sys
from pathlib import Path

path = Path(sys.argv[1])
hostname = sys.argv[2]
postgres_password = sys.argv[3]
encryption_key = sys.argv[4]
jwt_secret = sys.argv[5]

values = {
    "POSTGRES_PASSWORD": postgres_password,
    "N8N_ENCRYPTION_KEY": encryption_key,
    "N8N_USER_MANAGEMENT_JWT_SECRET": jwt_secret,
    "N8N_HOST": hostname,
    "N8N_PROTOCOL": "https",
    "N8N_WEBHOOK_URL": f"https://{hostname}/",
    "N8N_EDITOR_BASE_URL": f"https://{hostname}/",
    "DAGENT_WORKER_URL": "http://host.docker.internal:8765",
    "CLOUDFLARE_TUNNEL_TOKEN": "unused-existing-system-cloudflared-service",
}

lines = []
for line in path.read_text(encoding="utf-8").splitlines():
    if not line or line.startswith("#") or "=" not in line:
        lines.append(line)
        continue
    key, _ = line.split("=", 1)
    if key in values:
        lines.append(f"{key}={values[key]}")
    else:
        lines.append(line)

path.write_text("\n".join(lines) + "\n", encoding="utf-8")
PY

  echo "Created $ENV_FILE"
else
  echo "Using existing $ENV_FILE"
fi

docker compose --env-file "$ENV_FILE" -f docker/automation-stack/compose.yml up -d postgres redis n8n n8n-worker

echo
echo "Waiting for n8n on http://127.0.0.1:5678 ..."
for _ in $(seq 1 60); do
  if curl -fsS -I http://127.0.0.1:5678 >/dev/null 2>&1; then
    echo "n8n is reachable locally."
    exit 0
  fi
  sleep 2
done

echo "n8n did not become reachable in time. Check logs with:"
echo "  scripts/n8nctl logs"
exit 1

