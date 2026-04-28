#!/usr/bin/env bash
set -euo pipefail

cd "$(dirname "$0")/.."

CONFIG="${DAGENT_WORKER_CONFIG:-$PWD/worker/config.yml}"
MESSAGE="${1:-dAgent ntfy smoke test from $(hostname)}"

if [[ ! -x .venv/bin/python ]]; then
  echo ".venv not found. Run: make install" >&2
  exit 1
fi

DAGENT_WORKER_CONFIG="$CONFIG" .venv/bin/python - "$MESSAGE" <<'PY'
from __future__ import annotations

import sys

from dagent_worker.config import load_config
from dagent_worker.notifier import Notifier

message = sys.argv[1]
config = load_config()
notifier = Notifier(config.notifications)

if not notifier.enabled():
    raise SystemExit("ntfy is not configured in worker/config.yml")

notifier.send(title="dAgent ntfy test", message=message, priority="default", tags="white_check_mark")
print(f"sent to {config.notifications.ntfy_url.rstrip('/')}/{config.notifications.ntfy_topic}")
PY

