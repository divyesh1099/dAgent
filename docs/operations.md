# Operations

## Daily Start

```bash
scripts/n8nctl up
scripts/dagentctl health main
```

For boot startup, use the combined startup installer:

```bash
scripts/n8nctl startup
```

That command installs/enables the host worker service, starts the `main` worker,
installs the automation-stack user service, enables lingering, and starts the
Compose stack. If `CLOUDFLARE_TUNNEL_TOKEN` is set to a real tunnel token in
`docker/automation-stack/.env`, `scripts/n8nctl up` also starts `cloudflared`.

The Docker Compose services use `restart: unless-stopped`, so Docker will restart
existing containers after the Docker daemon starts. The systemd guard retries if
Docker is still starting and waits for the worker before starting n8n.

If startup says Docker is running but this user cannot access
`/var/run/docker.sock`, add the user to the Docker group once, then log out and
back in:

```bash
sudo usermod -aG docker "$USER"
```

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

## Cloudflare Access 403

This response means Cloudflare Access blocked the request before n8n saw it:

```text
Forbidden. You don't have permission to view this.
```

For dAgent, keep the n8n editor root protected by Cloudflare Access, but bypass
Cloudflare Access for the production webhook path. The webhook is still protected
by `X-Dagent-Shortcut-Secret` before n8n calls the worker.

In Cloudflare Zero Trust:

```text
Access > Applications > Add application > Self-hosted
Hostname: n8n.divyeshvishwakarma.com
Path: /webhook/*
Policy action: Bypass
Include: Everyone
```

Then verify:

```bash
scripts/n8nctl public
```

Expected result:

```text
OK: Cloudflare Access appears to be protecting n8n.divyeshvishwakarma.com.
OK: Cloudflare Access is not blocking /webhook/dagent-watch-capture.
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
