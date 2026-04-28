# Operations

## Daily Start

```bash
scripts/n8nctl up
scripts/dagentctl up main 8765
```

For boot startup:

```bash
scripts/n8nctl startup
scripts/dagentctl boot
```

The Docker Compose services use `restart: unless-stopped`, so Docker will restart
existing containers after the Docker daemon starts. `scripts/n8nctl startup`
adds a small user-level systemd guard that also runs `docker compose up -d`
after startup/login.

For startup status:

```bash
systemctl --user status dagent-automation-stack.service
scripts/n8nctl ps
```

For worker status/logs:

```bash
scripts/dagentctl list
scripts/dagentctl status main
scripts/dagentctl logs main
scripts/dagentctl health main
```

## Health Checks

n8n:

```bash
curl http://127.0.0.1:5678/healthz
scripts/n8nctl public
```

worker:

```bash
scripts/dagentctl health main
```

worker ready endpoint:

```bash
curl -H "Authorization: Bearer $DAGENT_WORKER_API_TOKEN" http://127.0.0.1:8765/ready
```

## Logs

- n8n execution logs: n8n UI and Postgres.
- worker job DB: `.data/worker/jobs.sqlite3`.
- worker job logs: `.data/worker/logs/`.
- Docker service logs:

```bash
docker compose --env-file docker/automation-stack/.env -f docker/automation-stack/compose.yml logs -f
```

## Backups

Back up:

- `docker/automation-stack/.env`
- n8n Postgres volume
- `worker/config.yml`
- `.data/worker/`
- any notes/research folders the worker writes to

Do not put secrets into Git.

## Upgrade Rule

Pin versions when the stack is stable. Upgrade one layer at a time:

1. n8n image
2. worker code
3. agent CLIs
4. Cloudflare tunnel
5. ntfy

Run smoke tests after each upgrade.
