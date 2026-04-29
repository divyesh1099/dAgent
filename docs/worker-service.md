# Worker Service

Use `scripts/dagentctl` to run the worker as a boot-starting service.

For dAgent, a host systemd service is the simplest reliable choice. The worker needs access to your local repos, SSH keys, CLIs, Docker, GPU tools, and eventually browser sessions. A container is useful later for isolated specialist workers, but it adds friction for the first workstation worker.

## First Worker

Create and start the default worker:

```bash
scripts/dagentctl up main 8765
```

Enable boot startup for user services:

```bash
scripts/dagentctl boot
```

If your system asks for sudo, run the printed `sudo loginctl enable-linger ...` command once.

If you also want n8n, the n8n queue worker, and the optional Cloudflare tunnel to
come back with the host worker, run:

```bash
scripts/n8nctl startup
```

That installer makes the automation stack wait for the configured worker before
starting the Docker services.

## Check It

```bash
scripts/dagentctl status main
scripts/dagentctl health main
scripts/dagentctl jobs main
scripts/dagentctl logs main
```

The token n8n needs:

```bash
scripts/dagentctl token main
```

The worker URL for n8n inside Docker remains:

```text
http://host.docker.internal:8765
```

The worker URL from the host is:

```text
http://127.0.0.1:8765
```

## List Workers

```bash
scripts/dagentctl list
```

## Add Another Worker

Most of the time, keep one worker and add capabilities to `worker/config.yml`.

If you want a second isolated worker instance later:

```bash
scripts/dagentctl init research 8766
scripts/dagentctl enable research
scripts/dagentctl start research
scripts/dagentctl health research
```

Then point n8n to:

```text
http://host.docker.internal:8766
```

Each worker gets its own env file:

```text
~/.config/dagent/workers/main.env
~/.config/dagent/workers/research.env
```

Each worker can use a different `DAGENT_WORKER_CONFIG`, port, token, and data directory.

## Doctor

Run:

```bash
scripts/dagentctl doctor
```

It checks common service prerequisites and tells you the next command to run.
