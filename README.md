# dAgent

dAgent is a workstation control plane for "tell it while walking, let the workstation do it" automation.

The first version is intentionally boring and reliable:

```text
Apple Watch / phone / laptop
        -> Cloudflare Tunnel
        -> n8n webhook router
        -> allowlisted local worker
        -> Codex / Claude Code / scripts / Docker / docs
        -> ntfy + logs + later Grafana
```

The rule that keeps this safe: public webhooks submit intent, not raw commands.

Good:

```json
{
  "intent": "codex_task",
  "repo": "dagent",
  "task": "fix the failing worker tests"
}
```

Not allowed:

```json
{
  "command": "rm -rf ..."
}
```

## What This Repo Contains

- `docker/automation-stack/`: n8n, Postgres, Redis, and optional Cloudflare Tunnel Compose stack.
- `worker/`: a small FastAPI service that executes only allowlisted jobs.
- `docs/`: architecture, security model, Apple Shortcut contract, n8n flow, GitHub account handling, and command matrix.
- `examples/`: payloads and workflow notes you can copy into n8n/Shortcuts.
- `scripts/`: local helper scripts for secrets and smoke tests.

## Quick Start

For a fresh clone that should recreate the full local setup:

```bash
scripts/bootstrap_dagent.sh
```

Use a public n8n hostname when needed:

```bash
N8N_PUBLIC_HOSTNAME=n8n.your-domain.example scripts/bootstrap_dagent.sh
```

The bootstrap creates both workers (`main` and `chatgpt`), generates local
secrets, starts the Docker stack, imports the checked-in n8n router workflow,
and installs startup services. It keeps secrets in ignored local files.

Manual setup is still available:

1. Install/start the default worker as a service:

```bash
scripts/dagentctl up main 8765
scripts/dagentctl up chatgpt 8767
scripts/dagentctl boot
```

2. Get the worker token for n8n:

```bash
scripts/dagentctl token main
scripts/dagentctl token chatgpt
```

3. Create automation secrets for n8n/Shortcuts:

```bash
./scripts/generate_secrets.sh
```

4. Start n8n/Postgres/Redis:

```bash
scripts/deploy_n8n.sh
scripts/n8nctl startup
scripts/n8nctl import-workflows
```

5. Configure the worker:

```bash
cp worker/config.example.yml worker/config.yml
```

Edit `worker/config.yml` and add your real repo paths, GitHub account labels, and tool commands.

You can still run the worker manually for development:

```bash
python -m venv .venv
. .venv/bin/activate
pip install -e worker
export DAGENT_WORKER_API_TOKEN="replace-with-generated-token"
export DAGENT_WORKER_CONFIG="$PWD/worker/config.yml"
uvicorn dagent_worker.main:app --host 127.0.0.1 --port 8765
```

6. Smoke test:

```bash
./scripts/smoke_test_worker.sh
scripts/n8nctl public
```

From n8n running inside Docker, call the host worker at:

```text
http://host.docker.internal:8765
```

The Compose file includes the Linux `host-gateway` mapping for that name.

## Reliability Defaults

- n8n runs as its own Compose project with Postgres persistence.
- Redis is included so n8n can run in queue mode with workers.
- `scripts/n8nctl startup` installs a user service that starts the host worker before n8n.
- `scripts/n8nctl up` starts `cloudflared` too when `CLOUDFLARE_TUNNEL_TOKEN` is set.
- Cloudflare Access should protect the n8n editor root, while production
  `/webhook/*` paths should use a narrow Access Bypass plus the dAgent shared secret.
- The local worker has a persistent SQLite job log.
- Every repo and tool is named in `worker/config.yml`.
- High-risk intents require an approval step.
- ntfy is optional but supported for status and approval notifications.
- The worker binds to `127.0.0.1` by default. Cloudflare should expose n8n, not the raw worker.

## Suggested First Workflows

Start with five commands:

- `Capture idea`: save a dictated idea to a markdown inbox.
- `Repo status`: return branch, git status, and latest log for a configured repo.
- `Codex task`: create an approval-required coding job in a configured repo.
- `Claude task`: same pattern for Claude Code.
- `Research note`: create a structured document request for a later research workflow.

Then add more specialized flows once the basics are boring and dependable.

## Docs

- [Architecture](docs/architecture.md)
- [Security Model](docs/security-model.md)
- [Apple Shortcuts](docs/apple-shortcuts.md)
- [n8n Workflows](docs/n8n-workflows.md)
- [Command Matrix](docs/command-matrix.md)
- [GitHub Accounts](docs/github-accounts.md)
- [Operations](docs/operations.md)
- [Worker Service](docs/worker-service.md)
- [Reproducible Setup](docs/reproducible-setup.md)
- [dLogs Integration](docs/dlogs-integration.md)
- [References](docs/references.md)
