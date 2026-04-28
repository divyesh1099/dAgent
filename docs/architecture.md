# Architecture

## Goal

The goal is not to make the phone or watch powerful. The goal is to make them reliable capture and approval devices for your workstation.

The workstation remains the execution host because it has:

- local repos and Git credentials
- Docker
- GPU and local tools
- browser sessions if needed
- Codex, Claude Code, Aider, scripts, and other developer tooling

## System Shape

```text
input devices
  Apple Watch, iPhone, Android, laptop

ingress
  Cloudflare Tunnel -> n8n webhook

orchestration
  n8n router workflow
  n8n worker queue for durable workflow execution

execution
  local worker API on the workstation
  allowlisted repos, scripts, agent tools, Docker jobs

feedback
  ntfy notifications
  n8n execution logs
  worker SQLite job database and log files
  Grafana later
```

## Why n8n Plus Worker

n8n is good at receiving webhooks, validating payloads, branching workflows, retrying API calls, and sending notifications. It should not be the thing that blindly runs terminal commands.

The worker is the small trusted execution boundary. It receives a normalized job request from n8n, checks it against local config, asks for approval when required, and then launches the configured tool in the configured repo.

## Execution Modes

### Safe Commands

These can run without approval once the webhook is authenticated:

- save idea/note
- check repo status
- check service health
- produce a dry-run plan
- inspect logs

### Approval Commands

These should require approval:

- modify a repo
- run Codex or Claude Code with write permissions
- run scripts that can deploy, delete, publish, or spend money
- run Docker jobs that change state
- use browser automation against real accounts

### Manual Final-Step Commands

Some workflows should stop before the final irreversible action:

- job applications
- social posting
- purchases
- production deploys
- deleting data
- sending email to external people

## Parallel Processing

Use parallelism at the orchestration layer first:

- n8n splits one request into independent research subtasks.
- Each subtask becomes a worker job.
- The aggregator workflow waits for results and writes one final report.

Use multi-agent chats only when they add value. For reliability, prefer independent jobs with clear contracts over agents debating endlessly.

## First Deployment

Keep everything on the workstation:

- n8n/Postgres/Redis in Docker Compose
- worker on the host
- Cloudflare Tunnel routes to n8n only
- ntfy already handles phone/watch notifications

Only add a VPS later if you need a tiny always-on public broker while the workstation is offline.

