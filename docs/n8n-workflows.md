# n8n Workflows

## Router Workflow

The first n8n workflow should be boring:

```text
Webhook
  -> validate shortcut secret
  -> normalize fields
  -> switch on intent
  -> call worker /v1/jobs
  -> respond immediately with job id/status
  -> send ntfy status
```

## First Watch Workflow

Configure n8n's local worker/watch secrets:

```bash
scripts/n8nctl watch-env
```

This adds these values to `docker/automation-stack/.env` and recreates n8n:

- `DAGENT_WORKER_API_TOKEN`
- `DAGENT_SHORTCUT_SECRET`

Use `DAGENT_SHORTCUT_SECRET` in the Apple Shortcut header:

```bash
grep '^DAGENT_SHORTCUT_SECRET=' docker/automation-stack/.env
```

## Webhook Contract

Required:

```json
{
  "intent": "repo_status",
  "task": "check status"
}
```

Common optional fields:

```json
{
  "repo": "dagent",
  "tool": "codex",
  "source": "apple_watch",
  "input_type": "voice",
  "require_approval": true,
  "metadata": {
    "priority_reason": "walking idea"
  }
}
```

## Worker Request

n8n calls:

```text
POST http://host.docker.internal:8765/v1/jobs
Authorization: Bearer <DAGENT_WORKER_API_TOKEN>
Content-Type: application/json
```

For Watch captures, let n8n generate the worker `Idempotency-Key`. Do not reuse
plain execution IDs like `n8n-13`, because n8n counters can reset while the
worker DB keeps older keys.

## Approval Workflow

```text
approval ntfy action
  -> n8n approval webhook
  -> call worker /v1/jobs/{job_id}/approval
  -> notify result
```

Approval request:

```json
{
  "decision": "approve",
  "approval_code": "code-from-worker-response"
}
```

## Error Handling

For reliability:

- n8n validates the incoming secret before doing anything else.
- n8n responds quickly with a job id instead of waiting for long agent tasks.
- worker calls use n8n retry-on-fail for transient errors.
- long-running work happens in the worker, not inside the webhook response.
- every job has an idempotency key when possible.

## Queue Mode

Use n8n queue mode once the router works in regular mode. Queue mode lets the main instance handle webhooks while worker instances execute jobs through Redis.

This repo's Compose file starts n8n with queue mode by default because Postgres and Redis are included.

If a workflow needs to persist large binary files inside n8n queue mode, use external object storage instead of filesystem binary storage. For v0, pass file references or cloud links to the worker rather than piping large binary uploads through the watch webhook.
