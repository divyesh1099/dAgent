#!/usr/bin/env bash
set -euo pipefail

cd "$(dirname "$0")/.."

if [[ ! -d .venv ]]; then
  echo ".venv not found. Run: make install" >&2
  exit 1
fi

if [[ -z "${DAGENT_WORKER_API_TOKEN:-}" ]]; then
  echo "DAGENT_WORKER_API_TOKEN is required." >&2
  echo "Example:" >&2
  echo "  export DAGENT_WORKER_API_TOKEN=\"paste-generated-token\"" >&2
  exit 1
fi

export DAGENT_WORKER_CONFIG="${DAGENT_WORKER_CONFIG:-$PWD/worker/config.yml}"

. .venv/bin/activate
exec uvicorn dagent_worker.main:app --host 127.0.0.1 --port 8765

