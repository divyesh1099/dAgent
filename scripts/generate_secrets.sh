#!/usr/bin/env bash
set -euo pipefail

rand_hex() {
  openssl rand -hex "${1:-32}"
}

echo "POSTGRES_PASSWORD=$(rand_hex 24)"
echo "N8N_ENCRYPTION_KEY=$(rand_hex 32)"
echo "N8N_USER_MANAGEMENT_JWT_SECRET=$(rand_hex 32)"
echo "DAGENT_WORKER_API_TOKEN=$(rand_hex 32)"
echo "DAGENT_WORKER_HMAC_SECRET=$(rand_hex 32)"
echo "DAGENT_SHORTCUT_SECRET=$(rand_hex 32)"

