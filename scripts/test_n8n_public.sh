#!/usr/bin/env bash
set -euo pipefail

HOST="${1:-n8n.divyeshvishwakarma.com}"
LOCAL_URL="http://127.0.0.1:5678"
PUBLIC_URL="https://$HOST"

echo "Local n8n"
if curl -fsS -I "$LOCAL_URL" >/tmp/dagent-n8n-local-headers.txt; then
  sed -n '1,12p' /tmp/dagent-n8n-local-headers.txt
else
  echo "FAILED: $LOCAL_URL is not reachable."
fi

echo
echo "Public DNS"
public_ips=""
if command -v dig >/dev/null 2>&1; then
  echo "@1.1.1.1:"
  dig +short @1.1.1.1 "$HOST" || true
  public_ips="$(dig +short @1.1.1.1 "$HOST" | awk '/^[0-9.]+$/ { print; exit }')"
fi

echo "system resolver:"
if getent hosts "$HOST"; then
  :
else
  echo "No answer from local system resolver yet."
fi

router_dns="$(resolvectl dns 2>/dev/null | awk '
  /DNS Servers:/ && $3 ~ /^[0-9.]+$/ { print $3; exit }
  /Link [0-9]+/ && $NF ~ /^[0-9.]+$/ { print $NF; exit }
')"
if [[ -n "$router_dns" && "$router_dns" != "1.1.1.1" && "$router_dns" != "8.8.8.8" ]]; then
  router_answer=""
  if command -v dig >/dev/null 2>&1; then
    router_answer="$(dig +time=3 +tries=1 +short @"$router_dns" "$HOST" 2>/dev/null || true)"
  fi
  if [[ -z "$router_answer" && -n "$public_ips" ]]; then
    echo
    echo "Resolver note:"
    echo "  Your current DNS server ($router_dns) did not resolve $HOST,"
    echo "  but public DNS did. Use Cloudflare/Google DNS on this PC:"
    echo "    sudo nmcli connection modify \"netplan-enp6s0\" ipv4.ignore-auto-dns yes ipv4.dns \"1.1.1.1 8.8.8.8\""
    echo "    sudo nmcli connection up \"netplan-enp6s0\""
    echo "    sudo resolvectl flush-caches"
  fi
fi

echo
echo "Public HTTPS"
curl_args=(-k -sS -I --max-time 20 "$PUBLIC_URL")
if ! getent hosts "$HOST" >/dev/null 2>&1 && [[ -n "$public_ips" ]]; then
  echo "Using --resolve with $public_ips because the local resolver is stale."
  curl_args=(-k -sS -I --max-time 20 --resolve "$HOST:443:$public_ips" "$PUBLIC_URL")
fi

if curl "${curl_args[@]}" >/tmp/dagent-n8n-public-headers.txt; then
  sed -n '1,30p' /tmp/dagent-n8n-public-headers.txt
else
  echo "FAILED: $PUBLIC_URL is not reachable."
  exit 1
fi

echo
if grep -qiE 'cloudflareaccess|Cloudflare-Access|/cdn-cgi/access/login' /tmp/dagent-n8n-public-headers.txt; then
  echo "OK: Cloudflare Access appears to be protecting $HOST."
elif grep -qi '^HTTP/.* 200' /tmp/dagent-n8n-public-headers.txt; then
  echo "WARNING: $HOST returned raw HTTP 200. The n8n editor may be public."
  echo "Fix Cloudflare Access so the app covers the hostname root path / and not only a subpath."
else
  echo "Check the headers above. Expected either Cloudflare Access login/redirect or a protected response."
fi
