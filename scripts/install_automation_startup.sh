#!/usr/bin/env bash
set -euo pipefail

cd "$(dirname "$0")/.."

ROOT_DIR="$(pwd -P)"
UNIT_DIR="$HOME/.config/systemd/user"
UNIT_FILE="$UNIT_DIR/dagent-automation-stack.service"

mkdir -p "$UNIT_DIR"

cat > "$UNIT_FILE" <<EOF
[Unit]
Description=dAgent automation stack
Documentation=file://$ROOT_DIR/docs/operations.md
After=default.target

[Service]
Type=oneshot
WorkingDirectory=$ROOT_DIR
ExecStartPre=/usr/bin/bash -lc 'for i in {1..60}; do docker info >/dev/null 2>&1 && exit 0; sleep 2; done; echo "Docker was not ready in time" >&2; exit 1'
ExecStart=$ROOT_DIR/scripts/n8nctl up
RemainAfterExit=yes
TimeoutStartSec=180

[Install]
WantedBy=default.target
EOF

systemctl --user daemon-reload
systemctl --user enable --now dagent-automation-stack.service

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
