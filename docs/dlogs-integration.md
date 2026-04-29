# dLogs Integration

dAgent can use your existing dLogs stack without mixing the two repos.

This integration uses:

- dLogs ntfy at `http://127.0.0.1:8080` for worker notifications.
- dLogs Prometheus at `http://127.0.0.1:9090` to scrape dAgent `/metrics`.
- dLogs Grafana at `http://127.0.0.1:3000` to display the dAgent dashboard.

This repo does not edit `/home/divyesh-nandlal-vishwakarma/Desktop/Divyesh/dLogs`.

## Current dAgent Worker Metrics

The worker exposes unauthenticated Prometheus metrics at:

```text
http://127.0.0.1:8765/metrics
```

From dLogs Prometheus running in Docker, the same worker is reachable at:

```text
http://host.docker.internal:8765/metrics
```

Check locally:

```bash
scripts/dagentctl metrics main
```

## ntfy Test

The worker is configured to publish to local dLogs ntfy:

```yaml
notifications:
  ntfy_url: "http://127.0.0.1:8080"
  ntfy_topics:
    - <your-dagent-topic>
  ntfy_token: ""
```

Subscribe to the topic in the ntfy app using your public ntfy server:

```text
https://ntfy.divyeshvishwakarma.com/<your-dagent-topic>
```

Get the actual topic from your ignored local config:

```bash
grep -n 'ntfy_topics' -A3 worker/config.yml
```

Then test:

```bash
scripts/test_ntfy.sh
```

## dLogs Smoke Test

```bash
scripts/dlogs_smoke_test.sh
```

This checks:

- dAgent worker health
- dAgent metrics
- dLogs Prometheus health
- dLogs Grafana health
- dLogs ntfy health
- one ntfy publish

## Prometheus Target

dAgent can generate a target file for all configured workers:

```bash
scripts/dagentctl dlogs-targets
```

For the current worker it will look like:

```text
[
  {
    "targets": ["host.docker.internal:8765"],
    "labels": {
      "source": "host",
      "app": "dagent",
      "worker": "main"
    }
  }
]
```

dLogs already uses `file_sd_configs` for the `host-machine` scrape job. To attach dAgent to the existing scrape job, copy or merge that JSON into the dLogs runtime file:

```text
/home/divyesh-nandlal-vishwakarma/Desktop/Divyesh/dLogs/.dlogs-state/prometheus/host-machine.json
```

I am not applying that change automatically because you asked not to change dLogs from here.

The easier managed command is:

```bash
sed -i 's/^DAGENT_WORKER_HOST=.*/DAGENT_WORKER_HOST=0.0.0.0/' ~/.config/dagent/workers/main.env
scripts/dagentctl restart main
scripts/dlogs_register_prometheus_target.py --apply
docker restart dlogs-prometheus
```

This command creates a backup of the dLogs target file before writing. It probes candidate addresses from inside the `dlogs-prometheus` container and chooses a reachable one.

## Grafana Dashboard

dAgent includes a dashboard JSON:

```text
monitoring/dlogs/grafana/dagent-worker-dashboard.json
```

Import it in Grafana:

```text
Grafana -> Dashboards -> New -> Import -> Upload JSON file
```

It expects the dLogs Prometheus datasource UID:

```text
dlogs-prometheus
```

You can also validate/import through the API:

```bash
scripts/dlogs_import_dashboard.py
scripts/dlogs_import_dashboard.py --apply
```

The script defaults to Grafana at `http://127.0.0.1:3000` with `admin:admin`. Override with:

```bash
scripts/dlogs_import_dashboard.py --apply --user admin --password '<password>'
```

## Useful Commands

```bash
scripts/dagentctl overview
scripts/dagentctl list
scripts/dagentctl health main
scripts/dagentctl metrics main
scripts/dagentctl jobs main 20
scripts/dagentctl logs main
```
