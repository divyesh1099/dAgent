#!/usr/bin/env bash
set -euo pipefail

cd "$(dirname "$0")/.."

ROOT_DIR="$(pwd -P)"
UNIT_DIR="$HOME/.config/systemd/user"
UNIT_FILE="$UNIT_DIR/dagent-automation-stack.service"
STARTUP_WORKERS="${DAGENT_STARTUP_WORKERS:-${DAGENT_STARTUP_WORKER:-main chatgpt}}"

mkdir -p "$UNIT_DIR"

"$ROOT_DIR/scripts/dagentctl" install

worker_port() {
  case "$1" in
    main) echo "${DAGENT_STARTUP_MAIN_PORT:-8765}" ;;
    chatgpt) echo "${DAGENT_STARTUP_CHATGPT_PORT:-8767}" ;;
    *) echo "${DAGENT_STARTUP_WORKER_PORT:-8765}" ;;
  esac
}

worker_unit_names=()
for worker in $STARTUP_WORKERS; do
  port="$(worker_port "$worker")"
  if [[ ! -f "$HOME/.config/dagent/workers/$worker.env" ]]; then
    "$ROOT_DIR/scripts/dagentctl" init "$worker" "$port"
  fi
  "$ROOT_DIR/scripts/dagentctl" enable "$worker"
  "$ROOT_DIR/scripts/dagentctl" start "$worker" || true
  worker_unit_names+=("dagent-worker@$worker.service")
done

worker_units="${worker_unit_names[*]}"

cat > "$UNIT_FILE" <<EOF
[Unit]
Description=dAgent automation stack
Documentation=file://$ROOT_DIR/docs/operations.md
Wants=network-online.target $worker_units
After=default.target network-online.target $worker_units
StartLimitIntervalSec=0

[Service]
Type=oneshot
WorkingDirectory=$ROOT_DIR
ExecStartPre=/usr/bin/bash -lc 'for i in {1..180}; do output="\$(docker info 2>&1)" && exit 0; if command -v sg >/dev/null 2>&1 && getent group docker | cut -d: -f4 | tr "," "\n" | grep -qx "\$USER" && sg docker -c "docker info >/dev/null 2>&1"; then exit 0; fi; if echo "\$output" | grep -qi "permission denied"; then echo "Docker is running, but $USER cannot access /var/run/docker.sock. Run: sudo usermod -aG docker $USER ; then log out and back in." >&2; exit 126; fi; sleep 2; done; echo "Docker was not ready in time" >&2; exit 1'
ExecStartPre=/usr/bin/bash -lc 'for worker in $STARTUP_WORKERS; do for i in {1..45}; do "$ROOT_DIR/scripts/dagentctl" health "\$worker" >/dev/null 2>&1 && break; if [ "\$i" = 45 ]; then echo "dAgent worker \$worker was not ready in time" >&2; exit 1; fi; sleep 2; done; done'
ExecStart=$ROOT_DIR/scripts/n8nctl up
RemainAfterExit=yes
TimeoutStartSec=480

[Install]
WantedBy=default.target
EOF

systemctl --user daemon-reload
systemctl --user enable dagent-automation-stack.service
systemctl --user start dagent-automation-stack.service || true

echo "Installed and started $UNIT_FILE"
echo
systemctl --user --no-pager status dagent-automation-stack.service || true

if command -v loginctl >/dev/null 2>&1; then
  linger="$(loginctl show-user "$USER" -p Linger --value 2>/dev/null || true)"
  if [[ "$linger" != "yes" ]]; then
    if loginctl enable-linger "$USER" >/dev/null 2>&1; then
      echo
      echo "Enabled lingering for $USER so the user service can start before login."
    else
      echo
      echo "To let the user service start before desktop login, run:"
      echo "  sudo loginctl enable-linger $USER"
    fi
  fi
fi
