# First Watch Workflow

Goal:

```text
Apple Watch / iPhone Shortcut
  -> Cloudflare Access
  -> n8n webhook
  -> dAgent worker /v1/jobs
  -> note in .data/notes + ntfy notification
```

## 1. Configure Local n8n Secrets

```bash
cd /home/divyesh-nandlal-vishwakarma/Desktop/Divyesh/dAgent
scripts/n8nctl watch-env
```

This stores these values in `docker/automation-stack/.env` and recreates n8n:

- `DAGENT_WORKER_API_TOKEN`
- `DAGENT_SHORTCUT_SECRET`
- `N8N_BLOCK_ENV_ACCESS_IN_NODE=false`

Show the shortcut secret when needed:

```bash
grep '^DAGENT_SHORTCUT_SECRET=' docker/automation-stack/.env
```

## 2. Create Cloudflare Service Token

In Cloudflare One:

```text
Access controls
  -> Service credentials
  -> Service Tokens
  -> Create Service Token
```

Name:

```text
apple-watch-dagent
```

Copy both values immediately:

```text
CF-Access-Client-Id
CF-Access-Client-Secret
```

Then edit the `n8n` Access application and add a policy:

```text
Action: Service Auth
Include: Service Token -> apple-watch-dagent
```

Keep your existing email login policy too, so the browser editor still works.

## 3. Create n8n Workflow

Open:

```text
https://n8n.divyeshvishwakarma.com
```

Create a workflow named:

```text
dAgent Watch - Capture Idea
```

### Webhook Node

Add `Webhook`.

```text
HTTP Method: POST
Path: dagent-watch-capture
Authentication: Header Auth
Respond: When Last Node Finishes
Response Data: First Entry JSON
```

Create the Header Auth credential:

```text
Name: dAgent Shortcut Secret
Header Name: X-Dagent-Shortcut-Secret
Header Value: value from DAGENT_SHORTCUT_SECRET
```

### HTTP Request Node

Connect `Webhook` -> `HTTP Request`.

```text
Method: POST
URL: ={{ $env.DAGENT_WORKER_URL }}/v1/jobs
Send Headers: on
Send Body: on
Body Content Type: JSON
Specify Body: Using Fields Below
```

Headers:

```text
Authorization: ={{ 'Bearer ' + $env.DAGENT_WORKER_API_TOKEN }}
Content-Type: application/json
Idempotency-Key: ={{ $json.body.idempotency_key || ('n8n-' + $execution.id) }}
```

Keep the `Authorization` value on one line. A trailing newline in this field
will make Node reject the HTTP header with `Invalid character in header content`.

If the editor shows `[access to env vars denied]`, make sure n8n has been
recreated with:

```text
N8N_BLOCK_ENV_ACCESS_IN_NODE=false
```

The helper command sets this:

```bash
scripts/n8nctl watch-env
```

Body fields:

```text
source: apple_watch
intent: capture_idea
task: ={{ $json.body.task || $json.body.text || $json.body.idea }}
input_type: voice
idempotency_key: ={{ $json.body.idempotency_key || ('n8n-' + $execution.id) }}
```

Save the workflow.

## 4. Test From Terminal

For the test URL:

1. Open the Webhook node.
2. Click `Listen for test event`.
3. Copy the `Test URL`.

Then run:

```bash
export CF_ACCESS_CLIENT_ID='paste-cloudflare-client-id'
export CF_ACCESS_CLIENT_SECRET='paste-cloudflare-client-secret'

scripts/test_n8n_watch_webhook.sh \
  'https://n8n.divyeshvishwakarma.com/webhook-test/dagent-watch-capture' \
  'First capture idea through n8n.'
```

For production:

1. Toggle the workflow `Active`.
2. Use the production URL:

```bash
scripts/test_n8n_watch_webhook.sh \
  'https://n8n.divyeshvishwakarma.com/webhook/dagent-watch-capture' \
  'First production capture idea through n8n.'
```

## 5. Verify Result

```bash
ls -lt .data/notes | head
scripts/dagentctl jobs main 5
```

You should also receive an ntfy notification if dAgent notifications are configured.
