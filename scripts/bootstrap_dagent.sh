#!/usr/bin/env bash
set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$ROOT"

usage() {
  cat <<'EOF'
Usage:
  scripts/bootstrap_dagent.sh

Bootstraps a fresh clone with:
  - Python virtualenv and worker package
  - main worker config/env/service on port 8765
  - ChatGPT worker config/env/service on port 8767
  - n8n/Postgres/Redis Docker env and containers
  - dAgent n8n router workflow import/publish
  - user startup service for workers + n8n

Environment overrides:
  N8N_PUBLIC_HOSTNAME=<host>     Default: localhost
  DAGENT_SKIP_DOCKER=1           Skip n8n Docker setup/import
  DAGENT_SKIP_STARTUP=1          Skip startup service installation
  DAGENT_WORKER_BIND_HOST=<host> Default: 0.0.0.0
EOF
}

if [[ "${1:-}" == "-h" || "${1:-}" == "--help" ]]; then
  usage
  exit 0
fi

copy_if_missing() {
  local src="$1"
  local dst="$2"
  if [[ -f "$dst" ]]; then
    echo "ok  $dst already exists"
    return 0
  fi
  cp "$src" "$dst"
  echo "new $dst from $src"
}

echo "== dAgent bootstrap =="
echo "Repo: $ROOT"
echo

copy_if_missing worker/config.example.yml worker/config.yml
copy_if_missing worker/config.chatgpt.example.yml worker/config.chatgpt.yml

echo
echo "== Workers =="
export DAGENT_WORKER_BIND_HOST="${DAGENT_WORKER_BIND_HOST:-0.0.0.0}"
scripts/dagentctl up main 8765 "$ROOT/worker/config.yml"
scripts/dagentctl up chatgpt 8767 "$ROOT/worker/config.chatgpt.yml"

if [[ "${DAGENT_SKIP_DOCKER:-0}" != "1" ]]; then
  echo
  echo "== n8n stack =="
  N8N_PUBLIC_HOSTNAME="${N8N_PUBLIC_HOSTNAME:-localhost}" scripts/deploy_n8n.sh
  scripts/n8nctl watch-env main
  scripts/n8nctl import-workflows
else
  echo
  echo "Skipped Docker/n8n setup because DAGENT_SKIP_DOCKER=1."
fi

if [[ "${DAGENT_SKIP_STARTUP:-0}" != "1" ]]; then
  echo
  echo "== Startup service =="
  DAGENT_STARTUP_WORKERS="${DAGENT_STARTUP_WORKERS:-main chatgpt}" scripts/n8nctl startup
else
  echo
  echo "Skipped startup service because DAGENT_SKIP_STARTUP=1."
fi

echo
echo "== Done =="
scripts/dagentctl overview || true
echo
echo "Dashboard:"
scripts/dagentctl dashboard main
echo
echo "n8n:"
echo "  scripts/n8nctl health"
echo
echo "Shortcut secret:"
echo "  grep '^DAGENT_SHORTCUT_SECRET=' docker/automation-stack/.env"
