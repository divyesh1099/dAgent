# Code Worker

The code worker turns a watch or API request into an isolated project worktree,
runs the selected coding agent, then returns the worktree path, branch, changed
files, final agent message, DONE note, and code-server URL.

## Local Services

- Worker: `dagent-worker@main.service`
- code-server: `code-server-dagent.service`
- code-server local URL: `http://127.0.0.1:8766`
- code-server private URL: `https://vscode.divyeshvishwakarma.com`
- code-server config: `~/.config/code-server/config.yaml`
- Worktrees: `.data/code-worktrees`

The local deployment starts code-server against the full
`/home/divyesh-nandlal-vishwakarma/Desktop/Divyesh` folder. The worker scans that
folder for git repositories, so new projects under Divyesh are valid without
adding each one to `repos`.

## Project APIs

```text
GET /v1/projects/options?scan=true
POST /v1/projects
POST /v1/shortcut
```

`/v1/projects/options` returns an `options` array designed for Apple Shortcuts
`Choose from List`, plus a full `projects` array with names, paths, sources, and
approval state. `POST /v1/projects` registers an existing git repo under a
trusted root, or creates an empty git repo when `create_if_missing` is true.

The n8n Watch router should call `/v1/shortcut`, which accepts:

```json
{ "intent": "list_projects", "scan": true, "include_new": true }
```

```json
{ "intent": "add_project", "name": "new-app", "create_if_missing": true }
```

Task requests such as `code_task` continue to return the normal worker job
response.

## Request Shape

```json
{
  "intent": "code_task",
  "repo": "dagent",
  "task": "Add the requested feature and run the relevant checks.",
  "metadata": {
    "flavor": "codex"
  }
}
```

`repo` can be any discovered git repo name under the trusted Divyesh root. For
edge cases, pass `metadata.project_path` with an absolute path under the trusted
root.

## Codex Mode

The built-in Codex command uses the generated worktree as the execution folder
and writes Codex's final response to `.dagent/<job-id>-summary.md`.

This workstation currently uses:

```yaml
code:
  codex_sandbox: danger-full-access
  codex_approval_policy: never
```

That is intentional for this deployment: Codex's `workspace-write` Linux sandbox
could not create a bubblewrap namespace from the user service, while the worker
already gates each task with approval and creates a dedicated git worktree per
job. Keep code-server and the worker protected behind Cloudflare Access before
exposing this outside localhost.

## Cloudflare Private Link

Use the separate protected hostname `vscode.divyeshvishwakarma.com`, routed by
the existing Cloudflare Tunnel to `http://127.0.0.1:8766`.

In Cloudflare Zero Trust:

- Create a Self-hosted Access application for the code-server hostname.
- Add an Allow policy for only your email or your private group.
- Do not add a Bypass policy to the code-server hostname.
- Keep public share links on their own hostname or a more specific path rule.
- Keep private share links under an Access-protected hostname/path with an Allow
  policy.

After that, update `worker/config.yml`:

```yaml
code:
  code_server_url: "https://vscode.divyeshvishwakarma.com"
  code_server_folder_url_template: "https://vscode.divyeshvishwakarma.com/?folder={folder}"
```
