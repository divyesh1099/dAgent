# n8n Workflows

Keep exported workflows here after you build them in the n8n UI.

Recommended exports:

- `router-webhook.json`
- `approval-webhook.json`
- `nightly-healthcheck.json`
- `research-aggregator.json`

Before committing exported workflows, remove secrets and credentials.

The main checked-in workflow is `router-webhook.json`. It should use n8n
environment variables such as `DAGENT_WORKER_URL`,
`DAGENT_WORKER_API_TOKEN`, `DAGENT_CHATGPT_WORKER_URL`,
`DAGENT_CHATGPT_WORKER_API_TOKEN`, and `DAGENT_SHORTCUT_SECRET`.

Import it into a running local n8n stack with:

```bash
scripts/n8nctl import-workflows
```
