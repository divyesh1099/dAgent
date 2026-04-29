# Security Model

## Non-Negotiable Rule

Do not expose raw command execution through public webhooks.

The public edge receives intent. The private worker decides whether that intent is allowed.

## Trust Boundaries

```text
Internet
  -> Cloudflare
  -> n8n public webhook
  -> n8n internal workflow
  -> worker on workstation localhost/LAN
  -> local shell/tools/repos
```

The worker should not be publicly exposed. If it must be reachable remotely, put it behind Cloudflare Access or a private tunnel and keep bearer/HMAC auth enabled.

## Authentication Layers

Use separate secrets for separate hops:

- Apple Shortcut to n8n: shared webhook secret or header token.
- n8n UI: Cloudflare Access plus n8n auth.
- Public n8n production webhooks: narrow Cloudflare Access Bypass only for the
  required `/webhook/...` paths, plus `X-Dagent-Shortcut-Secret`.
- n8n to worker: `Authorization: Bearer <worker-token>`.
- Optional n8n to worker HMAC: `X-Dagent-Timestamp` and `X-Dagent-Signature`.
- ntfy: private topics and auth tokens.

## Approval Policy

Default to approval-required for anything that can:

- edit code
- commit/push/deploy
- delete or overwrite files
- spend money
- submit forms
- publish/send messages
- access private accounts through browser automation

The worker supports approval-required jobs. n8n can forward approval links/actions through ntfy.

## Repository Allowlist

Every repo gets a short name:

```yaml
repos:
  dagent:
    path: /home/you/projects/dAgent
    github_account: personal
    allowed_intents:
      - repo_status
      - codex_task
      - claude_task
```

The incoming request uses `repo: "dagent"`, never an arbitrary filesystem path.

## Tool Allowlist

Every tool command is configured locally:

```yaml
tools:
  codex:
    command:
      - codex
      - exec
      - "{task}"
```

The worker runs commands as argument arrays, not shell strings.

## Recommended Exposure

- Expose `n8n.yourdomain.com` through Cloudflare Tunnel.
- Protect n8n editor UI with Cloudflare Access.
- Do not put production webhook paths behind Cloudflare Access unless every
  caller can send valid Access service-token headers.
- Keep production webhook paths random and secret.
- Keep the worker bound to `127.0.0.1` or private LAN.
- Do not expose Docker socket, SSH agent, browser profiles, or repo roots to n8n directly.

## Dangerous Workflow Pattern

Use a two-step flow:

```text
request
  -> validate
  -> create approval-required job
  -> ntfy approval
  -> approve
  -> execute
  -> notify result
```

This is slower than fully autonomous execution, but it is much less likely to ruin a day.
