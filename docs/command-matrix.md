# Command Matrix

Use this as the first allowlist. Add commands slowly.

| Command | Intent | Device | Executes On | Approval | Output |
| --- | --- | --- | --- | --- | --- |
| Capture idea | `capture_idea` | Watch/phone | Worker | No | Markdown note + ntfy |
| Start research note | `research_note` | Watch/phone | Worker/n8n | No for note, yes for web automation | Markdown/PDF/doc link |
| Repo status | `repo_status` | Watch/phone/laptop | Worker | No | Branch, dirty files, latest commit |
| Code task | `code_task` | Watch/phone/laptop | Worker | Yes | Isolated worktree, branch, code-server link, diff summary |
| Codex task | `codex_task` | Watch/phone/laptop | Worker | Yes | Job log, patch summary |
| Claude task | `claude_task` | Watch/phone/laptop | Worker | Yes | Job log, patch summary |
| Run tests | `script_task` | Phone/laptop | Worker | Usually no for read-only tests | Test log |
| Docker restart | `script_task` | Phone/laptop | Worker | Yes | Service status |
| LinkedIn packet | `job_packet` | Phone/laptop | Worker/n8n | No until browser/apply step | Resume/cover letter |
| LinkedIn prefill | `browser_task` | Laptop/desktop | Workstation browser | Yes | Stop before submit |
| Deploy | `script_task` | Phone/laptop | Worker | Yes | Deployment log |

## Auto-Approve Candidates

- read-only git status
- writing a new note under an inbox folder
- summarizing provided text
- checking service health
- running non-mutating tests in a known repo

## Approval-Required Candidates

- write-enabled coding agents
- git commit/push
- production deploy
- browser automation against logged-in accounts
- posting/sending/submitting
- deleting files
- Docker commands that restart or remove services

## Device Strategy

- Apple Watch: quick intent capture and approval.
- iPhone/Android: richer text, image, file, and share sheet input.
- Laptop: complex prompts, review, and manual final actions.
- Workstation: execution, local tools, GPU, repos, Docker, browser profiles.
