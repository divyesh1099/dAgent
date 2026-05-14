#!/usr/bin/env bash
set -euo pipefail

ttyd_bin="${TTYD_BIN:-$HOME/.local/bin/ttyd}"
bind_host="${TERMINAL_BIND_HOST:-127.0.0.1}"
port="${TERMINAL_PORT:-7681}"
start_dir="${TERMINAL_START_DIR:-$HOME}"
title="${TERMINAL_TITLE:-Divyesh Terminal}"
terminal_type="${TERMINAL_TYPE:-xterm-256color}"
username="${TERMINAL_USERNAME:-}"
password="${TERMINAL_PASSWORD:-}"
shell_bin="${TERMINAL_SHELL:-${SHELL:-}}"

if [[ -z "$shell_bin" ]]; then
  shell_bin="$(getent passwd "$USER" | cut -d: -f7)"
fi
if [[ -z "$shell_bin" || ! -x "$shell_bin" ]]; then
  shell_bin="/bin/bash"
fi

mkdir -p "$start_dir"

ttyd_args=(
  --interface "$bind_host"
  --port "$port"
  --cwd "$start_dir"
  --terminal-type "$terminal_type"
  --check-origin
  --writable
  --client-option "titleFixed=$title"
)

if [[ -n "$username" || -n "$password" ]]; then
  if [[ -z "$username" || -z "$password" ]]; then
    echo "Set both TERMINAL_USERNAME and TERMINAL_PASSWORD, or neither." >&2
    exit 1
  fi
  ttyd_args+=(--credential "$username:$password")
fi

exec "$ttyd_bin" "${ttyd_args[@]}" "$shell_bin" -l
