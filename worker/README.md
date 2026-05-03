# dAgent Worker

The worker is the trusted execution boundary for dAgent.

n8n calls this service with normalized job requests. The worker checks:

- Is the intent known?
- Is the repo in the allowlist?
- Is the tool/script in the allowlist?
- Does this job require approval?

Only after those checks does it run a local command.

## Run Locally

```bash
python -m venv .venv
. .venv/bin/activate
pip install -e worker

cp worker/config.example.yml worker/config.yml
export DAGENT_WORKER_API_TOKEN="replace-with-generated-token"
export DAGENT_WORKER_CONFIG="$PWD/worker/config.yml"

uvicorn dagent_worker.main:app --host 127.0.0.1 --port 8765
```

## Endpoints

- `GET /ui`: worker dashboard for runs, logs, retries, edits, and ntfy resend.
- `GET /health`: unauthenticated liveness check.
- `GET /ready`: authenticated config/ready check.
- `GET /v1/projects`: list configured and registered projects.
- `GET /v1/projects/options`: list project names for Apple Shortcuts.
- `POST /v1/projects`: register or create a project under `trusted_roots`.
- `POST /v1/shortcut`: dispatch Shortcut requests for project list/add or jobs.
- `POST /v1/jobs`: create a job.
- `GET /v1/jobs`: list recent jobs.
- `GET /v1/jobs/{job_id}`: inspect one job.
- `GET /v1/jobs/{job_id}/log`: read the latest job log text.
- `GET /v1/jobs/{job_id}/note`: read note output when the job produced one.
- `POST /v1/jobs/{job_id}/cancel`: cancel a queued/running job.
- `POST /v1/jobs/{job_id}/retry`: rerun the same payload as a fresh job.
- `POST /v1/jobs/{job_id}/requeue`: edit fields and queue a fresh job.
- `POST /v1/jobs/{job_id}/notify`: resend the job result through ntfy.
- `DELETE /v1/jobs/{job_id}`: delete a terminal job record and its worker log.
- `GET /v1/dashboard`: aggregate local configured workers for the dashboard.
- `POST /v1/jobs/{job_id}/approval`: approve or reject an approval-required job.

## Auth

Set:

```bash
export DAGENT_WORKER_API_TOKEN="long-random-token"
```

Every non-health request needs:

```text
Authorization: Bearer <token>
```

Optional HMAC validation:

```bash
export DAGENT_WORKER_HMAC_SECRET="long-random-secret"
```

When enabled, requests with bodies must include:

```text
X-Dagent-Timestamp: <unix seconds>
X-Dagent-Signature: sha256=<hmac_sha256(timestamp + "." + raw_body)>
```
