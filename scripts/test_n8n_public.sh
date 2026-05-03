#!/usr/bin/env bash
set -euo pipefail

ENV_FILE="docker/automation-stack/.env"

get_env() {
  local key="$1"
  if [[ ! -f "$ENV_FILE" ]]; then
    return 0
  fi
  awk -F= -v key="$key" '$1 == key { sub(/^[^=]*=/, ""); print; exit }' "$ENV_FILE"
}

HOST="${1:-$(get_env N8N_HOST)}"
HOST="${HOST:-localhost}"
LOCAL_URL="http://127.0.0.1:5678"
if [[ "$HOST" == "localhost" || "$HOST" == "127.0.0.1" ]]; then
  PUBLIC_URL="http://$HOST:5678"
else
  PUBLIC_URL="https://$HOST"
fi
WEBHOOK_PATH="${DAGENT_PUBLIC_WEBHOOK_PATH:-/webhook/dagent-watch-capture}"

has_access_token() {
  [[ -n "$cf_access_client_id" && -n "$cf_access_client_secret" && "$cf_access_client_id" != "replace-with-cloudflare-access-client-id" && "$cf_access_client_secret" != "replace-with-cloudflare-access-client-secret" ]]
}

is_cloudflare_access_response() {
  local headers_file="$1"
  grep -qiE 'cloudflareaccess|Cloudflare-Access|/cdn-cgi/access/login|cf-access-' "$headers_file"
}

cf_access_client_id="${CF_ACCESS_CLIENT_ID:-$(get_env CF_ACCESS_CLIENT_ID)}"
cf_access_client_secret="${CF_ACCESS_CLIENT_SECRET:-$(get_env CF_ACCESS_CLIENT_SECRET)}"

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
echo "Public HTTPS without Cloudflare Access service token"
curl_args=(-k -sS -I --max-time 20)
if ! getent hosts "$HOST" >/dev/null 2>&1 && [[ -n "$public_ips" ]]; then
  echo "Using --resolve with $public_ips because the local resolver is stale."
  curl_args=(-k -sS -I --max-time 20 --resolve "$HOST:443:$public_ips")
fi

if curl "${curl_args[@]}" "$PUBLIC_URL" >/tmp/dagent-n8n-public-headers.txt; then
  sed -n '1,30p' /tmp/dagent-n8n-public-headers.txt
else
  echo "FAILED: $PUBLIC_URL is not reachable."
  exit 1
fi

echo
if is_cloudflare_access_response /tmp/dagent-n8n-public-headers.txt; then
  echo "OK: Cloudflare Access appears to be protecting $HOST."
elif grep -qi '^HTTP/.* 200' /tmp/dagent-n8n-public-headers.txt; then
  echo "WARNING: $HOST returned raw HTTP 200. The n8n editor may be public."
  echo "Fix Cloudflare Access so the app covers the hostname root path / and not only a subpath."
else
  echo "Check the headers above. Expected either Cloudflare Access login/redirect or a protected response."
fi

if ! has_access_token; then
  echo
  echo "Cloudflare Access service token is not stored locally; skipping authenticated editor check."
  echo "This is OK when the editor root is protected and /webhook/* uses a narrow Bypass policy."
else
  echo
  echo "Public HTTPS with Cloudflare Access service token"
  if curl "${curl_args[@]}" \
    -H "CF-Access-Client-Id: $cf_access_client_id" \
    -H "CF-Access-Client-Secret: $cf_access_client_secret" \
    "$PUBLIC_URL" >/tmp/dagent-n8n-public-auth-headers.txt; then
    sed -n '1,30p' /tmp/dagent-n8n-public-auth-headers.txt
  else
    echo "FAILED: $PUBLIC_URL is not reachable with the stored Access service token."
    exit 1
  fi

  echo
  if is_cloudflare_access_response /tmp/dagent-n8n-public-auth-headers.txt && grep -qi '^HTTP/.* 403' /tmp/dagent-n8n-public-auth-headers.txt; then
    echo "FAILED: Cloudflare Access rejected the stored service token."
    echo "Check that the n8n Access app has a Service Auth policy for this token."
    exit 1
  fi

  echo "OK: Cloudflare Access accepted the stored service token."
fi

echo
echo "Public webhook path without Cloudflare Access service token"
if curl "${curl_args[@]}" "$PUBLIC_URL$WEBHOOK_PATH" >/tmp/dagent-n8n-public-webhook-headers.txt; then
  sed -n '1,30p' /tmp/dagent-n8n-public-webhook-headers.txt
else
  echo "FAILED: $PUBLIC_URL$WEBHOOK_PATH is not reachable."
  exit 1
fi

echo
if is_cloudflare_access_response /tmp/dagent-n8n-public-webhook-headers.txt; then
  echo "FAILED: Cloudflare Access is still protecting $WEBHOOK_PATH."
  echo
  echo "Permanent fix in Cloudflare Zero Trust:"
  echo "  1. Access > Applications > Add application > Self-hosted."
  echo "  2. Hostname: $HOST"
  echo "  3. Path: /webhook/*"
  echo "  4. Policy action: Bypass"
  echo "  5. Include: Everyone"
  echo
  echo "Keep the root $HOST Access app protected for the n8n editor."
  echo "The dAgent webhook is still protected by X-Dagent-Shortcut-Secret."
  exit 1
fi

if grep -qi '^HTTP/.* 404' /tmp/dagent-n8n-public-webhook-headers.txt; then
  echo "Note: this check uses HEAD against a POST-only n8n webhook, so n8n may return 404 here."
fi
echo "OK: Cloudflare Access is not blocking $WEBHOOK_PATH."
