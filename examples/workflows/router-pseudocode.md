# Router Workflow Pseudocode

```text
Webhook /dagent/<random>
  Read body
  If header X-Dagent-Shortcut-Secret != expected secret:
    Respond 401

  Normalize:
    source = body.source || "unknown"
    input_type = body.input_type || "text"
    intent = body.intent
    task = body.task
    repo = body.repo || null

  If intent is missing:
    Respond 400

  HTTP Request:
    POST ${DAGENT_WORKER_URL}/v1/shortcut
    Authorization: Bearer ${DAGENT_WORKER_API_TOKEN}
    Idempotency-Key: n8n execution id or shortcut run id for task requests
    JSON normalized body

  If worker says approval_required:
    Send ntfy approval notification

  Respond with:
    job_id, status, message
```
