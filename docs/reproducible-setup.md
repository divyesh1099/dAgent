# Reproducible Setup

This repo is meant to be cloneable. The committed files describe the system;
local generated files hold secrets, tokens, machine-specific paths, databases,
and runtime logs.

## Fresh Clone

From a new checkout:

```bash
git clone <repo-url> dAgent
cd dAgent
scripts/bootstrap_dagent.sh
```

That command creates or updates:

- `.venv/` with the worker package installed.
- `worker/config.yml` from `worker/config.example.yml`.
- `worker/config.chatgpt.yml` from `worker/config.chatgpt.example.yml`.
- `~/.config/dagent/workers/main.env`.
- `~/.config/dagent/workers/chatgpt.env`.
- `~/.config/systemd/user/dagent-worker@.service`.
- `docker/automation-stack/.env`.
- n8n/Postgres/Redis Docker containers.
- the checked-in n8n router workflow.
- a user startup service for the workers and n8n stack.

The bootstrap is idempotent: it does not overwrite existing local config files
or worker env files.

## Public Hostname

For a local-only setup, the default `localhost` values are fine.

For Cloudflare/n8n on a public hostname:

```bash
N8N_PUBLIC_HOSTNAME=n8n.your-domain.example scripts/bootstrap_dagent.sh
```

The script writes the n8n URLs into `docker/automation-stack/.env`. If you add
Cloudflare Access service tokens later, store them without editing workflows:

```bash
scripts/n8nctl access-env '<client-id>' '<client-secret>'
```

## What Is Committed

- Docker Compose stack: `docker/automation-stack/compose.yml`.
- Docker env template: `docker/automation-stack/.env.example`.
- Worker config templates:
  - `worker/config.example.yml`
  - `worker/config.chatgpt.example.yml`
- systemd template: `services/systemd/dagent-worker@.service`.
- n8n workflow export:
  - `docker/automation-stack/n8n/workflows/router-webhook.json`
- setup and operations scripts:
  - `scripts/bootstrap_dagent.sh`
  - `scripts/dagentctl`
  - `scripts/n8nctl`
  - `scripts/deploy_n8n.sh`
  - `scripts/setup_n8n_watch_env.sh`

## What Is Local Only

These files are intentionally ignored by git:

- `worker/config.yml`
- `worker/config.chatgpt.yml`
- `docker/automation-stack/.env`
- `.data/`
- `.venv/`
- `~/.config/dagent/workers/*.env`

They contain paths, tokens, generated secrets, databases, logs, job records, and
local preferences.

## Re-import n8n Workflow

After editing `docker/automation-stack/n8n/workflows/router-webhook.json`:

```bash
scripts/n8nctl import-workflows
```

This copies the workflow export into the n8n container, imports it, and
publishes/activates the workflow id stored in the JSON.

## Reconfigure Worker Tokens in n8n

When worker env files are regenerated:

```bash
scripts/n8nctl watch-env main
scripts/n8nctl restart
```

This stores the main and ChatGPT worker tokens in `docker/automation-stack/.env`
as:

- `DAGENT_WORKER_API_TOKEN`
- `DAGENT_CHATGPT_WORKER_API_TOKEN`
- `DAGENT_SHORTCUT_SECRET`

The n8n workflow reads those values from environment variables, so the workflow
export itself stays secret-free.

## Apple Shortcut Values

After bootstrap:

```bash
grep '^DAGENT_SHORTCUT_SECRET=' docker/automation-stack/.env
```

Use that value for the Shortcut header:

```text
X-Dagent-Shortcut-Secret: <value>
```

The webhook URL is:

```text
https://<your-n8n-host>/webhook/dagent-watch-capture
```

For a local-only n8n test, use:

```text
http://127.0.0.1:5678/webhook/dagent-watch-capture
```

## Health Checks

```bash
scripts/dagentctl overview
scripts/n8nctl health
scripts/test_n8n_watch_webhook.sh http://127.0.0.1:5678/webhook/dagent-watch-capture
```
