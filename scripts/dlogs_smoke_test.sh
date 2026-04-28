#!/usr/bin/env bash
set -euo pipefail

cd "$(dirname "$0")/.."

WORKER="${1:-main}"
GRAFANA_URL="${GRAFANA_URL:-http://127.0.0.1:3000}"
PROMETHEUS_URL="${PROMETHEUS_URL:-http://127.0.0.1:9090}"
NTFY_URL="${NTFY_URL:-http://127.0.0.1:8080}"

echo "dAgent worker health"
scripts/dagentctl health "$WORKER"
echo

echo "dAgent worker metrics"
scripts/dagentctl metrics "$WORKER" | sed -n '1,40p'
echo

echo "dLogs Prometheus health"
curl -fsS "$PROMETHEUS_URL/-/healthy"
echo

echo "dAgent scrape in Prometheus"
PROM_RESULT="$(curl -fsS --get --data-urlencode 'query=dagent_worker_up' "$PROMETHEUS_URL/api/v1/query")"
echo "$PROM_RESULT"
if [[ "$PROM_RESULT" == *'"result":[]'* ]]; then
  echo "note: Prometheus is healthy but is not scraping dAgent yet."
  echo "      Use: scripts/dagentctl dlogs-targets"
fi
echo

echo "dLogs Grafana health"
curl -fsS "$GRAFANA_URL/api/health"
echo

echo "dLogs ntfy health"
curl -fsS "$NTFY_URL/v1/health"
echo

echo "ntfy publish test"
scripts/test_ntfy.sh "dAgent can publish to dLogs ntfy."
