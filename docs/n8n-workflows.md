# n8n Workflows

## Router Workflow

The first n8n workflow should be boring:

```text
Webhook
  -> validate shortcut secret
  -> normalize fields and add idempotency for task requests
  -> call worker /v1/shortcut
  -> respond with project options, project add result, or job id/status
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

## Code Task Smoke Test

After the worker and code-server are running, send a dry-run coding request
through the public n8n webhook:

```bash
scripts/test_n8n_code_task_webhook.sh \
  https://n8n.divyeshvishwakarma.com/webhook/dagent-watch-capture \
  dagent
```

Expected response:

```json
{
  "status": "approval_required",
  "intent": "code_task"
}
```

Approve the returned job from the dashboard or worker API. The completed job
should include a `code_server_url` on `https://vscode.divyeshvishwakarma.com`.

## Webhook Contract

Project list:

```json
{
  "intent": "list_projects",
  "scan": true,
  "include_new": true
}
```

The response includes `options`, which is the array to feed into Apple
Shortcuts `Choose from List`, plus the full `projects` objects.

Project add:

```json
{
  "intent": "add_project",
  "name": "new-app",
  "create_if_missing": true
}
```

If the project already exists as a git repo under `trusted_roots`, dAgent
registers it. With `create_if_missing: true`, dAgent creates an empty git repo
under the first trusted root.

Task requests:

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
POST http://host.docker.internal:8765/v1/shortcut
Authorization: Bearer <DAGENT_WORKER_API_TOKEN>
Content-Type: application/json
```

For Watch captures and code tasks, let n8n generate the worker
`Idempotency-Key`. Do not reuse plain execution IDs like `n8n-13`, because n8n
counters can reset while the worker DB keeps older keys. Project list/add
requests do not require idempotency.

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
